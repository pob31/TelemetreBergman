# PRD — Cadreur Bergman

**Millumin scrim tracker: keeps projected video fitted to the travelling scrim.**

Status: approved for implementation · Schema/PRD version 1 · 2026-07-19
Runs on: the show Mac running **Millumin V5** · Companion to the Telemetre Bergman Pi app (this repo)

This document is self-contained. A fresh development session on the Mac must be able to
implement the app from this file alone, without asking anything else.

> **As-built note (2026-07-21).** The app was implemented and commissioned on the show Mac
> with three changes agreed live with the video manager. The sections below still describe
> the original design; where they differ, the as-built behaviour wins:
> 1. **OSC addressing.** The show uses **custom Millumin Interaction addresses**, not the
>    `/layer:NAME/…` API — per beamer `osc_scale` / `osc_posv` / `osc_posh` (defaults
>    `/front|retro/scale|positionV|positionH/1`), stored in the show file and editable.
> 2. **Normalised axes.** scale, **horizontal** (`positionH`) and **vertical** (`positionV`)
>    are all floats **0.0–1.0** (Millumin's transformer maps them; 0.5 = centred). Position
>    is not in pixels. Internally the point/param names stay `scale`/`pos_x`(=H)/`pos_y`(=V).
> 3. **Calibration = "drive from Cadreur".** Custom addresses don't answer `/?` readback, so
>    calibrate mode **drives** live manual values (three sliders) that Capture snapshots —
>    there is no Millumin readback. Feedback listener + armed probe are **off by default**
>    (`[millumin] feedback = false`). The travel mapping measured on stage is near-linear.

---

## 1. Purpose

On the Bergman stage, a scrim travels slowly upstage (~4 cm/min) while a **front** and a
**rear** beamer (possibly both at once) project video onto it. As the scrim recedes, the
projected image drifts and changes apparent size. **Cadreur** reads the scrim position
from the Telemetre Pi and continuously adjusts the **scale and position** of the mapped
Millumin layers so the picture stays fitted: cinemascope frames held at mid-height,
life-size actors keeping their feet on the ground. The *cadreur* is the camera operator
who keeps the subject framed — that is this app's whole job.

```
 Pi 5 + TF02-Pro ──── SSE GET /stream ───▶  cadreur (Mac)  ──── OSC/UDP :5000 ───▶  Millumin V5
 (telemetre app)      20 Hz JSON            Python/FastAPI  ◀─── OSC feedback ────  (same Mac)
        │                                   browser UI :8080     :8000 (queries)        │
   measures scrim                                │                              front + rear beamers
   cart distance                          video manager                                 │
                                                                            travelling scrim (~4 cm/min)
```

The operator (the show's video manager) calibrates by example: park the scrim, fit the
frame with Millumin's own tools, press **Capture point**. Cadreur interpolates between
captured points from the live distance and streams absolute scale/position to Millumin.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **abs_m** | Absolute sensor→scrim distance in meters, tare-independent (§5). All calibration is keyed on it. |
| **Look** | A named projection setup (e.g. "Cinémascope", "Acteurs taille réelle"): per-beamer target layer + calibration data. One look is active at a time. |
| **Beamer** | `front` or `rear` videoprojector. Each maps to one Millumin layer per look. |
| **Lens memory** | A front-projector lens preset (zoom/shift), switched manually on the projector. Each changes geometry → separate calibration per memory. Labels are user-defined (default `M1..M3`). |
| **Calibration set** | The points + trim for one (look, beamer, lens memory). Rear has a single set per look (reserved key `default`). |
| **Calibration point** | `{distance_m, scale, pos_x, pos_y}` — Millumin layer values captured at one scrim position. |
| **Trim** | Live post-interpolation correction (`scale ×`, `x +`, `y +`) per calibration set; can be baked into the points. |
| **Armed** | Master switch. Only when armed does cadreur send OSC. Never persisted — the app always starts disarmed. |

## 3. Key design decisions

| Decision | Why |
|---|---|
| Capture-based calibration (read values back *from* Millumin) | Bakes Millumin's anchor/centering math into the data — no need to model it. Feet-on-ground emerges from interpolated `pos_y`. |
| Key on **abs_m**, not the tared position | The stage manager can press "Set Zero" on the Pi mid-production without invalidating calibrations. |
| Piecewise-linear over N ≥ 2 points, clamp at the ends | Predictable; "add a point where it curves" beats curve-fit UI. Clamping is visually safe; extrapolation can run the image off the scrim. |
| Absolute OSC values + periodic refresh | UDP is lossy and Millumin may restart or change columns; absolute + refresh self-heals within ≤ 1 s. |
| Uncalibrated lens memory ⇒ **inhibit**, never fall back | Another memory's geometry is confidently wrong; no output beats wrong output. |
| Always start disarmed; disarmed = total OSC silence | A show-control app must never move layers before the operator says so. |
| Same repo, sibling package, shared-nothing with `telemetre` | Both machines clone one repo; the Pi service is never destabilized by Mac work. ~40 duplicated lines is the price, gladly paid. |

## 4. Functional requirements

**Distance input**
- FR-1: Subscribe to the Pi's SSE stream; auto-reconnect with backoff; surface connected/stale state.
- FR-2: Reconstruct **abs_m** per §5; ignore `raw_m` (unfiltered).
- FR-3: On stale/disconnect: freeze the smoothed distance, keep refreshing outputs with held values, show a prominent banner.

**Mapping engine**
- FR-4: Per beamer, map smoothed abs_m → (scale, pos_x, pos_y) via the active look's calibration set (§7), apply trim, smooth output (§8), send OSC (§9).
- FR-5: Front resolves its calibration set from the global **active lens memory**; rear uses its single set.
- FR-6: Clamp outside the calibrated range and report "clamped" in the beamer status.

**Calibration**
- FR-7: Per-beamer **Calibrate mode** that suspends that beamer's output while the operator drags in Millumin.
- FR-8: **Capture point**: query Millumin for the layer's current scale + position, store with current smoothed abs_m; sorted insert; replace an existing point within 0.03 m.
- FR-9: Manual fallback: numeric entry form when feedback fails, with a checklist of causes.
- FR-10: Points table: inline edit, per-row re-capture, delete; unlimited points (N ≥ 2 typical, > 3 supported).
- FR-11: Trim nudges applied live; **Bake into points**; **Reset trim**.

**Runtime control**
- FR-12: Master **Arm/Disarm**; per-beamer **enable** toggles; per-beamer output gate per §10.
- FR-13: Look selector (create / duplicate / rename / delete); lens-memory selector (chips) on the front panel; both persisted in the show file.
- FR-14: **Test Millumin** button: feedback round-trip with latency readout.

**Persistence**
- FR-15: One show JSON holds everything the operator edits (§6); autosave (debounced, atomic) + rotating startup backups; explicit Save / Save as / Load; Export/Import through the browser.
- FR-16: Machine-specific settings (addresses, ports) live in `cadreur.toml`, not in the show file (§13).

**UI**
- FR-17: Single-page control surface per §12; both beamer panels always visible; targeted layer name displayed per beamer.
- FR-18: Bilingual EN/FR with the same mechanism as the Pi app (§12).

## 5. Telemeter input

Source: `GET {telemetre.url}/stream` — Server-Sent Events, ~20 Hz, one JSON object per
`data:` line (see `src/telemetre/state.py` in this repo):

```json
{"position_m": 1.234, "raw_m": 3.210, "strength": 240, "temp_c": 31.0,
 "connected": true, "port": "/dev/ttySC1", "stale": false,
 "zero_cm": 197.0, "sign": -1, "units": "m"}
```

- **abs_m reconstruction** (the load-bearing formula; `sign ∈ {-1, +1}`, so `sign² = 1`):

  ```
  abs_m = position_m * sign + zero_cm / 100.0        # null-check position_m first
  ```

  `position_m = (filtered_cm − zero_cm) · sign / 100` on the Pi, hence abs_m is the Pi's
  *filtered* absolute distance — immune to Set Zero / Clear Zero / Invert. Rounding on the
  Pi (3 dp position, 0.1 cm zero) bounds reconstruction error at ~1.5 mm — irrelevant
  beside the Pi's own 0.75 cm display hysteresis.
- A payload is **usable** iff `connected && !stale && position_m != null`.
- Mac-side staleness: additionally treat the source as stale after `stale_after_ms`
  (default 1500) without any SSE event (covers a dead TCP path the Pi can't flag).
- The Pi value arrives as a **staircase** (its 0.75 cm hysteresis ⇒ one step ≈ every 11 s
  at 4 cm/min). The smoothing chain (§8) melts steps into glides; do not "fix" this on
  the Pi.
- Client implementation: daemon thread mirroring `src/telemetre/serial_reader.py`'s
  reconnect pattern (connect → stream → on error mark disconnected, backoff 1→5 s,
  retry). Use stdlib `http.client` with incremental reads — no new dependency, and SSE
  parsing here is 20 lines (split on blank line, take `data:` payloads).

## 6. Data model — the show file

One JSON file = one show. Everything the operator edits, including smoothing (truss
behavior is a venue property, it travels with the show). Machine/network settings do NOT
live here (§13).

```json
{
  "app": "cadreur",
  "version": 1,
  "meta": {
    "name": "Bergman création 2026",
    "saved_at": "2026-07-19T14:03:00Z",
    "notes": "sensor on flybar 4 — re-rigging it invalidates all points"
  },
  "settings": { "active_look": "cinemascope", "active_lens_memory": "M1" },
  "lens_memories": ["M1", "M2", "M3"],
  "smoothing": {
    "ema_tau_s": 5.0,
    "deadband_scale": 0.0005, "deadband_px": 0.5,
    "slew_scale_per_s": 0.05, "slew_px_per_s": 50.0,
    "refresh_hz": 1.0
  },
  "looks": [
    {
      "id": "cinemascope",
      "name": "Cinémascope",
      "beamers": {
        "front": {
          "layer": "scope-front",
          "enabled": true,
          "calibrations": {
            "M1": {
              "interp": "linear",
              "trim": { "scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0 },
              "points": [
                { "distance_m": 2.10, "scale": 0.620, "pos_x": 960.0, "pos_y": 540.0 },
                { "distance_m": 3.20, "scale": 0.535, "pos_x": 960.0, "pos_y": 574.0 },
                { "distance_m": 4.30, "scale": 0.458, "pos_x": 960.0, "pos_y": 610.0 }
              ]
            }
          }
        },
        "rear": {
          "layer": "scope-rear",
          "enabled": true,
          "calibrations": {
            "default": { "interp": "linear",
                         "trim": { "scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0 },
                         "points": [] }
          }
        }
      }
    }
  ]
}
```

Rules (enforce in `show.py`, test in `test_show.py`):

- `beamers` keys are exactly `"front"` / `"rear"`; each optional per look. A missing
  beamer = idle in that look (nothing sent; panel shows "no layer in this look").
- Front resolves its calibration set via `settings.active_lens_memory`; rear always uses
  the reserved key `"default"` — one uniform code path.
- Active lens memory has **no set** in the active look → that beamer is *inhibited*
  (status "Uncalibrated for M2 in this look"). Never fall back to another memory's set.
- `distance_m` is **abs_m**. Points are stored sorted by `distance_m`; the loader sorts
  defensively; two points within **1 mm** → keep the later one.
- `trim` lives inside each calibration set (a trim correcting M1 must not touch M2).
  Applied post-interpolation: `scale *= scale_mul; x += dx_px; y += dy_px`.
- **Armed is never persisted.** `enabled` per beamer *is* (a look can legitimately keep a
  beamer off).
- `interp` is `"linear"` in v1; the field exists so `"optical"` (1/d fit) can arrive
  without a schema bump.
- Versioning: integer `version`. Loader refuses a missing or *greater* version with a
  clear "made by a newer Cadreur" message; migrations bump the integer. Unknown keys are
  ignored on load (house style); save writes only the known schema.
- Multiple looks may target the same layer — legal; only the active look sends.

## 7. Interpolation

Per parameter (scale, pos_x, pos_y independently), over the set's sorted points:

- **N = 0** → beamer inhibited (uncalibrated).
- **N = 1** → constant hold (useful for a beamer that barely changes).
- **N ≥ 2** → piecewise-linear between neighbors; **clamp** to the end values outside
  the range, with a "clamped low/high — out of calibrated range" status hint.
- Rounding before send: scale → 4 dp, positions → 2 dp (both below the dead-bands, so
  rounding never fights the send policy). OSC floats.

Worked example (points from §6): at `abs_m = 2.65`, `t = (2.65−2.10)/(3.20−2.10) = 0.5`
→ `scale = 0.620 + 0.5·(0.535−0.620) = 0.5775` → sent `0.5775`; `pos_y = 540 + 0.5·34 = 557.0`.

**Why no 1/d "optical" mode in v1**: projected image size ∝ throw distance, so the
compensating `scale(d)` is a gentle hyperbola `k/(a ± d)`. Over a 2–3 m travel with 3
points, the piecewise-linear chord error is already small — and the fix is operational,
not mathematical: *capture another point where it visibly curves* (that is why point
count is unlimited). The `interp` field keeps the door open.

## 8. Smoothing

The truss hanging from the rails oscillates slightly (sub-Hz pendulum); the Pi feed is a
0.75 cm staircase; look/memory/point edits cause steps. At 4 cm/min (0.67 mm/s), seconds
of lag are invisible — spend them freely.

| # | Stage | Domain | Default (show-file key) | Purpose |
|---|---|---|---|---|
| 1 | Median-of-3 | distance | fixed | SSE hiccup/replay insurance |
| 2 | EMA parameterized in seconds: `alpha = dt/(tau+dt)` with measured dt | distance | `ema_tau_s = 5.0` (0–30) | Pendulum rejection: ~16× attenuation at 0.5 Hz (±1 cm sway → ±0.6 mm); ramp lag = `tau·v` ≈ 3.3 mm |
| 3 | Interpolation + trim (§7) | → scale/x/y | — | — |
| 4 | Slew limiter toward target | output | `slew_scale_per_s = 0.05`, `slew_px_per_s = 50` | Turns **any** discontinuity (look/memory switch, point/trim edit, staircase step, stale-recovery jump) into a 1–2 s glide; far above tracking speed, so it never limits normal motion |
| 5 | Send dead-band + refresh | output | `deadband_scale = 0.0005`, `deadband_px = 0.5`, `refresh_hz = 1.0` | Send when moved ≥ dead-band OR refresh due; absolute values self-heal Millumin restarts |

- Stages 1–2 run on SSE arrival (client thread, real dt); stages 4–5 run in the engine
  tick (20 Hz).
- **Snap, don't slew, on Arm** (arming is a setup-time act; the operator expects
  immediate effect). On **Calibrate-mode exit**, re-seed the slew limiter from a fresh
  feedback query of the layer's actual values (the operator just moved it); if feedback
  is unavailable, snap.
- On stale/disconnect: freeze stage-2 output (no filter reset); on recovery, median+EMA
  absorb small jumps, the slew limiter glides big ones.
- Operator-tunable (Advanced drawer, persisted in the show file): `ema_tau_s`, both
  dead-bands, both slew rates, `refresh_hz`. Median-3 is fixed. No hysteresis on
  distance — dead-banding in output space (pixels) is what matters.

## 9. OSC I/O

References: Millumin OSC documentation
<https://github.com/anome/millumin-dev-kit/wiki/OSC-documentation> and
<https://help.millumin.com/docs/connect/osc-api/> (V5; V4 syntax identical).

**Out (UDP → `millumin.host:millumin.port`, default 127.0.0.1:5000):**

```
/layer:NAME/scale <float>              # uniform multiplier, 1.0 = 100 %
/layer:NAME/position/xy <float float>  # pixels, canvas top-left origin
```

- Layer names: validate `^[A-Za-z0-9._-]+$` at edit time (spaces break OSC addresses).
- Send policy per §8 stage 5. Steady-state traffic ≈ 4 msg/s; worst case ~80 msg/s on
  loopback — negligible.
- UDP reports no errors for unknown layers — see the armed probe below.

**In (feedback listener bound on UDP `millumin.feedback_port`, default 8000):**

- In Millumin: Device manager (⌘K) → OSC → enable **API feedback** with destination
  `127.0.0.1:8000`. Input (5000) and feedback (8000) ports are distinct and cannot be
  shared. Cadreur cannot enable this remotely — it is a manual Millumin setting.
- Query protocol (used by Capture, Test Millumin, calibrate-exit re-seed, armed probe):

  ```
  send  /layer:NAME/scale/?          →  expect  /millumin/layer:NAME/scale <f>
  send  /layer:NAME/position/xy/?    →  expect  /millumin/layer:NAME/position/xy <f f>
  ```

  Correlate by expected reply address; ignore unsolicited feedback traffic; total
  timeout `feedback_timeout_ms` (default 1500). **Tolerate both reply arities** for
  position: one `/position/xy <f f>` message or separate `/position/x <f>` +
  `/position/y <f>` (verify the real shape on day 1 of Mac bring-up).
- **Armed probe**: while armed, every 10 s query one active layer (round-robin). Two
  consecutive misses → non-blocking warning "layer 'NAME' unreachable — or API feedback
  down" (the two causes are indistinguishable; the warning text says to check both).
  Output continues regardless.
- Implementation: `python-osc` — `SimpleUDPClient` out, `ThreadingOSCUDPServer` in
  (dispatcher with a default handler feeding a small correlation table).

## 10. Runtime state machine

- **Global**: `DISARMED` (startup, and after any show load/import) ⇄ `ARMED` via the
  master toggle. Disarmed ⇒ **zero OSC traffic**, and disarming does *not* send any
  "return to neutral" — layers stay where they are.
- **Per-beamer output gate** — all must hold:

  ```
  armed ∧ active look has this beamer ∧ beamer.enabled
        ∧ calibration set exists for the active lens memory (front) / default (rear)
        ∧ set has ≥ 1 point ∧ ¬calibrate_mode ∧ a usable distance has been received
  ```

- **Distance source states**: `LIVE` → feed chain; `STALE` / `DISCONNECTED` → hold last
  smoothed value, keep refresh-cadence sends (absolute + periodic = self-healing),
  banner "Distance stale — holding". Recovery per §8.
- **Look / lens-memory switch while armed**: recompute targets; affected beamers glide
  (slew); beamers losing their set stop sending with an explanatory status. Lens memory
  is global (it mirrors the physical projector), survives look switches, persisted.
- **Engine**: asyncio task in the FastAPI lifespan, 20 Hz tick: read smoothed abs_m →
  gates → targets → slew → send policy → snapshot for the UI.

## 11. Calibration workflow

Preconditions surfaced in the UI: Millumin OSC input on 5000; API feedback →
127.0.0.1:8000 (§9).

1. Select look, beamer, and (front) the lens memory being calibrated. Toggle
   **Calibrate mode** — output for that beamer is suspended (mandatory: refresh sends
   would fight the operator's dragging). Amber "CALIBRATING — output suspended" flag.
2. Fit the frame perfectly using Millumin's own tools at the current scrim position.
3. **Capture point** (disabled while distance is stale) → cadreur queries scale +
   position (§9) and stores `{distance_m: current smoothed abs_m, scale, pos_x, pos_y}`,
   sorted insert; an existing point within **0.03 m** is replaced (natural re-capture at
   a mark). Toast shows the captured numbers.
4. **Timeout** → modal: "No reply from Millumin. Check: (a) Millumin is running with
   this project, (b) a layer named 'NAME' exists, (c) Device manager → OSC → API
   feedback is enabled → 127.0.0.1:8000. Or enter values manually." Manual form:
   distance pre-filled from live abs_m (editable), scale/x/y typed from Millumin's
   inspector.
5. **Points table** (always sorted by distance): columns d / scale / x / y; inline edit;
   per-row **Re-capture** (replaces the row with current distance + current Millumin
   values; confirm shows old→new distance delta) and **Delete** (confirm).
6. Leave Calibrate mode → output resumes with the §8 re-seed-or-snap rule, then glides.
7. **Trim** (armed, outside calibrate): nudge scale ±0.001 / ±0.01, x/y ±1 / ±10 px,
   live; **Bake into points** folds trim into every point of the set (scale multiplies,
   px adds) then resets trim; **Reset trim**.
8. **Test Millumin** (always available): one query round-trip; reports latency or
   failure with the same checklist.

Repeat per lens memory for the front beamer (switch the projector's memory, then the
matching chip in cadreur, recalibrate). Typical show: 2–3 points per set.

## 12. UI

Single dark page, tablet-friendly (large touch targets; status = color **and** text),
same visual family as the Pi readout.

```
┌────────────────────────────────────────────────────────────────────┐
│ CADREUR Bergman   ● Pi: live 3.214 m   ● Millumin: ok (12 ms)      │
│                                            [ ARM ██ OFF ] (big)    │
├────────────────────────────────────────────────────────────────────┤
│  ABS DISTANCE  3.214 m      stage position  −1.244 m (crew ref)    │
│  travel ├──●────┼───────▲───────┼──────┼──┤  ▲ cart  ┼ cal points  │
│         2.10  (front ticks above bar, rear below)          4.30    │
├────────────────────────────────────────────────────────────────────┤
│  LOOK [ Cinémascope ▼ ]  [+ new] [duplicate] [rename] [delete]     │
├────────────────────────────┬───────────────────────────────────────┤
│ FRONT  layer: scope-front  │ REAR  layer: scope-rear               │
│ lens memory (M1)(M2)(M3)   │                                       │
│ [enable ✓] status: OK      │ [enable ✓] status: clamped high       │
│ live: scale .535 x 960     │ live: scale .700 x 960 y 512          │
│       y 574                │                                       │
│ [Calibrate mode]           │ [Calibrate mode]                      │
│  [⊕ Capture point]         │  (same panel layout)                  │
│  d     scale   x     y     │                                       │
│  2.10  .620   960   540 ✎↻🗑│                                       │
│  3.20  .535   960   574 ✎↻🗑│                                       │
│  4.30  .458   960   610 ✎↻🗑│                                       │
│ trim scale[−|+] x[−|+] y[−|+]  [bake] [reset]                      │
├────────────────────────────┴───────────────────────────────────────┤
│ show: bergman-2026.json ● autosaved  [Save][Save as][Load]         │
│ [Export][Import]        ▸ Advanced (smoothing)          EN/FR      │
└────────────────────────────────────────────────────────────────────┘
```

- Header: Pi dot (live/stale/disconnected + abs_m), Millumin dot (last probe/test
  result + latency), master ARM toggle (red/green, unmissable).
- Travel bar: min/max of all calibrated distances across the active look; cart marker;
  per-beamer point ticks (front above, rear below).
- "Stage position (crew ref)" = the Pi's tared `position_m`, displayed only — so the
  operator and stage manager can talk in the same numbers.
- Front panel: targeted layer name always visible; lens-memory chips (big, colored;
  active chip highlighted); chips with no calibration set in the active look show a
  hollow/warning style.
- Live line per beamer: the values currently sent (or would-be values when gated, with
  the gating reason as status).
- **i18n**: exactly the Pi mechanism — `<script type="application/json" id="i18n">`
  with `en`/`fr` dictionaries, `data-i18n` attributes, auto language with `?lang=`
  override remembered in `localStorage["cadreur_lang"]`, English fills missing keys
  (see `web/index.html` + `web/app.js` in this repo).
- Transport: the app's own SSE `GET /stream` (10 Hz snapshot of everything the page
  shows: distance + source state, arm, active look/memory, per-beamer computed values,
  gate status, clamp/calibrate flags, dirty/autosave state, warnings) + `POST /api/*`
  controls returning `{"ok": true, ...}` — the Pi app's exact interaction grammar.

**API endpoints** (all POST unless noted): `GET /stream`, `GET /api/health`,
`POST /api/arm {armed}`, `/api/look {id}`, `/api/looks` (create/duplicate/rename/delete
via `{op, ...}`), `/api/lens_memory {id}`, `/api/beamer/{b}/enable {enabled}`,
`/api/beamer/{b}/calibrate {on}`, `/api/beamer/{b}/capture`, `/api/beamer/{b}/points
{op: add|edit|delete|recapture, ...}`, `/api/beamer/{b}/trim {scale_mul?, dx_px?,
dy_px?}`, `/api/beamer/{b}/trim/bake`, `/api/beamer/{b}/trim/reset`, `/api/save`,
`/api/save_as {name}`, `/api/load {name}`, `GET /api/shows` (list server-side files),
`GET /api/export` (download current JSON), `POST /api/import` (upload),
`/api/smoothing {key: value}`, `/api/test_millumin`.

## 13. Config & persistence

**Machine config** — `cadreur.toml` at repo root (copied from `cadreur.example.toml`;
env override `CADREUR_CONFIG`; same dataclass-per-section, unknown-key-tolerant loader
as `src/telemetre/config.py`; every key has a code default):

```toml
[telemetre]
url = "http://192.168.0.51"     # cadreur appends /stream
stale_after_ms = 1500

[millumin]
host = "127.0.0.1"
port = 5000                     # Millumin OSC input
feedback_port = 8000            # must match Millumin's API-feedback destination
feedback_timeout_ms = 1500

[web]
host = "127.0.0.1"              # set 0.0.0.0 to allow a tablet control surface
port = 8080

[shows]
dir = "shows"                   # repo-relative
autosave = true
autosave_debounce_s = 5
```

> The web UI has no authentication. Default bind is loopback; binding 0.0.0.0 hands
> geometry control to anyone on the stage LAN — acceptable on this closed network, but
> it is a deliberate operator choice.

- **Show JSON** (§6): autosave = dirty flag + 5 s debounce + atomic write (tmp +
  `os.replace`). On each app start, before loading, copy the current file to
  `shows/backups/<name>-<YYYYmmdd-HHMMSS>.json` (keep the 10 newest).
- **Runtime state** — `cadreur_state.json` (mirrors the Pi's `state.json`): last-opened
  show path only. Armed is never persisted anywhere.
- Git: `shows/` is gitignored except `shows/example-show.json`.

## 14. Repo layout & house style

```
src/telemetre/                  # Pi app — DO NOT TOUCH
src/cadreur/
  __init__.py  __main__.py      # python -m cadreur → uvicorn (mirror telemetre/__main__.py)
  app.py                        # FastAPI: /stream SSE, /api/*, static mount
  config.py                     # TOML loader (mirror telemetre/config.py)
  show.py                       # schema, load/save/migrate, rules of §6
  state.py                      # lock + snapshot() (mirror telemetre/state.py)
  telemetre_client.py           # SSE reader thread (mirror serial_reader.py reconnect loop)
  smoothing.py  interp.py       # pure, dependency-free (§7, §8)
  engine.py                     # 20 Hz tick: gates → targets → slew → send policy
  millumin.py                   # OSC client + feedback listener + query-with-timeout
  web/index.html  web/app.js  web/style.css
scripts/sim_telemetre.py  scripts/millumin_sim.py
tests/test_interp.py  test_smoothing.py  test_show.py  test_engine.py
documentation/PRD-cadreur.md    # this file
cadreur.example.toml
shows/example-show.json
```

House-style checklist (copy the pattern from the named `telemetre` file):
- dataclass-per-`[section]` config with unknown keys ignored (`telemetre/config.py`,
  incl. the `REPO_ROOT` idiom);
- one lock, `snapshot()` dict for the UI (`telemetre/state.py`);
- daemon reader thread with reconnect + backoff (`telemetre/serial_reader.py`);
- SSE generator with periodic keepalive comment + no-cache revalidating static mount
  (`telemetre/app.py`);
- `{"ok": true, ...}` POST responses; defensive I/O (a bad file or dead socket must
  never crash the app);
- vanilla-JS IIFE + `EventSource` + i18n JSON block (`web/app.js`, `web/index.html`);
- `unittest`, pure-logic modules tested without hardware/network.

`pyproject.toml`: add `cadreur = "cadreur.__main__:main"` to `[project.scripts]`.
Existing dependencies already cover fastapi / uvicorn / python-osc (pyserial is unused
by cadreur — harmless). v1 is operator-started (`python -m cadreur` in a venv); no
launchd unit.

## 15. Testing & simulation

**Unit tests** (`python -m unittest discover -s tests -v` — same command covers both apps):
- `test_interp`: exact at points; midpoints; clamp both ends; N=0 inhibit; N=1 hold;
  sorted insert; 1 mm dedup; 0.03 m merge-replace; trim application; rounding.
- `test_smoothing`: tau-EMA attenuation of a synthetic 0.5 Hz ±1 cm sine (residual
  < 1 mm); ramp lag ≈ `tau·v`; slew step response duration; dead-band suppression;
  freeze/resume without reset; snap-on-arm.
- `test_show`: round-trip; unknown keys ignored; version refusal (missing / newer);
  missing-lens-memory inhibit; defensive sort; armed never serialized.
- `test_engine`: fake clock — gate truth table; epsilon + refresh send decisions; stale
  hold; look-switch glide; calibrate-mode suspension.

**`scripts/sim_telemetre.py`** — byte-compatible fake Pi for the Windows/any-OS dev loop:
serves `GET /stream` with the §5 payload; `--start_m 3.2 --speed_cm_min 4
--direction -1` scripted travel; `--osc_amp_cm 1 --osc_hz 0.5` injected pendulum;
`--zero_cm 197 --sign -1` non-trivial tare (exercises abs_m reconstruction);
`--stale_burst` to rehearse hold behavior.

**`scripts/millumin_sim.py`** — fake Millumin: listens on 5000; pretty-prints every
message with timestamp + per-layer last state; answers `/layer:*/scale/?` and
`/layer:*/position/xy/?` from a mutable table toward the configured feedback
destination; `--no-feedback` exercises the capture-timeout → manual-entry path;
`--reply-split-xy` exercises the two-message position reply arity.

**Mac bring-up checklist** (day 1 with real Pi + Millumin):
1. Millumin: Device manager (⌘K) → OSC → input port 5000 enabled; **API feedback**
   enabled → destination 127.0.0.1:8000.
2. macOS will prompt to allow Python to receive incoming network connections (the
   feedback listener) — allow it.
3. Verify layer names against Millumin's **"Copy all Addresses"** (Device manager →
   OSC) — exact spelling.
4. **Test Millumin** green with single-digit-ms latency; confirm the real
   `/position/xy` reply arity and adjust if needed.
5. Capture 2 points at the two travel ends (cart really moved); arm; verify tracking
   during a slow run.
6. Kill and relaunch Millumin mid-run → geometry restored within ~1 s (refresh sends).
7. Switch Millumin columns → confirm behavior and that refresh re-asserts geometry.
8. Disarm → confirm **total OSC silence** (watch millumin_sim on a mirror port or
   Millumin's OSC monitor).

## 16. Milestones & acceptance

| M | Scope | Accepted when |
|---|---|---|
| M1 | `interp.py`, `smoothing.py`, `show.py` + their tests | All unit tests green on the dev machine; no FastAPI/network code involved |
| M2 | `telemetre_client.py`, `millumin.py`, `engine.py` | Against `sim_telemetre` + `millumin_sim`: correct gate behavior, glides on look switch, ≤ dead-band silence at rest, refresh cadence visible, stale hold works |
| M3 | `app.py` + web UI | Full workflow (§11) doable against the simulators incl. capture, timeout fallback, trim/bake, save/load/export/import, EN/FR |
| M4 | Mac bring-up | §15 checklist fully passed on the real rig; a show file for the production exists with ≥ 1 front memory + rear calibrated |

## 17. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Layer renamed/missing — UDP fails silently | Armed 10 s round-robin feedback probe → warning; name validation at edit time; "Copy all Addresses" check in bring-up |
| Wrong lens memory selected (app cannot detect the projector's state) | Big colored chips; uncalibrated-memory inhibit; future PJLink readback |
| Uniform scale only (aspect preserved) | Fine for both defined looks — capture pairs bake Millumin's anchor math into the data. True keystone from off-axis projection is what lens memories mitigate physically; corner-pin OSC (`/layer:NAME/mapping/topLeft …`) is the reserved future fix |
| Sensor re-rigged / flybar moved → all points invalid despite tare-immunity | `meta.notes` warning in the show file; recalibrate (PRD states it plainly) |
| Pi's 0.75 cm hysteresis staircase | Absorbed by §8; if finer tracking is ever needed, publish a pre-hysteresis value from the Pi (cross-app open item, not v1) |
| `/position/xy` feedback reply arity uncertain | Tolerate both shapes (§9); verify day 1 (§15) |
| Autosave mid-edit corruption | Atomic replace + rotating startup backups (§13) |

## 18. Out of scope / future

- Corner-pin tracking via `/layer:NAME/mapping/*` (per-corner calibration points).
- PJLink (or brand-specific) lens-memory recall/readback on the front projector.
- OSC-triggered look switching from Millumin cues — address `/cadreur/look <id>`
  reserved.
- Multiple scrims / additional beamers; launchd auto-start; authentication.
