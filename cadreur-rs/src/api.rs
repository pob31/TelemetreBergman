//! HTTP API: SSE snapshot stream + REST controls + the embedded web UI.
//!
//! Ported from `src/cadreur/app.py`. All control endpoints return
//! `{"ok": true, ...}`; operator-level failures return
//! `{"ok": false, "error": ...}` with status 400 — a bad request must never
//! crash the app.
//!
//! The UI is served from `src/cadreur/web/`, compiled into the binary. It is
//! the same directory the Python serves, so the two implementations cannot
//! drift apart, and the Rust build still ships as a single file.

use std::convert::Infallible;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use include_dir::{Dir, include_dir};
use serde_json::{Map, Value, json};

use crate::config::Config;
use crate::engine::Engine;
use crate::interp::{self, Point, round_dp};
use crate::millumin::OscSender;
use crate::show::{self, BeamerKey, ShowError};
use crate::state::{Shared, Source, State as CadreurState, lock};

static WEB: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/../src/cadreur/web");

const SNAPSHOT_HZ: f64 = 10.0;

#[derive(Clone)]
pub struct App {
    pub state: Shared,
    pub engine: Arc<Mutex<Engine>>,
    pub io: Arc<dyn OscSender>,
    pub cfg: Config,
}

// --- helpers -----------------------------------------------------------------

fn err(message: impl std::fmt::Display) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({"ok": false, "error": message.to_string()})))
        .into_response()
}

fn ok(v: Value) -> Response {
    Json(v).into_response()
}

