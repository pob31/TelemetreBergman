# PRD — Cadreur Bergman (as-built)

**Millumin scrim tracker: keeps projected video fitted to the travelling scrim.**

Status: **implemented and commissioned on stage** (schema v2) · Runs on the show Mac with
**Millumin V5** · Companion to the Telemetre Bergman Pi app (this repo).

This document describes the app **as shipped**. It supersedes the original Looks-based
design; if you find an older description elsewhere, this file is authoritative. It is
self-contained: a developer taking over should be able to work from this file plus the code.

---

## 1. Purpose

On the Bergman stage a scrim travels slowly upstage (~4 cm/min) while a **front** and a
**rear** beamer project video onto it, often at the same time. As the scrim recedes, the
projected image drifts and changes apparent size. **Cadreur** reads the scrim distance from
the Telemetre Pi and continuously adjusts the **scale, horizontal and vertical position** of
the mapped Millumin layers so each picture stays fitted.

```
 Pi 5 + TF02-Pro ── SSE GET /stream ──▶  Cadreur (show Mac)  ── OSC/UDP :5000 ──▶  Millumin V5
 (telemetre app)   20 Hz JSON, abs      Python/FastAPI          send-only            front + rear
 192.168.0.51      distance             browser + native win                        beamers
        │                               (:8080 / cadreur-gui)                            │
   measures scrim                              │                                  travelling scrim
   cart distance                        video manager                             (~4 cm/min)
```

The operator (the show's video manager) calibrates **by driving from Cadreur**: at a given
scrim position they set each layer's scale/H/V with on-screen sliders (which drive Millumin
live), then **Capture** the values. Cadreur interpolates between captured points from the
live distance and streams absolute values to Millumin.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **abs_m** | Absolute sensor→scrim distance in meters, tare-independent (§4). All calibration is keyed on it. |
| **Beamer** | `front` or `rear` videoprojector. |
| **Channel** | One Millumin layer target on a beamer. Each beamer has a flat list of channels (4 + 4 by default), all driven **continuously and simultaneously**. The operator picks a "mode" (scope, people, …) by **layer visibility in Millumin**, not by switching in Cadreur. |
| **Axis** | One of three normalised **0.0–1.0** values per channel: `scale`, `horizontal`, `vertical`. 0.5 = centred. Millumin's Interaction transformer maps each 0–1 to pixels/scale — Cadreur never deals in pixels. |
| **Lens memory** | A front-projector lens preset (zoom/shift), switched on the projector. Each front channel is calibrated **per lens memory** (global selector). The rear has no lens memories (single set per channel). |
| **Calibration point** | `{distance_m, scale, pos_x, pos_y}` captured at one scrim position (see §5 for the naming). |
| **Trim** | Live post-interpolation correction per calibration set (scale multiplies, H/V add). |
| **Armed** | Master switch. Only when armed does Cadreur drive layers from the live distance. Calibrate mode drives regardless of arm. Never persisted — always starts disarmed. |

## 3. Architecture & data flow

Single Python process (FastAPI + uvicorn). Started headless with `python -m cadreur`, or in
a native macOS window with `cadreur-gui` / `Cadreur.app` (same server inside a WKWebView).

- **`telemetre_client.py`** — daemon thread: connects to the Pi SSE, reconstructs abs_m,
  runs smoothing stages 1–2 (median-3 + τ-EMA), writes the smoothed distance into shared state.
  Reconnects with 1→5 s backoff; a silent TCP path is cut after ~20 s.
- **`state.py`** — the single source of truth (one lock). Holds the smoothed distance, the
  show document, runtime controls (armed, per-channel calibrate set, per-channel manual
  drive values) and the engine's per-channel output. Produces the UI snapshot.
- **`engine.py`** — asyncio task, **20 Hz tick**: for every channel of every beamer, gate →
  interpolate → slew → send policy → OSC. Writes each channel's runtime status back to state.
