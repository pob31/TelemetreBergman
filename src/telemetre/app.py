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
    try:
        subprocess.Popen(cmd)  # fire-and-forget so the HTTP response still flushes
        return {"ok": True}
    except Exception as e:
        log.error("Power command failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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


# Static SPA at root. Mounted last so /api/* and /stream take precedence.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
