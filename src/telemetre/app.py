"""FastAPI app: SSE live stream + REST controls + static web UI.

Single process (one serial port, one shared State). The serial reader thread is
started/stopped by the lifespan handler.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import REPO_ROOT, load_config
from .osc_out import OscSender
from .serial_reader import SerialReader
from .state import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("telemetre")

cfg = load_config()
state = State(cfg)
osc = OscSender(cfg.osc)
reader = SerialReader(cfg, state, osc)

WEB_DIR = REPO_ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    reader.start()
    log.info("Telemetre Bergman up on %s:%d (osc=%s)", cfg.web.host, cfg.web.port, osc.active)
    try:
        yield
    finally:
        reader.stop()


app = FastAPI(title="Telemetre Bergman", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return state.health()


@app.post("/api/tare")
async def tare():
    state.set_zero()
    return {"ok": True, "zero_cm": state.zero_cm}


@app.post("/api/clear_zero")
async def clear_zero():
    state.clear_zero()
    return {"ok": True}


@app.post("/api/invert")
async def invert():
    state.toggle_sign()
    return {"ok": True, "sign": state.sign}


def _run_power(cmd: list[str]):
    # `systemctl poweroff|reboot` queues the transition with PID 1 and returns
    # promptly, so we can capture a fast failure (e.g. sudo denied) instead of
    # reporting a false success while the Pi keeps running.
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return {"ok": True}  # still running after 5s: shutdown is under way
    except Exception as e:
        log.error("Power command failed to launch: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip() or f"exit {p.returncode}"
        log.error("Power command failed: %s", err)
        return JSONResponse({"ok": False, "error": err}, status_code=500)
    return {"ok": True}


@app.post("/api/poweroff")
async def poweroff():
    return _run_power(["sudo", "-n", "/usr/bin/systemctl", "poweroff"])


@app.post("/api/reboot")
async def reboot():
    return _run_power(["sudo", "-n", "/usr/bin/systemctl", "reboot"])


@app.get("/stream")
async def stream(request: Request):
    period = 1.0 / max(1, cfg.filter.publish_hz)

    async def gen():
        last_beat = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(state.snapshot())}\n\n"
            now = time.monotonic()
            if now - last_beat > 15:  # keepalive comment for proxies/dead conns
                last_beat = now
                yield ": ping\n\n"
            await asyncio.sleep(period)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# Static SPA at root, served with revalidation so UI updates (e.g. the French
# strings) land on the next refresh instead of Safari serving a stale cached
# bundle. `no-cache` still allows 304s via etag, so it stays cheap. Mounted last
# so /api/* and /stream take precedence. (Subclass, not middleware — middleware
# would wrap and risk buffering the /stream SSE response.)
class _RevalidatingStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", _RevalidatingStatic(directory=str(WEB_DIR), html=True), name="web")
