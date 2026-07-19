"""FastAPI app: SSE snapshot stream + REST controls + static web UI.

Single process. The telemetre SSE client thread, the OSC feedback listener and
the 20 Hz engine task are started/stopped by the lifespan handler. All control
endpoints return {"ok": true, ...}; operator-level failures return
{"ok": false, "error": ...} with status 400 (defensive: a bad request must
never crash the app).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import interp
from . import show as showmod
from .config import load_config
from .engine import Engine
from .millumin import MilluminIO
from .state import CadreurState
from .telemetre_client import TelemetreClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cadreur")

cfg = load_config()
state = CadreurState(cfg)
io = MilluminIO(cfg.millumin)
client = TelemetreClient(cfg, state)
engine = Engine(cfg, state, io)

WEB_DIR = Path(__file__).resolve().parent / "web"

SNAPSHOT_HZ = 10
CAPTURE_CHECKLIST = (
    "No reply from Millumin. Check: (a) Millumin is running with this project, "
    "(b) a layer named '{layer}' exists, (c) Device manager → OSC → API feedback "
    "is enabled → 127.0.0.1:{port}. Or enter values manually."
)


def _load_startup_show() -> None:
    last = state.load_last_show_path()
    if not last:
        return
    showmod.startup_backup(last)  # rotating backups before touching the file
    try:
        state.show = showmod.load_show(last)
        state.show_path = last
        log.info("Loaded show %s", last)
    except showmod.ShowError as e:
        log.warning("Could not load last show %s: %s", last, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_startup_show()
    io.start()
    client.start()
    engine_task = asyncio.create_task(engine.run())
    log.info("Cadreur up on %s:%d (millumin %s:%d, telemetre %s)",
             cfg.web.host, cfg.web.port, cfg.millumin.host, cfg.millumin.port,
             cfg.telemetre.url)
    try:
        yield
    finally:
        engine_task.cancel()
        client.stop()
        io.stop()


app = FastAPI(title="Cadreur Bergman", lifespan=lifespan)


def err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


async def body_of(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def active_beamer(b: str) -> tuple[dict, dict]:
    """(active look, beamer dict) or ShowError."""
    if b not in showmod.BEAMER_KEYS:
        raise showmod.ShowError(f"Unknown beamer '{b}'.")
    look = showmod.active_look(state.show)
    if not look or b not in look["beamers"]:
        raise showmod.ShowError(f"No {b} beamer in this look.")
    return look, look["beamers"][b]


@app.get("/api/health")
async def health():
    return state.health()


# --- runtime controls --------------------------------------------------------

@app.post("/api/arm")
async def arm(request: Request):
    state.armed = bool((await body_of(request)).get("armed"))
    return {"ok": True, "armed": state.armed}


@app.post("/api/look")
async def set_look(request: Request):
    lid = str((await body_of(request)).get("id") or "")
    if not showmod.get_look(state.show, lid):
        return err(f"Unknown look '{lid}'.")
    state.show["settings"]["active_look"] = lid
    state.mark_dirty()
    return {"ok": True}


@app.post("/api/looks")
async def looks_op(request: Request):
    d = await body_of(request)
    op = d.get("op")
    try:
        if op == "create":
            lk = showmod.create_look(state.show, str(d.get("name") or "Look"))
            state.show["settings"]["active_look"] = lk["id"]
        elif op == "duplicate":
            lk = showmod.duplicate_look(state.show, str(d.get("id") or ""), d.get("name"))
            state.show["settings"]["active_look"] = lk["id"]
        elif op == "rename":
            showmod.rename_look(state.show, str(d.get("id") or ""), str(d.get("name") or ""))
        elif op == "delete":
            showmod.delete_look(state.show, str(d.get("id") or ""))
        else:
            return err(f"Unknown op '{op}'.")
    except showmod.ShowError as e:
        return err(str(e))
    state.mark_dirty()
    return {"ok": True}


@app.post("/api/lens_memory")
async def set_lens_memory(request: Request):
    mem = str((await body_of(request)).get("id") or "")
    if mem not in state.show["lens_memories"]:
        return err(f"Unknown lens memory '{mem}'.")
    state.show["settings"]["active_lens_memory"] = mem
    state.mark_dirty()
    return {"ok": True}


# --- per-beamer --------------------------------------------------------------

@app.post("/api/beamer/{b}/enable")
async def beamer_enable(b: str, request: Request):
    try:
        _, beamer = active_beamer(b)
    except showmod.ShowError as e:
        return err(str(e))
    beamer["enabled"] = bool((await body_of(request)).get("enabled"))
    state.mark_dirty()
    return {"ok": True}


@app.post("/api/beamer/{b}/layer")
async def beamer_layer(b: str, request: Request):
    name = str((await body_of(request)).get("name") or "")
    if not showmod.valid_layer_name(name):
        return err("Invalid layer name (letters, digits, . _ - only — no spaces).")
    try:
        _, beamer = active_beamer(b)
    except showmod.ShowError as e:
        return err(str(e))
    beamer["layer"] = name
    state.mark_dirty()
    return {"ok": True}


@app.post("/api/beamer/{b}/calibrate")
async def beamer_calibrate(b: str, request: Request):
    if b not in showmod.BEAMER_KEYS:
        return err(f"Unknown beamer '{b}'.")
    on = bool((await body_of(request)).get("on"))
    was_on = state.calibrate[b]
    state.calibrate[b] = on
    if was_on and not on:
        # Re-seed the slew from the layer's actual values (the operator just
        # moved it); if feedback is unavailable, snap (PRD §8).
        try:
            _, beamer = active_beamer(b)
            layer = beamer["layer"]
        except showmod.ShowError:
            layer = None

        async def reseed():
            res = await asyncio.to_thread(io.query, layer) if layer else None
            engine.request_reseed(b, res)

        asyncio.create_task(reseed())
    return {"ok": True, "calibrate": state.calibrate[b]}


@app.post("/api/beamer/{b}/capture")
async def beamer_capture(b: str):
    try:
        look, beamer = active_beamer(b)
    except showmod.ShowError as e:
        return err(str(e))
    if state.source_state() != "live":
        return err("Distance is stale — capture disabled.")
    abs_m, _ = state.distance()
    if abs_m is None:
        return err("No distance received yet.")
    layer = beamer["layer"]
    if not showmod.valid_layer_name(layer):
        return err("Set a valid layer name first.")
    res = await asyncio.to_thread(io.query, layer)
    if res is None:
        state.millumin = {"ok": False, "latency_ms": None, "warning": None}
        return JSONResponse({
            "ok": False, "error": "timeout",
            "checklist": CAPTURE_CHECKLIST.format(layer=layer, port=cfg.millumin.feedback_port),
            "distance_m": round(abs_m, 3),
        }, status_code=504)
    state.millumin = {"ok": True, "latency_ms": res["latency_ms"], "warning": None}
    cset = showmod.ensure_cal_set(state.show, look, b)
    point = {"distance_m": round(abs_m, 3), "scale": res["scale"],
             "pos_x": res["pos_x"], "pos_y": res["pos_y"]}
    cset["points"], replaced = interp.insert_point(cset["points"], point)
    state.mark_dirty()
    return {"ok": True, "point": point, "replaced": replaced,
            "latency_ms": res["latency_ms"]}


@app.post("/api/beamer/{b}/points")
async def beamer_points(b: str, request: Request):
    d = await body_of(request)
    op = d.get("op")
    try:
        look, _ = active_beamer(b)
        cset = showmod.ensure_cal_set(state.show, look, b)
    except showmod.ShowError as e:
        return err(str(e))
    pts = cset["points"]
    try:
        if op == "add":
            p = interp.clean_point(d.get("point") or {})
            if p is None:
                return err("Point needs numeric distance_m, scale, pos_x, pos_y.")
            cset["points"], _ = interp.insert_point(pts, p)
        elif op in ("edit", "delete", "recapture"):
            idx = int(d.get("index", -1))
            if not 0 <= idx < len(pts):
                return err("No such point.")
            if op == "delete":
                del pts[idx]
            elif op == "edit":
                p = interp.clean_point(d.get("point") or {})
                if p is None:
                    return err("Point needs numeric distance_m, scale, pos_x, pos_y.")
                del pts[idx]
                cset["points"], _ = interp.insert_point(pts, p)
            else:  # recapture: current distance + current Millumin values
                if state.source_state() != "live":
                    return err("Distance is stale — capture disabled.")
                abs_m, _ = state.distance()
                _, beamer = active_beamer(b)
                res = await asyncio.to_thread(io.query, beamer["layer"])
                if res is None:
                    return err("No reply from Millumin.", status=504)
                del pts[idx]
                p = {"distance_m": round(abs_m, 3), "scale": res["scale"],
                     "pos_x": res["pos_x"], "pos_y": res["pos_y"]}
                cset["points"], _ = interp.insert_point(pts, p)
        else:
            return err(f"Unknown op '{op}'.")
    except (TypeError, ValueError):
        return err("Bad point payload.")
    state.mark_dirty()
    return {"ok": True, "points": cset["points"]}


@app.post("/api/beamer/{b}/trim")
async def beamer_trim(b: str, request: Request):
    d = await body_of(request)
    try:
        look, _ = active_beamer(b)
        cset = showmod.ensure_cal_set(state.show, look, b)
    except showmod.ShowError as e:
        return err(str(e))
    for k in ("scale_mul", "dx_px", "dy_px"):
        if k in d:
            try:
                cset["trim"][k] = float(d[k])
            except (TypeError, ValueError):
                return err(f"Bad value for {k}.")
    state.mark_dirty()
    return {"ok": True, "trim": cset["trim"]}


@app.post("/api/beamer/{b}/trim/bake")
async def beamer_trim_bake(b: str):
    try:
        look, _ = active_beamer(b)
        cset = showmod.ensure_cal_set(state.show, look, b)
    except showmod.ShowError as e:
        return err(str(e))
    cset["points"] = interp.bake_trim(cset["points"], cset["trim"])
    cset["trim"] = showmod.default_trim()
    state.mark_dirty()
    return {"ok": True, "points": cset["points"]}


@app.post("/api/beamer/{b}/trim/reset")
async def beamer_trim_reset(b: str):
    try:
        look, _ = active_beamer(b)
        cset = showmod.ensure_cal_set(state.show, look, b)
    except showmod.ShowError as e:
        return err(str(e))
    cset["trim"] = showmod.default_trim()
    state.mark_dirty()
    return {"ok": True}


# --- smoothing / millumin ----------------------------------------------------

@app.post("/api/smoothing")
async def set_smoothing(request: Request):
    d = await body_of(request)
    sm = state.show["smoothing"]
    for k, v in d.items():
        if k not in showmod.DEFAULT_SMOOTHING:
            return err(f"Unknown smoothing key '{k}'.")
        try:
            lo, hi = showmod.SMOOTHING_LIMITS[k]
            sm[k] = min(hi, max(lo, float(v)))
        except (TypeError, ValueError):
            return err(f"Bad value for {k}.")
    state.mark_dirty()
    return {"ok": True, "smoothing": sm}


@app.post("/api/test_millumin")
async def test_millumin():
    look = showmod.active_look(state.show)
    layer = None
    if look:
        for b in showmod.BEAMER_KEYS:
            beamer = look["beamers"].get(b)
            if beamer and showmod.valid_layer_name(beamer["layer"]):
                layer = beamer["layer"]
                break
    if not layer:
        return err("No beamer with a valid layer name in this look.")
    res = await asyncio.to_thread(io.query, layer)
    if res is None:
        state.millumin = {"ok": False, "latency_ms": None, "warning": None}
        return JSONResponse({
            "ok": False, "error": "timeout", "layer": layer,
            "checklist": CAPTURE_CHECKLIST.format(layer=layer, port=cfg.millumin.feedback_port),
        }, status_code=504)
    state.millumin = {"ok": True, "latency_ms": res["latency_ms"], "warning": None}
    return {"ok": True, "layer": layer, "latency_ms": res["latency_ms"]}


# --- persistence -------------------------------------------------------------

def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9àâäéèêëîïôöùûüç._ -]", "", name).strip()
    return name or "show"


@app.post("/api/save")
async def save():
    if not state.show_path:
        return err("No file yet — use Save as.")
    try:
        showmod.save_show(state.show_path, state.show)
    except showmod.ShowError as e:
        return err(str(e))
    state.dirty = False
    return {"ok": True, "file": state.show_path.name}


@app.post("/api/save_as")
async def save_as(request: Request):
    name = _sanitize_name(str((await body_of(request)).get("name") or ""))
    path = cfg.shows_dir() / (name if name.endswith(".json") else name + ".json")
    try:
        showmod.save_show(path, state.show)
    except showmod.ShowError as e:
        return err(str(e))
    state.show_path = path
    state.dirty = False
    state.remember_show_path()
    return {"ok": True, "file": path.name}


@app.post("/api/load")
async def load(request: Request):
    name = str((await body_of(request)).get("name") or "")
    path = cfg.shows_dir() / Path(name).name  # no path traversal
    try:
        doc = showmod.load_show(path)
    except showmod.ShowError as e:
        return err(str(e))
    state.armed = False  # DISARMED after any show load/import (PRD §10)
    state.calibrate = {b: False for b in showmod.BEAMER_KEYS}
    state.show = doc
    state.show_path = path
    state.dirty = False
    state.remember_show_path()
    return {"ok": True, "file": path.name}


@app.get("/api/shows")
async def list_shows():
    d = cfg.shows_dir()
    files = sorted(p.name for p in d.glob("*.json")) if d.exists() else []
    return {"ok": True, "shows": files,
            "current": state.show_path.name if state.show_path else None}


@app.get("/api/export")
async def export():
    doc = showmod.normalize(state.show)
    name = (state.show_path.name if state.show_path
            else _sanitize_name(doc["meta"]["name"]) + ".json")
    return Response(
        json.dumps(doc, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/import")
async def import_show(request: Request):
    # The browser reads the file and POSTs its JSON body directly.
    try:
        doc = showmod.normalize(await request.json())
    except showmod.ShowError as e:
        return err(str(e))
    except Exception:
        return err("Not a JSON file.")
    state.armed = False
    state.calibrate = {b: False for b in showmod.BEAMER_KEYS}
    state.show = doc
    state.show_path = None  # imported: operator names it with Save as
    state.mark_dirty()
    return {"ok": True}


@app.post("/api/meta")
async def set_meta(request: Request):
    d = await body_of(request)
    if "name" in d:
        state.show["meta"]["name"] = str(d["name"]) or state.show["meta"]["name"]
    if "notes" in d:
        state.show["meta"]["notes"] = str(d["notes"])
    state.mark_dirty()
    return {"ok": True}


# --- SSE snapshot stream -----------------------------------------------------

@app.get("/stream")
async def stream(request: Request):
    period = 1.0 / SNAPSHOT_HZ

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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# Static SPA at root with revalidation (subclass, not middleware — middleware
# would wrap and risk buffering the /stream SSE response). Mounted last so
# /api/* and /stream take precedence.
class _RevalidatingStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", _RevalidatingStatic(directory=str(WEB_DIR), html=True), name="web")