- **`millumin.py`** — OSC out (python-osc `SimpleUDPClient`). `send_value(address, value)`
  sends one float. Feedback listener exists but is **off by default** (custom addresses do
  not answer `/?`).
- **`app.py`** — FastAPI: the app's own SSE `/stream` (10 Hz UI snapshot), REST controls,
  static UI. **`show.py`** = schema/load/save/migration. **`interp.py` / `smoothing.py`** =
  pure logic. **`config.py`** = machine config. **`gui.py`** = the pywebview wrapper.

## 4. Telemeter input

Source: `GET {telemetre.url}/stream` — Server-Sent Events, ~20 Hz, one JSON object per
`data:` line (see `src/telemetre/state.py`):

```json
{"position_m": 1.234, "raw_m": 3.210, "strength": 240, "temp_c": 31.0,
 "connected": true, "port": "/dev/ttySC1", "stale": false,
 "zero_cm": 197.0, "sign": -1, "units": "m"}
```

- **abs_m reconstruction** (tare-proof; `sign ∈ {-1,+1}`, so `sign² = 1`):

  ```
  abs_m = position_m * sign + zero_cm / 100.0
  ```

  Immune to Set Zero / Clear Zero / Invert on the Pi, so the stage manager can re-tare
  mid-production without invalidating a calibration. `raw_m` is unfiltered — never used.
- A payload is **usable** iff `connected && !stale && position_m != null`.
- Smoothing on arrival: `median-of-3 → τ-EMA` with the measured dt; on a gap (first payload
  or stale recovery) a nominal 20 Hz dt is used so the EMA absorbs rather than snaps.
