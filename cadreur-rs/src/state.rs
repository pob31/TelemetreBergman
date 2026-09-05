//! Shared runtime state — the single source of truth.
//!
//! Ported from `src/cadreur/state.py`. The telemetre task writes the distance;
//! the web layer and the engine read snapshots and edit the show. The Python
//! guarded only the cross-thread distance fields, relying on the asyncio loop
//! to serialise the rest; tokio tasks can genuinely run in parallel, so here a
//! single mutex guards everything. Critical sections stay short — no await is
//! ever held across the lock.
//!
//! Armed is runtime-only and always starts false; it is never persisted
//! (PRD §10). `cadreur_state.json` remembers only the last-opened show path.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::{Config, data_dir};
use crate::interp::{Trim, Values, round_dp};
use crate::show::{self, BeamerKey, Channel, Show};

pub const STATE_FILE: &str = "cadreur_state.json";

/// Seconds since process start. The Python used `time.monotonic()`; this has
/// the same guarantee (never goes backwards) and the same units.
pub fn monotonic() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Live,
    Stale,
    Disconnected,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Manual {
    pub scale: f64,
    pub pos_v: f64,
    pub pos_h: f64,
}

impl Default for Manual {
    fn default() -> Self {
        // 0.5 = centred, in the normalised 0..1 space Millumin is mapped to.
        Self { scale: 0.5, pos_v: 0.5, pos_h: 0.5 }
    }
}

/// Written by the engine each tick, read by the UI.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ChannelRuntime {
    pub gate: bool,
    pub reason: Option<String>,
    pub clamped: Option<String>,
    pub values: Option<Values>,
    pub sending: bool,
    pub n_points: usize,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MilluminStatus {
    pub ok: Option<bool>,
    pub latency_ms: Option<f64>,
    pub warning: Option<String>,
}