/// A malformed or absent body is an empty object, exactly as the Python's
/// `body_of` did — defensive by design.
fn body_of(bytes: &Bytes) -> Map<String, Value> {
    serde_json::from_slice::<Value>(bytes)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn beamer(b: &str) -> Result<BeamerKey, Box<Response>> {
    BeamerKey::parse(b).ok_or_else(|| Box::new(err(format!("Unknown beamer '{b}'."))))
}

/// Errors if the channel does not exist, so handlers fail before mutating.
fn require_channel(st: &CadreurState, b: BeamerKey, cid: &str) -> Result<(), Box<Response>> {
    if st.show.channel(b, cid).is_none() {
        return Err(Box::new(err(format!("Unknown channel '{cid}'."))));
    }
    Ok(())
}

fn as_f64(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn as_bool(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|x| x != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Null => false,
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Keeps the accented characters a French show name needs, drops anything that
/// could escape the shows directory.
fn sanitize_name(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .filter(|c| {
            c.is_ascii_alphanumeric()
                || "àâäéèêëîïôöùûüç._ -".contains(*c)
        })
        .collect();
    let trimmed = cleaned.trim();
    if trimmed.is_empty() { "show".to_string() } else { trimmed.to_string() }
}

/// Store the channel's current manual drive values at the current distance.
fn capture_point(
    st: &mut CadreurState,
    b: BeamerKey,
    cid: &str,
    abs_m: f64,
) -> Result<(Point, bool), ShowError> {
    let key = CadreurState::chan_key(b, cid);
    let m = st.manual_of(&key);
    let point = Point {
        distance_m: round_dp(abs_m, 3),
        scale: round_dp(m.scale, 4),
        pos_x: round_dp(m.pos_h, 4),
        pos_y: round_dp(m.pos_v, 4),
    };
    let cset = st.show.ensure_cal_set(b, cid)?;
    let (points, replaced) = interp::insert_point(&cset.points, point);
    cset.points = points;
    Ok((point, replaced))
}

// --- routes ------------------------------------------------------------------

pub fn router(app: App) -> Router {
    Router::new()
        .route("/api/health", get(health))
        .route("/api/arm", post(arm))
        .route("/api/lens_memory", post(set_lens_memory))
        .route("/api/beamer/{b}/channel/add", post(channel_add))
        .route("/api/channel/{b}/{cid}/delete", post(channel_delete))
        .route("/api/channel/{b}/{cid}/rename", post(channel_rename))
        .route("/api/channel/{b}/{cid}/osc", post(channel_osc))
        .route("/api/channel/{b}/{cid}/enable", post(channel_enable))
        .route("/api/channel/{b}/{cid}/calibrate", post(channel_calibrate))
        .route("/api/channel/{b}/{cid}/show", post(channel_show))
        .route("/api/channel/{b}/{cid}/manual", post(channel_manual))
        .route("/api/channel/{b}/{cid}/capture", post(channel_capture))
        .route("/api/capture_all", post(capture_all))
        .route("/api/channel/{b}/{cid}/points", post(channel_points))
        .route("/api/channel/{b}/{cid}/trim", post(channel_trim))
        .route("/api/channel/{b}/{cid}/trim/bake", post(channel_trim_bake))
        .route("/api/channel/{b}/{cid}/trim/reset", post(channel_trim_reset))
        .route("/api/smoothing", post(set_smoothing))
        .route("/api/test_millumin", post(test_millumin))
        .route("/api/save", post(save))
        .route("/api/save_as", post(save_as))
        .route("/api/load", post(load_show_route))
        .route("/api/shows", get(list_shows))
        .route("/api/export", get(export))
        .route("/api/import", post(import_show))
        .route("/api/meta", post(set_meta))
        .route("/stream", get(stream))
        .fallback(static_files)
        .with_state(app)
}

async fn health(State(app): State<App>) -> Response {
    ok(lock(&app.state).health())
}

async fn arm(State(app): State<App>, body: Bytes) -> Response {
    let armed = body_of(&body).get("armed").is_some_and(as_bool);
    lock(&app.state).armed = armed;
    ok(json!({"ok": true, "armed": armed}))
}

async fn set_lens_memory(State(app): State<App>, body: Bytes) -> Response {
    let mem = body_of(&body).get("id").and_then(Value::as_str).unwrap_or("").to_string();
    let mut st = lock(&app.state);
    if !st.show.lens_memories.contains(&mem) {
        return err(format!("Unknown lens memory '{mem}'."));
    }
    st.show.settings.active_lens_memory = mem;
    st.mark_dirty();
    ok(json!({"ok": true}))
}

// --- channel management ------------------------------------------------------

async fn channel_add(State(app): State<App>, Path(b): Path<String>, body: Bytes) -> Response {
    let Ok(b) = beamer(&b) else { return err(format!("Unknown beamer '{b}'.")) };
    let name = body_of(&body).get("name").and_then(Value::as_str).map(str::to_string);
    let mut st = lock(&app.state);
    let ch = show::add_channel(&mut st.show, b, name.as_deref().filter(|s| !s.is_empty()));
    st.mark_dirty();
    ok(json!({"ok": true, "id": ch.id}))
}

async fn channel_delete(State(app): State<App>, Path((b, cid)): Path<(String, String)>) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let mut st = lock(&app.state);
    if let Err(e) = show::delete_channel(&mut st.show, b, &cid) {
        return err(e);
    }
    let key = CadreurState::chan_key(b, &cid);
    st.calibrate.remove(&key);
    st.mark_dirty();
    ok(json!({"ok": true}))
}

async fn channel_rename(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let name = body_of(&body).get("name").and_then(Value::as_str).unwrap_or("").to_string();
    let mut st = lock(&app.state);
    if let Err(e) = show::rename_channel(&mut st.show, b, &cid, &name) {
        return err(e);
    }
    st.mark_dirty();
    ok(json!({"ok": true}))
}

async fn channel_osc(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let addrs = body_of(&body);
    let mut st = lock(&app.state);
    if let Err(e) = show::set_channel_osc(&mut st.show, b, &cid, &addrs) {
        return err(e);
    }
    st.mark_dirty();
    ok(json!({"ok": true}))
}

// --- per-channel controls ----------------------------------------------------

async fn channel_enable(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let enabled = body_of(&body).get("enabled").is_some_and(as_bool);
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    st.show.channel_mut(b, &cid).expect("checked").enabled = enabled;
    st.mark_dirty();
    ok(json!({"ok": true}))
}

async fn channel_calibrate(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let on = body_of(&body).get("on").is_some_and(as_bool);
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    let key = CadreurState::chan_key(b, &cid);
    if on {
        st.calibrate.insert(key);
    } else {
        st.calibrate.remove(&key);
    }
    ok(json!({"ok": true, "calibrate": on}))
}

/// Reveal the layer in Millumin by sending `osc_show` as a **pure path** with
/// no argument, so the operator can display the layer being calibrated from
/// the stage. Millumin treats the bare address as the trigger.
async fn channel_show(State(app): State<App>, Path((b, cid)): Path<(String, String)>) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let addr = {
        let st = lock(&app.state);
        match st.show.channel(b, &cid) {
            Some(ch) => ch.osc_show.clone(),
            None => return err(format!("Unknown channel '{cid}'.")),
        }
    };
    app.io.send_bang(&addr);
    ok(json!({"ok": true, "sent": addr}))
}

/// The live drive values (normalised 0..1) sent while calibrating.
async fn channel_manual(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let d = body_of(&body);
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    let key = CadreurState::chan_key(b, &cid);
    let mut m = st.manual_of(&key);
    for k in ["scale", "pos_v", "pos_h"] {
        let Some(raw) = d.get(k) else { continue };
        let Some(x) = as_f64(raw) else { return err(format!("Bad value for {k}.")) };
        let x = x.clamp(0.0, 1.0);
        match k {
            "scale" => m.scale = x,
            "pos_v" => m.pos_v = x,
            _ => m.pos_h = x,
        }
    }
    st.manual.insert(key, m);
    ok(json!({"ok": true, "manual": m}))
}

async fn channel_capture(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    if st.source_state(crate::state::monotonic()) != Source::Live {
        return err("Distance is stale — capture disabled.");
    }
    let Some(abs_m) = st.distance().0 else { return err("No distance received yet.") };
    match capture_point(&mut st, b, &cid, abs_m) {
        Ok((point, replaced)) => {
            st.mark_dirty();
            ok(json!({"ok": true, "point": point, "replaced": replaced}))
        }
        Err(e) => err(e),
    }
}

/// Capture a point at the current distance for every channel in calibrate mode
/// — "fit every layer at this scrim position, then capture in one go".
async fn capture_all(State(app): State<App>) -> Response {
    let mut st = lock(&app.state);
    if st.source_state(crate::state::monotonic()) != Source::Live {
        return err("Distance is stale — capture disabled.");
    }
    let Some(abs_m) = st.distance().0 else { return err("No distance received yet.") };
    let targets: Vec<(BeamerKey, String)> = BeamerKey::ALL
        .iter()
        .flat_map(|&b| {
            st.show
                .channels(b)
                .iter()
                .map(|ch| (b, ch.id.clone()))
                .filter(|(b, cid)| st.calibrate.contains(&CadreurState::chan_key(*b, cid)))
                .collect::<Vec<_>>()
        })
        .collect();
    let mut n = 0;
    for (b, cid) in targets {
        if capture_point(&mut st, b, &cid, abs_m).is_ok() {
            n += 1;
        }
    }
    if n > 0 {
        st.mark_dirty();
    }
    ok(json!({"ok": true, "count": n, "distance_m": round_dp(abs_m, 3)}))
}

async fn channel_points(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let d = body_of(&body);
    let op = d.get("op").and_then(Value::as_str).unwrap_or("").to_string();
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }

    // "recapture" needs live distance and the manual values; resolve both
    // before taking the calibration set, which borrows the show mutably.
    let recapture_point = if op == "recapture" {
        if st.source_state(crate::state::monotonic()) != Source::Live {
            return err("Distance is stale — capture disabled.");
        }
        let Some(abs_m) = st.distance().0 else { return err("No distance received yet.") };
        let m = st.manual_of(&CadreurState::chan_key(b, &cid));
        Some(Point {
            distance_m: round_dp(abs_m, 3),
            scale: round_dp(m.scale, 4),
            pos_x: round_dp(m.pos_h, 4),
            pos_y: round_dp(m.pos_v, 4),
        })
    } else {
        None
    };

    let cset = match st.show.ensure_cal_set(b, &cid) {
        Ok(c) => c,
        Err(e) => return err(e),
    };

    match op.as_str() {
        "add" => {
            let Some(p) = d.get("point").and_then(interp::clean_point) else {
                return err("Point needs numeric distance_m, scale, pos_x, pos_y.");
            };
            cset.points = interp::insert_point(&cset.points, p).0;
        }
        "edit" | "delete" | "recapture" => {
            let idx = d.get("index").and_then(as_f64).unwrap_or(-1.0);
            let idx = if idx < 0.0 { usize::MAX } else { idx as usize };
            if idx >= cset.points.len() {
                return err("No such point.");
            }
            match op.as_str() {
                "delete" => {
                    cset.points.remove(idx);
                }
                "edit" => {
                    let Some(p) = d.get("point").and_then(interp::clean_point) else {
                        return err("Point needs numeric distance_m, scale, pos_x, pos_y.");
                    };
                    cset.points.remove(idx);
                    cset.points = interp::insert_point(&cset.points, p).0;
                }
                _ => {
                    cset.points.remove(idx);
                    let p = recapture_point.expect("resolved above");
                    cset.points = interp::insert_point(&cset.points, p).0;
                }
            }
        }
        _ => return err(format!("Unknown op '{op}'.")),
    }
    let points = cset.points.clone();
    st.mark_dirty();
    ok(json!({"ok": true, "points": points}))
}