- Mac-side staleness: also treat the source stale after `telemetre.stale_after_ms` without a
  usable event (covers a dead TCP path the Pi can't flag). On stale/disconnect the smoothed
  value **holds** (never reset).

## 5. Data model — the show file (JSON, schema v2)

One JSON file = one show. Everything the operator edits, including smoothing (a venue
property, so it travels with the show). Machine/network settings do **not** live here (§12).

```json
{
  "app": "cadreur",
  "version": 2,
  "meta": { "name": "Bergman 2026", "saved_at": "2026-07-22T12:00:00Z", "notes": "…" },
  "settings": { "active_lens_memory": "M1" },
  "lens_memories": ["M1", "M2", "M3"],
  "smoothing": {
    "ema_tau_s": 5.0, "deadband_scale": 0.0005, "slew_scale_per_s": 0.05, "refresh_hz": 1.0
  },
  "beamers": {
    "front": {
      "channels": [
        {
          "id": "front-1",
          "name": "Scope",
          "enabled": true,
          "osc_scale": "/front/scale/1",
          "osc_posv": "/front/positionV/1",
          "osc_posh": "/front/positionH/1",
          "calibrations": {
            "M1": {
              "interp": "linear",
              "trim": { "scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0 },
              "points": [
                { "distance_m": 0.45, "scale": 0.704, "pos_x": 0.5, "pos_y": 0.663 },
                { "distance_m": 9.95, "scale": 0.923, "pos_x": 0.5, "pos_y": 0.596 }
              ]
            }
          }
        }
        /* front-2 … front-4, same shape, empty calibrations */
      ]
    },
    "rear": { "channels": [ /* rear-1 … rear-4, calibrations keyed "default" */ ] }
  }
}
```

Rules (in `show.py`, tested in `test_show.py`):

- Beamers are exactly `front` / `rear`. Each has a **list of channels** (4 + 4 by default;
  add/delete allowed, never below 1 per beamer). Channel `id` is unique within a beamer.
- **Front** channels resolve their calibration set via `settings.active_lens_memory`
  (global). **Rear** channels use the reserved key `"default"` — one uniform code path.
- Active lens memory with **no set** for a channel → that channel is *inhibited* (nothing
  sent; status "uncalibrated"). Never fall back to another memory's set.
- `distance_m` is **abs_m**. Points are kept sorted by distance; two points within **1 mm**
  keep the later one (a re-capture overwrites); a capture within **0.03 m** replaces.
- `trim` is per calibration set, applied post-interpolation. **Armed is never persisted**;
  `enabled` per channel *is*.
- `interp` is `"linear"` in v1/v2; the field is reserved so a future mode can arrive without
  a schema bump.
- **Versioning / migration**: integer `version`. The loader refuses a missing or *greater*
  version. A **v1 (Looks) file auto-migrates**: the active look's front/rear beamer becomes
  channel 1 (calibration + OSC addresses preserved), channels 2..N are filled fresh, Looks
  are dropped. Unknown keys are ignored on load; save writes only the known schema.

### Internal naming note (important for maintainers)

The three axes are stored under legacy names that a future reader must not misread:

| Concept (UI/OSC) | Internal point/trim key | Range |
|---|---|---|
| scale | `scale` / trim `scale_mul` (multiplies) | 0–1 |
| **horizontal** | **`pos_x`** / trim **`dx_px`** (adds) | 0–1 |
| **vertical** | **`pos_y`** / trim **`dy_px`** (adds) | 0–1 |

`pos_x/pos_y/dx_px/dy_px` are historical names from a pixel-based draft; they now carry
**normalised 0–1** horizontal/vertical values. Do not reintroduce pixel semantics.

## 6. OSC output (§ millumin.py)

Send-only, UDP to `millumin.host:millumin.port` (default `127.0.0.1:5000`). Per channel,
each 20 Hz decision sends up to three floats to the channel's own addresses:

```
<osc_scale>  <f 0..1>     e.g. /front/scale/1
<osc_posh>   <f 0..1>     e.g. /front/positionH/1   (horizontal)
<osc_posv>   <f 0..1>     e.g. /front/positionV/1   (vertical)
```

- Millumin maps each 0–1 to pixels/scale via the **Interaction transformer** the operator
  sets up (e.g. positionV 0→−1200 px, 0.5→centre, 1→+1200 px). Choose the pixel span per
  axis in Millumin — a **smaller** range for horizontal gives finer centring.
- Defaults are `/front|retro/{scale,positionV,positionH,layer}/{1..4}`; **editable per
  channel** from the UI (OSC… button) or the show file. Validated against `^/[A-Za-z0-9._:/-]+$`.
- **`osc_show`** (`/front|retro/layer/N`) is a **one-shot pure-path trigger**, not part of
  the 20 Hz tick: the per-channel **Show** button posts to `…/show`, which sends the bare
  address **with no OSC argument** so the operator can reveal the layer being calibrated
  **from the stage**, without returning to the booth.
- Absolute values + periodic refresh (§8) self-heal a Millumin restart or column change.
- UDP gives **no error** for an unknown address — if a layer doesn't move, the Interaction
  isn't learned or the address is wrong (verify in Millumin). Feedback/readback is off by
  default; `test_millumin` reports "send-only".

## 7. Interpolation (§ interp.py)

Per axis (scale, pos_x, pos_y) independently, over the channel's sorted points:

- **N = 0** → channel inhibited (uncalibrated). **N = 1** → constant hold. **N ≥ 2** →
  piecewise-linear between neighbours; **clamp** to the end values outside the range (status
  shows "clamped low/high").
- Trim applied after interpolation: `scale *= scale_mul; pos_x += dx_px; pos_y += dy_px`.
  `bake` folds trim into every point and resets it.
- Rounding before send: all three axes → 4 dp (below the dead-band).
- The stage-measured mapping is **near-linear** (mid point ≈ 1.6 % of range off the endpoint
  chord for scale, 1.7 % for vertical), so 2 endpoint points are almost enough and a 3rd
  mid point makes it essentially exact. No non-linear fit mode is needed; add a point where
  the curve visibly bends (`scripts/analyze_points.py` measures this from the live points).

## 8. Smoothing (§ smoothing.py)

At 4 cm/min (0.67 mm/s) seconds of lag are invisible, so smoothing is generous.

| Stage | Where | Default (show key) | Purpose |
|---|---|---|---|
| Median-of-3 | client thread | fixed | SSE hiccup insurance |
| τ-EMA `alpha = dt/(tau+dt)` | client thread | `ema_tau_s = 5.0` | kills sub-Hz truss pendulum (~16× at 0.5 Hz), ~3.3 mm ramp lag |
| Slew limiter (per axis) | engine tick | `slew_scale_per_s = 0.05` | turns any discontinuity (point edit, memory switch, stale recovery) into a ~1–2 s glide |
| Send dead-band + refresh | engine tick | `deadband_scale = 0.0005`, `refresh_hz = 1.0` | send when any axis moved ≥ dead-band OR the refresh period elapsed (absolute → self-healing) |

All three axes are 0–1, so one dead-band and one slew rate govern all of them (4 operator
knobs total, in the Advanced drawer). On **Arm**, outputs snap to target (setup act);
otherwise everything glides. In **calibrate** mode the engine keeps a channel's slews
seeded on its manual value so leaving calibrate glides into interpolated playback.

## 9. Runtime state machine (§ engine.py)

- **Master Arm** — `DISARMED` (startup, and after any show load/import) ⇄ `ARMED`. Disarmed
  ⇒ **no OSC**, and disarming sends no "return to neutral" — layers stay put.
- **Calibrate drives regardless of arm**: a channel in calibrate mode sends its manual
  values live even when disarmed (you calibrate before arming). Several channels may be in
  calibrate at once.
- **Per-channel gate** (playback) reasons, in order: `calibrating` → `disarmed` → `disabled`
  → `uncalibrated` (no set for the active memory) → `no_points` → `no_distance` → gated open.
- **Distance stale/disconnected** → hold the last smoothed value, keep refresh-cadence sends
  (absolute + periodic self-heals), UI banner "distance stale — holding".
- Engine tick = 20 Hz: median/EMA smoothed abs_m in, per-channel decisions out; channel
  runtime keyed `"{beamer}/{cid}"` (stale keys pruned when channels are removed).

## 10. Calibration workflow — "drive from Cadreur"

Because custom Interaction addresses don't answer `/?` readback, Cadreur **is** the
controller during calibration (no Millumin readback):

1. Optionally pick the front **lens memory** being calibrated (chips on the FACE column).
2. Toggle **Mode calibration** on the channel(s) you want to set — the three sliders
   (échelle / horizontal / vertical, 0–1) now **drive that layer live** in Millumin.
   Several channels can be in calibrate at once.
3. At the current scrim position, set each channel's sliders so its frame fits.
4. **Capture** the channel (stores `{abs_m, scale, H, V}`), or **Capturer tous** to capture
   *every* calibrating channel at the current distance in one go.
5. **Move the scrim** to the next position and repeat (typically far → middle → near). This
   is the intended flow: fit *all* layers at one position, capture, then move — you never
   walk the scrim back and forth per channel.
6. Leave calibrate mode → the channel glides into interpolated playback.

Two aids for setting up **from the stage** (tablet in hand, handling the curtain): each
channel's **Show** button reveals that layer in Millumin (`osc_show`, a pure-path trigger),
and the **Precision** toggle next to the sliders (in calibrate mode) makes them 10× finer.
Channel cards **collapse** so you can fold away the layers you're not working on.

Points table per channel: inline edit, per-row re-capture (current distance + current manual
values) and delete. Trim nudges: scale ±0.01/±0.001, H/V ±0.001/±0.0001 (10× finer), plus
bake / reset. Capture is disabled while the distance is stale.

## 11. UI (§ web/)

Single dark page, tablet-friendly, bilingual FR/EN (JSON i18n block + `data-i18n`, `?lang=`
remembered in `localStorage["cadreur_lang"]`, English fills gaps). Served in the browser at
`:8080` and, identically, inside the native window.

- **Header**: Pi status + live abs_m, Millumin status (send-only), master **ARM** toggle.
- **Distance**: abs_m + stage position (crew reference); travel bar with per-beamer point
  ticks and a cart marker; a **Capturer tous** bar appears when any channel is calibrating.
- **Two columns FACE / RÉTRO**, each with its channels as cards cloned from a `<template>`:
  a **collapse** chevron + editable name, enable, OSC… (edit addresses), delete; live status
  + values; calibrate toggle + three drive sliders with a per-card **Precision** toggle (10×
  finer steps, persisted); a **Show** button (reveal the layer in Millumin from the stage);
  capture; points table; trim; **+ canal** to add one. A collapsed card keeps only its
  header row. Lens-memory chips sit on the FACE column (global; hollow = no points).
- **Footer**: show file + autosave dot, Save / Save as / Load / Export / Import, Advanced
  (smoothing) drawer.
- Transport: the app's own SSE `GET /stream` (10 Hz snapshot); controls are `POST /api/…`
  returning `{"ok": true, …}`.

**Snapshot shape** (per SSE event): `{distance:{abs_m, abs_m_raw, position_m, source},
armed, settings:{active_lens_memory}, lens_memories, smoothing, beamers:{front:[chan…],
rear:[chan…]}, show:{name,notes,saved_at,file,dirty,autosave}, millumin:{ok,latency_ms,
warning}}`. Each channel object: `{id, name, enabled, osc_scale, osc_posv, osc_posh,
cal_key, points, trim, calibrating, manual:{scale,pos_v,pos_h}, reason, gate, clamped,
values, sending, n_points}`.

**API endpoints** (all POST unless noted):
`GET /api/health`, `arm`, `lens_memory`,
`beamer/{b}/channel/add`,
`channel/{b}/{cid}/{delete, rename, osc, enable, calibrate, manual, show, capture, points,
trim, trim/bake, trim/reset}`, `capture_all`, `smoothing`, `test_millumin`,
`save`, `save_as`, `load`, `GET shows`, `GET export`, `import`, `meta`, `GET /stream`.

## 12. Config & persistence

**Machine config** — `cadreur.toml` at repo root (from `cadreur.example.toml`; env override
`CADREUR_CONFIG`; dataclass-per-section, unknown keys ignored):

```toml
[telemetre]
url = "http://192.168.0.51"      # cadreur appends /stream
stale_after_ms = 1500

[millumin]
host = "127.0.0.1"
port = 5000                      # Millumin OSC input
feedback = false                 # send-only for custom addresses (default)
feedback_port = 8001             # only if feedback = true (8000 is often taken)
feedback_timeout_ms = 1500

[web]
host = "127.0.0.1"               # 0.0.0.0 to allow a tablet/browser on the LAN
port = 8080

[shows]
dir = "shows"
autosave = true
autosave_debounce_s = 5
```

- **Show JSON** (§5): autosave = dirty flag + debounce + atomic write; on each app start a
  rotating backup `shows/backups/<name>-<stamp>.json` (keep 10) before loading. Explicit
  Save / Save as / Load (server-side `shows/`) and Export/Import (browser).
- **Runtime state** `cadreur_state.json`: remembers the last-opened show path only (an
  *absolute* path — see the README note on moving the folder). Armed is never persisted.
- Git: `shows/` is gitignored except `shows/example-show.json`; `cadreur.toml`,
  `cadreur_state.json`, `.venv/`, `Cadreur.app`, `*.log` are not versioned.

> The UI has no authentication. Default bind is loopback; `0.0.0.0` hands geometry control
> to anyone on the stage LAN — acceptable on that closed network, but a deliberate choice.

## 13. Repo layout

```
src/cadreur/
  __init__.py  __main__.py      # python -m cadreur → uvicorn
  app.py                        # FastAPI: /stream SSE, /api/*, static mount
  config.py                     # cadreur.toml loader
  show.py                       # v2 schema, load/save, v1→v2 migration, channel ops
  state.py                      # lock + snapshot; per-channel calibrate/manual/runtime
  telemetre_client.py           # Pi SSE reader thread + abs_m + median/EMA
  smoothing.py  interp.py       # pure logic
  engine.py                     # 20 Hz tick over all channels
  millumin.py                   # OSC out (+ optional feedback listener)
  gui.py                        # pywebview native-window wrapper (cadreur-gui)
  web/  index.html  app.js  style.css
scripts/  make_app.sh  sim_telemetre.py  millumin_sim.py  osc_test.py  drive_demo.py  analyze_points.py
tests/    test_interp.py  test_smoothing.py  test_show.py  test_engine.py
documentation/PRD-cadreur.md    cadreur.example.toml    shows/example-show.json
```

`pyproject.toml` (shared with the Pi app) exposes `cadreur` and `cadreur-gui` scripts and a
`gui` extra (`pywebview`).

## 14. Run, native app, deploy, backup

- **Headless**: `python3 -m venv .venv && .venv/bin/pip install -e .` then
  `.venv/bin/python -m cadreur` (UI on `http://127.0.0.1:8080`).
- **Native window**: `.venv/bin/pip install -e '.[gui]'` then `.venv/bin/cadreur-gui`.
- **Double-clickable app**: `./scripts/make_app.sh` builds `Cadreur.app` at the repo root
  (keep it there; drag to the Dock). It runs `cadreur-gui`, logs to `cadreur_gui.log`.
- **Backup / moving the folder**: the code is location-independent; only `.venv/` must be
  recreated after a move or on a new machine. What to back up = `shows/*.json` +
  `cadreur.toml`. Full steps: README → "Backup and moving the folder".

## 15. Testing & simulation

- **Unit tests** (`python -m unittest discover -s tests`; ~92, off-hardware): `test_interp`
  (interpolation, clamp, N=0/1, merge, trim, rounding), `test_smoothing` (τ-EMA attenuation,
  slew, dead-band, freeze/resume), `test_show` (v2 round-trip, **v1→v2 migration**, channel
  ops, version refusal, inhibit rules), `test_engine` (fake clock: per-channel gates,
  calibrate-drives-manual, multi-channel calibrate, capture cadence, stale hold, glides).
- **Simulators** (any OS, no rig): `scripts/sim_telemetre.py` (byte-compatible fake Pi SSE
  with speed/pendulum/tare/stale options), `scripts/millumin_sim.py` (prints all OSC).
- **Dev helpers**: `osc_test.py` (send raw values to test a Millumin binding),
  `drive_demo.py` (drive a channel through the engine), `analyze_points.py` (linearity of
  captured points).
- **Millumin bring-up**: enable OSC input on 5000; learn each channel's Interactions
  (`scale/positionH/positionV`, indices 1..4 per beamer) and choose the pixel range per axis
  (smaller for horizontal). Send-only — verify layers actually move.

## 16. Risks & operational notes

- **Address / Interaction drift** — UDP fails silently; if a layer doesn't move, the
  Interaction isn't learned or the channel's address is wrong. Fix via the channel's OSC…
  button or in Millumin. All channels' Interactions must be learned for the indices in use.
- **Wrong lens memory** — Cadreur can't read the projector's state; the selected memory must
  match the physical lens. Uncalibrated memory inhibits (no wrong output).
- **Millumin transformer ranges** — pixel span and centring live in Millumin. Keep them
  symmetric (0.5 = centred) and give horizontal a smaller span for finer centring.
- **Sensor re-rig** — moving the TF02-Pro flybar invalidates every calibration despite
  tare-immunity; `meta.notes` carries a warning; recapture.
- **Moving the folder** — recreate `.venv` and re-drag `Cadreur.app`; `cadreur_state.json`
  holds an absolute show path (delete it or Load once). See the README.

## 17. Out of scope / possible future

- Corner-pin / keystone via `/…/mapping/*` (per-corner calibration) — off-axis beamers see
  keystone that scale+translate can't fully correct; lens memories mitigate it physically.
- Reading the projector's active lens memory (PJLink) to auto-select it.
- OSC-triggered actions from Millumin cues; auth on the web UI; per-axis smoothing.
- A non-linear (1/throw) interpolation mode — not needed at the measured linearity.