// --- the snapshot the UI consumes over SSE -----------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct DistanceView {
    pub abs_m: Option<f64>,
    pub abs_m_raw: Option<f64>,
    pub position_m: Option<f64>,
    pub source: Source,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShowView {
    pub name: String,
    pub notes: String,
    pub saved_at: Option<String>,
    pub file: Option<String>,
    pub dirty: bool,
    pub autosave: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ChannelView {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    pub osc_scale: String,
    pub osc_posv: String,
    pub osc_posh: String,
    pub osc_show: String,
    pub cal_key: String,
    pub points: Vec<crate::interp::Point>,
    pub trim: Trim,
    pub calibrating: bool,
    pub manual: Manual,
    pub reason: Option<String>,
    pub gate: bool,
    pub clamped: Option<String>,
    pub values: Option<Values>,
    pub sending: bool,
    pub n_points: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub distance: DistanceView,
    pub armed: bool,
    pub settings: crate::show::Settings,
    pub lens_memories: Vec<String>,
    pub smoothing: crate::show::Smoothing,
    pub beamers: BeamerViews,
    pub show: ShowView,
    pub millumin: MilluminStatus,
}

#[derive(Debug, Clone, Serialize)]
pub struct BeamerViews {
    pub front: Vec<ChannelView>,
    pub rear: Vec<ChannelView>,
}

// --- state -------------------------------------------------------------------

pub struct State {
    pub cfg: Config,

    // distance, written by the telemetre task
    abs_m: Option<f64>,
    abs_m_raw: Option<f64>,
    position_m: Option<f64>,
    pi: Value,
    sse_connected: bool,
    last_usable: f64,
    ever_usable: bool,

    // show
    pub show: Show,
    pub show_path: Option<PathBuf>,
    pub dirty: bool,
    dirty_since: Option<f64>,
    pub last_autosave: Option<f64>,

    // runtime controls, keyed "{beamer}/{cid}"
    pub armed: bool,
    pub calibrate: HashSet<String>,
    pub manual: HashMap<String, Manual>,

    // written by the engine, read by the UI
    pub channels_state: HashMap<String, ChannelRuntime>,
    pub millumin: MilluminStatus,

    state_path: PathBuf,
}

impl State {
    pub fn new(cfg: Config) -> Self {
        Self {
            cfg,
            abs_m: None,
            abs_m_raw: None,
            position_m: None,
            pi: json!({}),
            sse_connected: false,
            last_usable: 0.0,
            ever_usable: false,
            show: Show::new("Nouveau spectacle"),
            show_path: None,
            dirty: false,
            dirty_since: None,
            last_autosave: None,
            armed: false,
            calibrate: HashSet::new(),
            manual: HashMap::new(),
            channels_state: HashMap::new(),
            millumin: MilluminStatus::default(),
            state_path: data_dir().join(STATE_FILE),
        }
    }

    pub fn chan_key(b: BeamerKey, cid: &str) -> String {
        format!("{}/{cid}", b.as_str())
    }

    pub fn manual_of(&mut self, key: &str) -> Manual {
        *self.manual.entry(key.to_string()).or_default()
    }

    // --- cadreur_state.json (last-opened show only) --------------------------

    /// Never lets a bad state file stop startup.
    pub fn load_last_show_path(&self) -> Option<PathBuf> {
        let text = std::fs::read_to_string(&self.state_path).ok()?;
        let d: Value = serde_json::from_str(&text)
            .map_err(|e| eprintln!("Ignoring unreadable {}: {e}", self.state_path.display()))
            .ok()?;
        d.get("last_show").and_then(Value::as_str).filter(|s| !s.is_empty()).map(PathBuf::from)
    }

    pub fn remember_show_path(&self) {
        let body = json!({
            "last_show": self.show_path.as_ref().map(|p| p.to_string_lossy().into_owned()),
        });
        if let Some(parent) = self.state_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Err(e) = std::fs::write(&self.state_path, body.to_string()) {
            eprintln!("Could not persist {}: {e}", self.state_path.display());
        }
    }

    // --- distance writes -----------------------------------------------------

    pub fn sse_status(&mut self, connected: bool) {
        self.sse_connected = connected;
    }

    pub fn update_distance(
        &mut self,
        abs_m_smoothed: f64,
        abs_m_raw: f64,
        position_m: Option<f64>,
        payload: Value,
        now: f64,
    ) {
        self.abs_m = Some(abs_m_smoothed);
        self.abs_m_raw = Some(abs_m_raw);
        self.position_m = position_m;
        self.pi = payload;
        self.last_usable = now;
        self.ever_usable = true;
    }

    /// An SSE event arrived but is not usable (Pi stale/disconnected/null).
    /// Distance holds; the usable timestamp is deliberately NOT refreshed.
    pub fn note_unusable(&mut self, payload: Value) {
        self.pi = payload;
    }

    // --- distance reads ------------------------------------------------------

    pub fn source_state(&self, now: f64) -> Source {
        if !self.sse_connected {
            return Source::Disconnected;
        }
        let fresh = (now - self.last_usable) * 1000.0
            <= self.cfg.telemetre.stale_after_ms as f64;
        if self.ever_usable && fresh { Source::Live } else { Source::Stale }
    }

    /// The engine's input: (smoothed abs_m, ever_usable).
    pub fn distance(&self) -> (Option<f64>, bool) {
        (self.abs_m, self.ever_usable)
    }

    // --- show edits ----------------------------------------------------------

    pub fn mark_dirty(&mut self) {
        self.dirty = true;
        self.dirty_since = Some(monotonic());
    }

    /// Debounced: saves `autosave_debounce_s` after the LAST edit.
    pub fn maybe_autosave(&mut self, now: f64) -> bool {
        if !(self.dirty && self.cfg.shows.autosave && self.show_path.is_some()) {
            return false;
        }
        let Some(since) = self.dirty_since else { return false };
        if now - since < self.cfg.shows.autosave_debounce_s {
            return false;
        }
        let path = self.show_path.clone().expect("checked above");
        match show::save_show(&path, &self.show) {
            Ok(doc) => {
                self.show.meta.saved_at = doc.meta.saved_at;
                self.dirty = false;
                self.dirty_since = None;
                self.last_autosave = Some(now);
                true
            }
            Err(e) => {
                // A failed autosave must never crash the engine.
                eprintln!("Autosave failed: {e}");
                self.dirty_since = Some(now); // retry after another debounce
                false
            }
        }
    }

    // --- views ---------------------------------------------------------------

    fn channel_view(&self, b: BeamerKey, ch: &Channel) -> ChannelView {
        let key = Self::chan_key(b, &ch.id);
        let cset = self.show.cal_set_for(b, ch);
        let rt = self.channels_state.get(&key);
        ChannelView {
            id: ch.id.clone(),
            name: ch.name.clone(),
            enabled: ch.enabled,
            osc_scale: ch.osc_scale.clone(),
            osc_posv: ch.osc_posv.clone(),
            osc_posh: ch.osc_posh.clone(),
            osc_show: ch.osc_show.clone(),
            cal_key: self.show.cal_key_for(b).to_string(),
            points: cset.map(|c| c.points.clone()).unwrap_or_default(),
            trim: cset.map_or_else(Trim::default, |c| c.trim),
            calibrating: self.calibrate.contains(&key),
            manual: self.manual.get(&key).copied().unwrap_or_default(),
            reason: rt.and_then(|r| r.reason.clone()),
            gate: rt.is_some_and(|r| r.gate),
            clamped: rt.and_then(|r| r.clamped.clone()),
            values: rt.and_then(|r| r.values),
            sending: rt.is_some_and(|r| r.sending),
            n_points: cset.map_or(0, |c| c.points.len()),
        }
    }

    pub fn snapshot(&self) -> Snapshot {
        let now = monotonic();
        Snapshot {
            distance: DistanceView {
                abs_m: self.abs_m.map(|x| round_dp(x, 4)),
                abs_m_raw: self.abs_m_raw.map(|x| round_dp(x, 4)),
                position_m: self.position_m,
                source: self.source_state(now),
            },
            armed: self.armed,
            settings: self.show.settings.clone(),
            lens_memories: self.show.lens_memories.clone(),
            smoothing: self.show.smoothing,
            beamers: BeamerViews {
                front: self
                    .show
                    .channels(BeamerKey::Front)
                    .iter()
                    .map(|ch| self.channel_view(BeamerKey::Front, ch))
                    .collect(),
                rear: self
                    .show
                    .channels(BeamerKey::Rear)
                    .iter()
                    .map(|ch| self.channel_view(BeamerKey::Rear, ch))
                    .collect(),
            },
            show: ShowView {
                name: self.show.meta.name.clone(),
                notes: self.show.meta.notes.clone(),
                saved_at: self.show.meta.saved_at.clone(),
                file: self
                    .show_path
                    .as_ref()
                    .and_then(|p| p.file_name())
                    .map(|n| n.to_string_lossy().into_owned()),
                dirty: self.dirty,
                autosave: self.cfg.shows.autosave,
            },
            millumin: self.millumin.clone(),
        }
    }

    pub fn health(&self) -> Value {
        json!({
            "status": "ok",
            "source": self.source_state(monotonic()),
            "armed": self.armed,
        })
    }

    #[cfg(test)]
    pub fn set_state_path(&mut self, p: PathBuf) {
        self.state_path = p;
    }
}

/// The handle every task shares.
pub type Shared = std::sync::Arc<Mutex<State>>;

pub fn shared(cfg: Config) -> Shared {
    std::sync::Arc::new(Mutex::new(State::new(cfg)))
}

/// Convenience: lock, recovering from a poisoned mutex rather than panicking.
/// A panicked tick must not take the show down with it.
pub fn lock(s: &Shared) -> std::sync::MutexGuard<'_, State> {
    s.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn st() -> State {
        State::new(Config::default())
    }

    #[test]
    fn armed_starts_false_and_is_never_in_the_show() {
        let s = st();
        assert!(!s.armed);
        assert!(s.show.to_value().get("armed").is_none());
    }

    #[test]
    fn source_is_disconnected_until_the_stream_connects() {
        let mut s = st();
        assert_eq!(s.source_state(monotonic()), Source::Disconnected);
        s.sse_status(true);
        // connected but nothing usable yet
        assert_eq!(s.source_state(monotonic()), Source::Stale);
    }

    #[test]
    fn source_goes_live_then_stale_on_the_configured_timeout() {
        let mut s = st();
        s.sse_status(true);
        s.update_distance(3.0, 3.0, Some(1.0), json!({}), 100.0);
        assert_eq!(s.source_state(100.2), Source::Live);
        // stale_after_ms defaults to 1500
        assert_eq!(s.source_state(101.6), Source::Stale);
    }

    #[test]
    fn unusable_payload_holds_the_distance() {
        let mut s = st();
        s.sse_status(true);
        s.update_distance(3.0, 3.0, Some(1.0), json!({}), 100.0);
        s.note_unusable(json!({"stale": true}));
        assert_eq!(s.distance(), (Some(3.0), true));
        // the usable timestamp was not refreshed, so it ages into stale
        assert_eq!(s.source_state(101.6), Source::Stale);
    }

    #[test]
    fn manual_defaults_to_centred() {
        let mut s = st();
        let m = s.manual_of("front/front-1");
        assert_eq!(m, Manual { scale: 0.5, pos_v: 0.5, pos_h: 0.5 });
    }

    #[test]
    fn autosave_is_debounced_and_needs_a_path() {
        let mut s = st();
        s.mark_dirty();
        assert!(!s.maybe_autosave(monotonic()), "no path yet");

        let dir = std::env::temp_dir().join(format!("cadreur-state-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        s.show_path = Some(dir.join("s.json"));
        s.dirty_since = Some(0.0);
        assert!(!s.maybe_autosave(1.0), "still inside the debounce window");
        assert!(s.maybe_autosave(99.0), "past the debounce window");
        assert!(!s.dirty);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn state_file_round_trips_and_tolerates_garbage() {
        let dir = std::env::temp_dir().join(format!("cadreur-sf-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let mut s = st();
        s.set_state_path(dir.join(STATE_FILE));

        assert!(s.load_last_show_path().is_none(), "missing file is not an error");
        s.show_path = Some(PathBuf::from("/tmp/x/show.json"));
        s.remember_show_path();
        assert_eq!(s.load_last_show_path(), Some(PathBuf::from("/tmp/x/show.json")));

        std::fs::write(dir.join(STATE_FILE), "{truncated").expect("write");
        assert!(s.load_last_show_path().is_none(), "garbage must not stop startup");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn snapshot_shape_matches_what_the_ui_reads() {
        let s = st();
        let v = serde_json::to_value(s.snapshot()).expect("serializes");
        for key in
            ["distance", "armed", "settings", "lens_memories", "smoothing", "beamers", "show", "millumin"]
        {
            assert!(v.get(key).is_some(), "snapshot missing {key}");
        }
        assert_eq!(v["distance"]["source"], "disconnected");
        assert_eq!(v["beamers"]["front"].as_array().expect("array").len(), 4);
        let ch = &v["beamers"]["front"][0];
        for key in ["id", "name", "enabled", "osc_scale", "cal_key", "points", "trim", "manual", "n_points"]
        {
            assert!(ch.get(key).is_some(), "channel view missing {key}");
        }
        assert_eq!(ch["name"], "Face 1");
    }
}