async fn channel_trim(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
    body: Bytes,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let d = body_of(&body);
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    // Validate before mutating, so a bad value cannot half-apply.
    let mut pending: Vec<(&str, f64)> = Vec::new();
    for k in ["scale_mul", "dx_px", "dy_px"] {
        let Some(raw) = d.get(k) else { continue };
        let Some(x) = as_f64(raw) else { return err(format!("Bad value for {k}.")) };
        pending.push((k, x));
    }
    let cset = match st.show.ensure_cal_set(b, &cid) {
        Ok(c) => c,
        Err(e) => return err(e),
    };
    for (k, x) in pending {
        match k {
            "scale_mul" => cset.trim.scale_mul = x,
            "dx_px" => cset.trim.dx_px = x,
            _ => cset.trim.dy_px = x,
        }
    }
    let trim = cset.trim;
    st.mark_dirty();
    ok(json!({"ok": true, "trim": trim}))
}

async fn channel_trim_bake(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    let cset = match st.show.ensure_cal_set(b, &cid) {
        Ok(c) => c,
        Err(e) => return err(e),
    };
    cset.points = interp::bake_trim(&cset.points, cset.trim);
    cset.trim = interp::Trim::default();
    let points = cset.points.clone();
    st.mark_dirty();
    ok(json!({"ok": true, "points": points}))
}

async fn channel_trim_reset(
    State(app): State<App>,
    Path((b, cid)): Path<(String, String)>,
) -> Response {
    let b = match beamer(&b) {
        Ok(b) => b,
        Err(e) => return *e,
    };
    let mut st = lock(&app.state);
    if let Err(e) = require_channel(&st, b, &cid) {
        return *e;
    }
    match st.show.ensure_cal_set(b, &cid) {
        Ok(c) => c.trim = interp::Trim::default(),
        Err(e) => return err(e),
    }
    st.mark_dirty();
    ok(json!({"ok": true}))
}

// --- smoothing / millumin ----------------------------------------------------

async fn set_smoothing(State(app): State<App>, body: Bytes) -> Response {
    let d = body_of(&body);
    let mut st = lock(&app.state);
    let mut sm = st.show.smoothing;
    for (k, v) in &d {
        let Some(x) = as_f64(v) else { return err(format!("Bad value for {k}.")) };
        match k.as_str() {
            "ema_tau_s" => sm.ema_tau_s = x.clamp(0.0, 30.0),
            "deadband_scale" => sm.deadband_scale = x.clamp(0.0, 0.1),
            "slew_scale_per_s" => sm.slew_scale_per_s = x.clamp(0.001, 10.0),
            "refresh_hz" => sm.refresh_hz = x.clamp(0.1, 20.0),
            other => return err(format!("Unknown smoothing key '{other}'.")),
        }
    }
    st.show.smoothing = sm;
    st.mark_dirty();
    ok(json!({"ok": true, "smoothing": sm}))
}

async fn test_millumin() -> Response {
    // Custom Interaction addresses are send-only (no /? readback) and feedback
    // is off by default, so there is nothing to round-trip.
    ok(json!({"ok": true, "note": "send-only", "latency_ms": Value::Null}))
}

// --- persistence -------------------------------------------------------------

async fn save(State(app): State<App>) -> Response {
    let mut st = lock(&app.state);
    let Some(path) = st.show_path.clone() else { return err("No file yet — use Save as.") };
    match show::save_show(&path, &st.show) {
        Ok(doc) => {
            st.show.meta.saved_at = doc.meta.saved_at;
            st.dirty = false;
            ok(json!({"ok": true, "file": path.file_name().map(|n| n.to_string_lossy())}))
        }
        Err(e) => err(e),
    }
}

async fn save_as(State(app): State<App>, body: Bytes) -> Response {
    let raw = body_of(&body).get("name").and_then(Value::as_str).unwrap_or("").to_string();
    let mut name = sanitize_name(&raw);
    if !name.ends_with(".json") {
        name.push_str(".json");
    }
    let path = app.cfg.shows_dir().join(&name);
    let mut st = lock(&app.state);
    match show::save_show(&path, &st.show) {
        Ok(doc) => {
            st.show.meta.saved_at = doc.meta.saved_at;
            st.show_path = Some(path);
            st.dirty = false;
            st.remember_show_path();
            ok(json!({"ok": true, "file": name}))
        }
        Err(e) => err(e),
    }
}

async fn load_show_route(State(app): State<App>, body: Bytes) -> Response {
    let raw = body_of(&body).get("name").and_then(Value::as_str).unwrap_or("").to_string();
    // Take the file name only: no path traversal out of the shows directory.
    let Some(file) = std::path::Path::new(&raw).file_name() else {
        return err("No such show.");
    };
    let path = app.cfg.shows_dir().join(file);
    let doc = match show::load_show(&path) {
        Ok(d) => d,
        Err(e) => return err(e),
    };
    let mut st = lock(&app.state);
    st.armed = false; // DISARMED after any show load or import (PRD §10)
    st.calibrate.clear();
    st.show = doc;
    st.show_path = Some(path);
    st.dirty = false;
    st.remember_show_path();
    ok(json!({"ok": true, "file": file.to_string_lossy()}))
}

async fn list_shows(State(app): State<App>) -> Response {
    let dir = app.cfg.shows_dir();
    let mut files: Vec<String> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".json"))
        .collect();
    files.sort();
    let current = lock(&app.state)
        .show_path
        .as_ref()
        .and_then(|p| p.file_name())
        .map(|n| n.to_string_lossy().into_owned());
    ok(json!({"ok": true, "shows": files, "current": current}))
}

async fn export(State(app): State<App>) -> Response {
    let st = lock(&app.state);
    let name = st.show_path.as_ref().and_then(|p| p.file_name()).map_or_else(
        || format!("{}.json", sanitize_name(&st.show.meta.name)),
        |n| n.to_string_lossy().into_owned(),
    );
    let body = serde_json::to_string_pretty(&st.show).unwrap_or_else(|_| "{}".into());
    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/json"));
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{name}\"")) {
        headers.insert(header::CONTENT_DISPOSITION, v);
    }
    (headers, body).into_response()
}

async fn import_show(State(app): State<App>, body: Bytes) -> Response {
    let Ok(raw) = serde_json::from_slice::<Value>(&body) else { return err("Not a JSON file.") };
    let doc = match show::normalize(&raw) {
        Ok(d) => d,
        Err(e) => return err(e),
    };
    let mut st = lock(&app.state);
    st.armed = false;
    st.calibrate.clear();
    st.show = doc;
    st.show_path = None; // imported: the operator names it with Save as
    st.mark_dirty();
    ok(json!({"ok": true}))
}

async fn set_meta(State(app): State<App>, body: Bytes) -> Response {
    let d = body_of(&body);
    let mut st = lock(&app.state);
    if let Some(name) = d.get("name").and_then(Value::as_str).filter(|s| !s.is_empty()) {
        st.show.meta.name = name.to_string();
    }
    if let Some(notes) = d.get("notes").and_then(Value::as_str) {
        st.show.meta.notes = notes.to_string();
    }
    st.mark_dirty();
    ok(json!({"ok": true}))
}

// --- SSE snapshot stream -----------------------------------------------------

async fn stream(State(app): State<App>) -> Sse<impl futures_util::Stream<Item = Result<Event, Infallible>>> {
    let period = Duration::from_secs_f64(1.0 / SNAPSHOT_HZ);
    let s = futures_util::stream::unfold((app, true), move |(app, first)| async move {
        if !first {
            tokio::time::sleep(period).await;
        }
        let snap = lock(&app.state).snapshot();
        let data = serde_json::to_string(&snap).unwrap_or_else(|_| "{}".into());
        Some((Ok(Event::default().data(data)), (app, false)))
    });
    // The keepalive replaces the Python's manual 15 s ping comment.
    Sse::new(s).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)).text("ping"))
}

// --- embedded static UI ------------------------------------------------------

fn content_type(path: &str) -> &'static str {
    match path.rsplit('.').next() {
        Some("html") => "text/html; charset=utf-8",
        Some("js") => "text/javascript; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("json") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("ico") => "image/x-icon",
        _ => "application/octet-stream",
    }
}

async fn static_files(uri: axum::http::Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    let Some(file) = WEB.get_file(path).or_else(|| WEB.get_file("index.html")) else {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    };
    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type(file.path().to_str().unwrap_or(""))));
    // Revalidate every time: the Python defeated stale bundle caching the same
    // way, after a stale app.js cost a debugging session.
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    (headers, file.contents().to_vec()).into_response()
}
