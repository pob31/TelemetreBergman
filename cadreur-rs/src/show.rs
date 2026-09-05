//! Show file: schema, load/save, migration.
//!
//! Ported from `src/cadreur/show.py`. Schema v2 has no Looks: each beamer
//! (front/rear) carries a flat list of **channels** — one per Millumin layer —
//! driven continuously and simultaneously. Each channel has its own OSC
//! addresses (all normalised 0..1) and its own calibration, keyed by lens
//! memory on the front and by the reserved `default` key on the rear.
//!
//! One JSON file = one show. Armed is NEVER persisted. Loads are defensive:
//! unknown keys ignored, points sorted and deduped, bad references repaired.
//! A v1 (Looks) file is migrated — the active look becomes channel 1.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use time::OffsetDateTime;
use time::format_description::FormatItem;
use time::macros::format_description;

use crate::interp::{Point, Trim, normalize_points};

pub const VERSION: u32 = 2;
/// The rear has no lens memories: one calibration per channel.
pub const REAR_CAL_KEY: &str = "default";
/// 4 front + 4 rear, per the show design.
pub const DEFAULT_CHANNELS: usize = 4;
pub const DEFAULT_LENS_MEMORIES: [&str; 3] = ["M1", "M2", "M3"];
/// `osc_show` is a one-shot "reveal this layer in Millumin" trigger, so the
/// operator can find the layer being calibrated from the stage.
pub const OSC_KEYS: [&str; 4] = ["osc_scale", "osc_posv", "osc_posh", "osc_show"];

const ISO: &[FormatItem] =
    format_description!("[year]-[month]-[day]T[hour]:[minute]:[second]Z");
const STAMP: &[FormatItem] =
    format_description!("[year][month][day]-[hour][minute][second]");

/// A show file we refuse to load or apply, with an operator-readable reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShowError(pub String);

impl fmt::Display for ShowError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ShowError {}

fn err<T>(msg: impl Into<String>) -> Result<T, ShowError> {
    Err(ShowError(msg.into()))
}

// --- beamers -----------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BeamerKey {
    Front,
    Rear,
}

impl BeamerKey {
    pub const ALL: [BeamerKey; 2] = [BeamerKey::Front, BeamerKey::Rear];

    pub fn as_str(self) -> &'static str {
        match self {
            BeamerKey::Front => "front",
            BeamerKey::Rear => "rear",
        }
    }

    /// The rear beamer addresses as `retro` — the name the operator uses.
    fn osc_prefix(self) -> &'static str {
        match self {
            BeamerKey::Front => "front",
            BeamerKey::Rear => "retro",
        }
    }

    fn channel_name(self) -> &'static str {
        match self {
            BeamerKey::Front => "Face",
            BeamerKey::Rear => "Lointain",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "front" => Some(BeamerKey::Front),
            "rear" => Some(BeamerKey::Rear),
            _ => None,
        }
    }
}

// --- schema ------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CalSet {
    pub interp: String,
    pub trim: Trim,
    pub points: Vec<Point>,
}

impl Default for CalSet {
    fn default() -> Self {
        Self { interp: "linear".into(), trim: Trim::default(), points: Vec::new() }
    }
}

/// Field order matches the Python `normalize()` output so a file re-saved by
/// this build diffs cleanly against one written by the Python.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Channel {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    /// Keyed by lens memory (front) or the reserved `default` (rear).
    /// A `BTreeMap` gives deterministic output; the Python preserved insertion
    /// order, which made re-saves depend on the order they happened to load in.
    pub calibrations: BTreeMap<String, CalSet>,
    pub osc_scale: String,
    pub osc_posv: String,
    pub osc_posh: String,
    pub osc_show: String,
}

impl Channel {
    pub fn new(beamer: BeamerKey, index: usize) -> Self {
        let p = beamer.osc_prefix();
        Self {
            id: format!("{}-{index}", beamer.as_str()),
            name: format!("{} {index}", beamer.channel_name()),
            enabled: true,
            calibrations: BTreeMap::new(),
            osc_scale: format!("/{p}/scale/{index}"),
            osc_posv: format!("/{p}/positionV/{index}"),
            osc_posh: format!("/{p}/positionH/{index}"),
            osc_show: format!("/{p}/layer/{index}"),
        }
    }

    pub fn osc(&self, key: &str) -> Option<&str> {
        match key {
            "osc_scale" => Some(&self.osc_scale),
            "osc_posv" => Some(&self.osc_posv),
            "osc_posh" => Some(&self.osc_posh),
            "osc_show" => Some(&self.osc_show),
            _ => None,
        }
    }

    fn set_osc(&mut self, key: &str, addr: String) {
        match key {
            "osc_scale" => self.osc_scale = addr,
            "osc_posv" => self.osc_posv = addr,
            "osc_posh" => self.osc_posh = addr,
            "osc_show" => self.osc_show = addr,
            _ => {}
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Beamer {
    pub channels: Vec<Channel>,
}

impl Beamer {
    fn defaults(beamer: BeamerKey) -> Self {
        Self { channels: (1..=DEFAULT_CHANNELS).map(|i| Channel::new(beamer, i)).collect() }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Beamers {
    pub front: Beamer,
    pub rear: Beamer,
}

impl Beamers {
    pub fn get(&self, b: BeamerKey) -> &Beamer {
        match b {
            BeamerKey::Front => &self.front,
            BeamerKey::Rear => &self.rear,
        }
    }

    pub fn get_mut(&mut self, b: BeamerKey) -> &mut Beamer {
        match b {
            BeamerKey::Front => &mut self.front,
            BeamerKey::Rear => &mut self.rear,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Meta {
    pub name: String,
    pub saved_at: Option<String>,
    pub notes: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Settings {
    pub active_lens_memory: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Smoothing {
    pub ema_tau_s: f64,
    pub deadband_scale: f64,
    pub slew_scale_per_s: f64,
    pub refresh_hz: f64,
}

impl Default for Smoothing {
    fn default() -> Self {
        Self { ema_tau_s: 5.0, deadband_scale: 0.0005, slew_scale_per_s: 0.05, refresh_hz: 1.0 }
    }
}

/// Operator-tunable ranges (the Advanced drawer).
const SMOOTHING_LIMITS: [(&str, f64, f64); 4] = [
    ("ema_tau_s", 0.0, 30.0),
    ("deadband_scale", 0.0, 0.1),
    ("slew_scale_per_s", 0.001, 10.0),
    ("refresh_hz", 0.1, 20.0),
];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Show {
    pub app: String,
    pub version: u32,
    pub meta: Meta,
    pub settings: Settings,
    pub lens_memories: Vec<String>,
    pub smoothing: Smoothing,
    pub beamers: Beamers,
}

impl Show {
    pub fn new(name: &str) -> Self {
        Self {
            app: "cadreur".into(),
            version: VERSION,
            meta: Meta { name: name.into(), saved_at: None, notes: String::new() },
            settings: Settings { active_lens_memory: "M1".into() },
            lens_memories: DEFAULT_LENS_MEMORIES.iter().map(|s| (*s).into()).collect(),
            smoothing: Smoothing::default(),
            beamers: Beamers {
                front: Beamer::defaults(BeamerKey::Front),
                rear: Beamer::defaults(BeamerKey::Rear),
            },
        }
    }

    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).expect("Show always serializes")
    }

    pub fn channels(&self, b: BeamerKey) -> &[Channel] {
        &self.beamers.get(b).channels
    }

    pub fn channel(&self, b: BeamerKey, cid: &str) -> Option<&Channel> {
        self.channels(b).iter().find(|c| c.id == cid)
    }

    pub fn channel_mut(&mut self, b: BeamerKey, cid: &str) -> Option<&mut Channel> {
        self.beamers.get_mut(b).channels.iter_mut().find(|c| c.id == cid)
    }

    /// Front resolves via the global active lens memory; rear uses the reserved
    /// `default` key — one uniform code path.
    pub fn cal_key_for(&self, b: BeamerKey) -> &str {
        match b {
            BeamerKey::Front => &self.settings.active_lens_memory,
            BeamerKey::Rear => REAR_CAL_KEY,
        }
    }

    /// The channel's active calibration set, or `None` (channel inhibited).
    /// Never falls back to another memory's set.
    pub fn cal_set_for<'c>(&self, b: BeamerKey, ch: &'c Channel) -> Option<&'c CalSet> {
        ch.calibrations.get(self.cal_key_for(b))
    }

    /// Get-or-create the channel's active set — capture creates it lazily.
    pub fn ensure_cal_set(&mut self, b: BeamerKey, cid: &str) -> Result<&mut CalSet, ShowError> {
        let key = self.cal_key_for(b).to_string();
        let ch = self
            .channel_mut(b, cid)
            .ok_or_else(|| ShowError(format!("Unknown channel '{cid}'.")))?;
        Ok(ch.calibrations.entry(key).or_default())
    }
}

// --- validators --------------------------------------------------------------

/// Spaces break OSC addresses, so layer names are restricted.
pub fn valid_layer_name(name: &str) -> bool {
    !name.is_empty()
        && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

pub fn valid_osc_addr(addr: &str) -> bool {
    let Some(rest) = addr.strip_prefix('/') else { return false };
    !rest.is_empty()
        && rest
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '/' | '-'))
}

pub fn unique_id(base: &str, taken: &[String]) -> String {
    if !taken.iter().any(|t| t == base) {
        return base.to_string();
    }
    let mut n = 2;
    loop {
        let candidate = format!("{base}-{n}");
        if !taken.contains(&candidate) {
            return candidate;
        }
        n += 1;
    }
}

// --- normalization -----------------------------------------------------------

fn as_f64(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    }
}

fn norm_trim(raw: Option<&Value>) -> Trim {
    let d = Trim::default();
    let Some(o) = raw.and_then(Value::as_object) else { return d };
    Trim {
        scale_mul: as_f64(o.get("scale_mul")).unwrap_or(d.scale_mul),
        dx_px: as_f64(o.get("dx_px")).unwrap_or(d.dx_px),
        dy_px: as_f64(o.get("dy_px")).unwrap_or(d.dy_px),
    }
}

fn norm_cal_set(raw: Option<&Value>) -> CalSet {
    let empty = serde_json::Map::new();
    let o = raw.and_then(Value::as_object).unwrap_or(&empty);
    let points = o.get("points").and_then(Value::as_array).map_or_else(Vec::new, |a| {
        normalize_points(a)
    });
    CalSet {
        // "linear" is the only interpolation this build knows.
        interp: "linear".into(),
        trim: norm_trim(o.get("trim")),
        points,
    }
}

fn norm_channel(raw: Option<&Value>, beamer: BeamerKey, index: usize, taken: &[String]) -> Channel {
    let empty = serde_json::Map::new();
    let o = raw.and_then(Value::as_object).unwrap_or(&empty);
    let mut d = Channel::new(beamer, index);

    let cid = o.get("id").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or(&d.id);
    d.id = unique_id(cid, taken);
    if let Some(name) = o.get("name").and_then(Value::as_str).filter(|s| !s.is_empty()) {
        d.name = name.to_string();
    }
    d.enabled = o.get("enabled").is_none_or(|v| match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64().is_some_and(|x| x != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    });
    if let Some(cals) = o.get("calibrations").and_then(Value::as_object) {
        d.calibrations =
            cals.iter().map(|(k, v)| (k.clone(), norm_cal_set(Some(v)))).collect();
    }
    for key in OSC_KEYS {
        if let Some(a) = o.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()) {
            d.set_osc(key, a.to_string());
        }
    }
    d
}

fn norm_beamer(raw: Option<&Value>, beamer: BeamerKey) -> Beamer {
    let raw_channels =
        raw.and_then(Value::as_object).and_then(|o| o.get("channels")).and_then(Value::as_array);
    let mut channels: Vec<Channel> = Vec::new();
    let mut taken: Vec<String> = Vec::new();
    for (i, rc) in raw_channels.into_iter().flatten().enumerate() {
        let ch = norm_channel(Some(rc), beamer, i + 1, &taken);
        taken.push(ch.id.clone());
        channels.push(ch);
    }
    if channels.is_empty() {
        // A beamer always has at least the default set of channels.
        return Beamer::defaults(beamer);
    }
    Beamer { channels }
}

fn norm_smoothing(raw: Option<&Value>) -> Smoothing {
    let d = Smoothing::default();
    let Some(o) = raw.and_then(Value::as_object) else { return d };
    let mut out = d;
    for (key, lo, hi) in SMOOTHING_LIMITS {
        let current = match key {
            "ema_tau_s" => &mut out.ema_tau_s,
            "deadband_scale" => &mut out.deadband_scale,
            "slew_scale_per_s" => &mut out.slew_scale_per_s,
            _ => &mut out.refresh_hz,
        };
        if let Some(x) = as_f64(o.get(key)) {
            *current = x.clamp(lo, hi);
        }
    }
    out
}

/// v1 (Looks) -> v2 (channels). The active look's front/rear beamer becomes
/// channel 1 of each, OSC addresses and calibrations preserved; channels 2..N
/// are fresh. Other looks are dropped.
fn migrate_v1(data: &Value) -> Value {
    let looks = data.get("looks").and_then(Value::as_array);
    let settings = data.get("settings");
    let active_id = settings.and_then(|s| s.get("active_look")).and_then(Value::as_str);

    let src = looks.and_then(|ls| {
        ls.iter()
            .find(|lk| lk.get("id").and_then(Value::as_str) == active_id.filter(|_| true))
            .or_else(|| ls.first())
    });
    let src_beamers = src.and_then(|s| s.get("beamers"));

    let mut beamers = serde_json::Map::new();
    for b in BeamerKey::ALL {
        let mut chans: Vec<Value> = (1..=DEFAULT_CHANNELS)
            .map(|i| serde_json::to_value(Channel::new(b, i)).expect("channel serializes"))
            .collect();
        if let Some(old) = src_beamers.and_then(|sb| sb.get(b.as_str())).and_then(Value::as_object)
        {
            // Carry the old single beamer into channel 1.
            let ch1 = chans[0].as_object_mut().expect("object");
            if let Some(cals) = old.get("calibrations") {
                ch1.insert("calibrations".into(), cals.clone());
            }
            for key in OSC_KEYS {
                if let Some(a) = old.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()) {
                    ch1.insert(key.into(), json!(a));
                }
            }
        }
        beamers.insert(b.as_str().into(), json!({ "channels": chans }));
    }

    json!({
        "app": "cadreur",
        "version": VERSION,
        "meta": data.get("meta").cloned().unwrap_or_else(|| json!({})),
        "settings": {
            "active_lens_memory": settings
                .and_then(|s| s.get("active_lens_memory"))
                .and_then(Value::as_str)
                .unwrap_or("M1"),
        },
        "lens_memories": data.get("lens_memories").cloned().unwrap_or(Value::Null),
        "smoothing": data.get("smoothing").cloned().unwrap_or_else(|| json!({})),
        "beamers": beamers,
    })
}

/// Validated copy holding only the known schema. Errors on a missing or newer
/// version; a v1 document is migrated; recoverable issues are repaired silently.
pub fn normalize(data: &Value) -> Result<Show, ShowError> {
    if !data.is_object() {
        return err("Not a Cadreur show file (expected a JSON object).");
    }
    // A JSON bool is not an integer version — Python guarded this explicitly
    // because bool is a subclass of int there.
    let v = match data.get("version") {
        Some(Value::Number(n)) if n.is_i64() || n.is_u64() => n.as_u64().unwrap_or(0),
        _ => return err("No schema version — not a Cadreur show file."),
    };
    if v > u64::from(VERSION) {
        return err(format!(
            "Show file version {v} was made by a newer Cadreur (this build reads v{VERSION})."
        ));
    }
    let migrated;
    let data = if v < 2 {
        migrated = migrate_v1(data);
        &migrated
    } else {
        data
    };

    let meta_raw = data.get("meta").and_then(Value::as_object);
    let settings_raw = data.get("settings").and_then(Value::as_object);

    let mut memories: Vec<String> = data
        .get("lens_memories")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|m| match m {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                })
                .filter(|s| !s.trim().is_empty())
                .collect()
        })
        .unwrap_or_default();
    if memories.is_empty() {
        memories = DEFAULT_LENS_MEMORIES.iter().map(|s| (*s).into()).collect();
    }

    let raw_beamers = data.get("beamers");
    let beamers = Beamers {
        front: norm_beamer(raw_beamers.and_then(|b| b.get("front")), BeamerKey::Front),
        rear: norm_beamer(raw_beamers.and_then(|b| b.get("rear")), BeamerKey::Rear),
    };

    let active_mem = settings_raw
        .and_then(|s| s.get("active_lens_memory"))
        .and_then(Value::as_str)
        .filter(|m| memories.iter().any(|x| x == m))
        .map_or_else(|| memories[0].clone(), str::to_string);

    Ok(Show {
        app: "cadreur".into(),
        version: VERSION,
        meta: Meta {
            name: meta_raw
                .and_then(|m| m.get("name"))
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or("Sans titre")
                .to_string(),
            saved_at: meta_raw
                .and_then(|m| m.get("saved_at"))
                .and_then(Value::as_str)
                .map(str::to_string),
            notes: meta_raw
                .and_then(|m| m.get("notes"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        },
        settings: Settings { active_lens_memory: active_mem },
        lens_memories: memories,
        smoothing: norm_smoothing(data.get("smoothing")),
        beamers,
    })
}

// --- channel operations ------------------------------------------------------

/// Lowest positive index not already used by a channel's scale address.
fn next_osc_index(show: &Show, b: BeamerKey) -> usize {
    let prefix = format!("/{}/scale/", b.osc_prefix());
    let used: Vec<usize> = show
        .channels(b)
        .iter()
        .filter_map(|ch| ch.osc_scale.strip_prefix(&prefix))
        .filter(|rest| !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()))
        .filter_map(|rest| rest.parse::<usize>().ok())
        .collect();
    (1..).find(|i| !used.contains(i)).expect("an unused index always exists")
}

pub fn add_channel(show: &mut Show, b: BeamerKey, name: Option<&str>) -> Channel {
    let idx = next_osc_index(show, b);
    let mut ch = Channel::new(b, idx);
    let taken: Vec<String> = show.channels(b).iter().map(|c| c.id.clone()).collect();
    ch.id = unique_id(&ch.id, &taken);
    if let Some(n) = name.filter(|s| !s.is_empty()) {
        ch.name = n.to_string();
    }
    show.beamers.get_mut(b).channels.push(ch.clone());
    ch
}

pub fn delete_channel(show: &mut Show, b: BeamerKey, cid: &str) -> Result<(), ShowError> {
    let chans = &mut show.beamers.get_mut(b).channels;
    let Some(i) = chans.iter().position(|c| c.id == cid) else {
        return err(format!("Unknown channel '{cid}'."));
    };
    if chans.len() <= 1 {
        return err("Cannot delete the last channel of a beamer.");
    }
    chans.remove(i);
    Ok(())
}

pub fn rename_channel(show: &mut Show, b: BeamerKey, cid: &str, name: &str) -> Result<(), ShowError> {
    let ch = show
        .channel_mut(b, cid)
        .ok_or_else(|| ShowError(format!("Unknown channel '{cid}'.")))?;
    if !name.is_empty() {
        ch.name = name.to_string();
    }
    Ok(())
}

pub fn set_channel_osc(
    show: &mut Show,
    b: BeamerKey,
    cid: &str,
    addrs: &serde_json::Map<String, Value>,
) -> Result<(), ShowError> {
    // Validate everything before mutating, so a bad address can't leave the
    // channel half-updated.
    let mut pending: Vec<(&str, String)> = Vec::new();
    for key in OSC_KEYS {
        let Some(v) = addrs.get(key) else { continue };
        let a = v.as_str().map_or_else(|| v.to_string(), str::to_string);
        if !valid_osc_addr(&a) {
            return err(format!("Invalid OSC address for {key}: '{a}'."));
        }
        pending.push((key, a));
    }
    let ch = show
        .channel_mut(b, cid)
        .ok_or_else(|| ShowError(format!("Unknown channel '{cid}'.")))?;
    for (key, a) in pending {
        ch.set_osc(key, a);
    }
    Ok(())
}

// --- files -------------------------------------------------------------------

fn now_iso_utc() -> String {
    OffsetDateTime::now_utc().format(ISO).unwrap_or_default()
}

pub fn load_show(path: &Path) -> Result<Show, ShowError> {
    let name = path.file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned());
    let text = std::fs::read_to_string(path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            ShowError(format!("Show file not found: {name}"))
        } else {
            ShowError(format!("Unreadable show file {name}: {e}"))
        }
    })?;
    let raw: Value = serde_json::from_str(&text)
        .map_err(|e| ShowError(format!("Unreadable show file {name}: {e}")))?;
    normalize(&raw)
}

/// Atomic write (tmp + rename) of the known schema only; stamps `meta.saved_at`
/// and returns the document actually written.
pub fn save_show(path: &Path, show: &Show) -> Result<Show, ShowError> {
    let mut doc = show.clone();
    doc.meta.saved_at = Some(now_iso_utc());
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| ShowError(format!("Cannot create {}: {e}", parent.display())))?;
    }
    let body = serde_json::to_string_pretty(&doc)
        .map_err(|e| ShowError(format!("Cannot serialize show: {e}")))?
        + "\n";
    let mut tmp = path.as_os_str().to_os_string();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    std::fs::write(&tmp, body)
        .map_err(|e| ShowError(format!("Cannot write {}: {e}", tmp.display())))?;
    std::fs::rename(&tmp, path)
        .map_err(|e| ShowError(format!("Cannot replace {}: {e}", path.display())))?;
    Ok(doc)
}

/// Before loading on app start, copy the current file to
/// `shows/backups/<name>-<stamp>.json` and prune to the `keep` newest.
///
/// Unlike the Python original this refuses to rotate when the file it is about
/// to copy is not valid JSON: an unclean shutdown that truncates a show would
/// otherwise push the ten good backups out, one per relaunch, exactly when the
/// operator is restarting the app trying to recover.
pub fn startup_backup(path: &Path, keep: usize) -> Option<PathBuf> {
    if !path.exists() {
        return None;
    }
    let bytes = std::fs::read(path).ok()?;
    if serde_json::from_slice::<Value>(&bytes).is_err() {
        eprintln!(
            "Startup backup skipped: {} is not valid JSON — keeping existing backups intact",
            path.display()
        );
        return None;
    }
    let stem = path.file_stem()?.to_string_lossy().into_owned();
    let backups = path.parent()?.join("backups");
    std::fs::create_dir_all(&backups).ok()?;
    let stamp = OffsetDateTime::now_local()
        .unwrap_or_else(|_| OffsetDateTime::now_utc())
        .format(STAMP)
        .ok()?;
    let dest = backups.join(format!("{stem}-{stamp}.json"));
    std::fs::write(&dest, &bytes).ok()?;

    let prefix = format!("{stem}-");
    let mut old: Vec<PathBuf> = std::fs::read_dir(&backups)
        .ok()?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.extension().is_some_and(|x| x == "json")
                && p.file_name().is_some_and(|n| n.to_string_lossy().starts_with(&prefix))
        })
        .collect();
    old.sort();
    if old.len() > keep {
        for p in &old[..old.len() - keep] {
            let _ = std::fs::remove_file(p);
        }
    }
    Some(dest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    /// A scratch directory unique to this test binary and call site — the
    /// standard library has no tempdir, and pulling one in for four tests is
    /// not worth a dependency.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static N: AtomicU32 = AtomicU32::new(0);
            let d = std::env::temp_dir().join(format!(
                "cadreur-test-{}-{}",
                std::process::id(),
                N.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&d).expect("scratch dir");
            Self(d)
        }

        fn join(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn pt(d: f64, s: f64, x: f64, y: f64) -> Value {
        json!({"distance_m": d, "scale": s, "pos_x": x, "pos_y": y})
    }

    fn cal(points: Vec<Value>) -> Value {
        json!({
            "interp": "linear",
            "trim": {"scale_mul": 1.0, "dx_px": 0, "dy_px": 0},
            "points": points,
        })
    }

    #[test]
    fn new_show_shape() {
        let doc = Show::new("X");
        assert_eq!(doc.version, VERSION);
        assert_eq!(doc.channels(BeamerKey::Front).len(), DEFAULT_CHANNELS);
        assert_eq!(doc.channels(BeamerKey::Rear).len(), DEFAULT_CHANNELS);
        assert_eq!(doc.channels(BeamerKey::Front)[0].osc_scale, "/front/scale/1");
        assert_eq!(doc.channels(BeamerKey::Rear)[3].osc_posh, "/retro/positionH/4");
        assert_eq!(doc.channels(BeamerKey::Front)[0].osc_show, "/front/layer/1");
        assert_eq!(doc.channels(BeamerKey::Rear)[0].osc_show, "/retro/layer/1");
    }

    #[test]
    fn normalize_is_a_fixed_point() {
        let doc = normalize(&Show::new("Nouveau spectacle").to_value()).expect("normalizes");
        assert_eq!(normalize(&doc.to_value()).expect("re-normalizes"), doc);
    }

    #[test]
    fn version_missing_refused() {
        assert!(normalize(&json!({"beamers": {}})).is_err());
    }

    #[test]
    fn version_newer_refused() {
        let e = normalize(&json!({"version": VERSION + 1})).expect_err("must refuse");
        assert!(e.0.contains("newer Cadreur"), "{}", e.0);
    }

    #[test]
    fn version_bool_is_not_a_version() {
        // Python guarded this because bool subclasses int there.
        assert!(normalize(&json!({"version": true})).is_err());
    }

    #[test]
    fn unknown_keys_ignored() {
        let mut raw = Show::new("X").to_value();
        raw["mystery"] = json!(1);
        raw["beamers"]["front"]["channels"][0]["surprise"] = json!(2);
        let doc = normalize(&raw).expect("normalizes").to_value();
        assert!(doc.get("mystery").is_none());
        assert!(doc["beamers"]["front"]["channels"][0].get("surprise").is_none());
    }

    #[test]
    fn armed_is_never_serialized() {
        let mut raw = Show::new("X").to_value();
        raw["armed"] = json!(true);
        let doc = normalize(&raw).expect("normalizes").to_value();
        assert!(doc.get("armed").is_none());
    }

    #[test]
    fn defensive_point_sort() {
        let mut raw = Show::new("X").to_value();
        raw["beamers"]["front"]["channels"][0]["calibrations"]["M1"] =
            cal(vec![pt(4.0, 0.4, 0.5, 0.6), pt(2.0, 0.6, 0.5, 0.4)]);
        let doc = normalize(&raw).expect("normalizes");
        let pts = &doc.channels(BeamerKey::Front)[0].calibrations["M1"].points;
        let ds: Vec<f64> = pts.iter().map(|p| p.distance_m).collect();
        assert_eq!(ds, vec![2.0, 4.0]);
    }

    #[test]
    fn bad_active_memory_falls_back() {
        let mut raw = Show::new("X").to_value();
        raw["settings"]["active_lens_memory"] = json!("M9");
        assert_eq!(normalize(&raw).expect("normalizes").settings.active_lens_memory, "M1");
    }

    #[test]
    fn empty_channels_get_defaults() {
        let mut raw = Show::new("X").to_value();
        raw["beamers"]["front"]["channels"] = json!([]);
        let doc = normalize(&raw).expect("normalizes");
        assert_eq!(doc.channels(BeamerKey::Front).len(), DEFAULT_CHANNELS);
    }

    fn v1_doc() -> Value {
        json!({
            "app": "cadreur", "version": 1,
            "settings": {"active_look": "look-1", "active_lens_memory": "M2"},
            "lens_memories": ["M1", "M2", "M3"],
            "looks": [{"id": "look-1", "name": "L", "beamers": {
                "front": {"layer": "scope-front", "enabled": true,
                          "osc_scale": "/front/scale/1", "osc_posv": "/front/positionV/1",
                          "osc_posh": "/front/positionH/1",
                          "calibrations": {"M2": cal(vec![pt(2.0, 0.6, 0.5, 0.4), pt(4.0, 0.4, 0.5, 0.6)])}},
                "rear": {"layer": "scope-rear", "enabled": true,
                         "osc_scale": "/retro/scale/1", "osc_posv": "/retro/positionV/1",
                         "osc_posh": "/retro/positionH/1",
                         "calibrations": {"default": cal(vec![pt(3.0, 0.7, 0.5, 0.5)])}}}}],
        })
    }

    #[test]
    fn v1_migrates_preserving_channel_1() {
        let doc = normalize(&v1_doc()).expect("migrates");
        assert_eq!(doc.version, VERSION);
        assert!(doc.to_value().get("looks").is_none());
        assert_eq!(doc.settings.active_lens_memory, "M2");
        let f = doc.channels(BeamerKey::Front);
        assert_eq!(f.len(), DEFAULT_CHANNELS);
        assert_eq!(f[0].calibrations["M2"].points.len(), 2); // preserved into ch1
        let r = doc.channels(BeamerKey::Rear);
        assert_eq!(r[0].calibrations["default"].points.len(), 1);
    }

    fn show_with_front_m1() -> Show {
        let mut raw = Show::new("X").to_value();
        raw["beamers"]["front"]["channels"][0]["calibrations"]["M1"] =
            cal(vec![pt(2.0, 0.6, 0.5, 0.4)]);
        normalize(&raw).expect("normalizes")
    }

    #[test]
    fn front_resolves_active_memory() {
        let doc = show_with_front_m1();
        let f1 = &doc.channels(BeamerKey::Front)[0];
        assert!(doc.cal_set_for(BeamerKey::Front, f1).is_some());
    }

    #[test]
    fn missing_memory_inhibits_with_no_fallback() {
        let mut doc = show_with_front_m1();
        doc.settings.active_lens_memory = "M2".into();
        let f1 = &doc.channels(BeamerKey::Front)[0];
        assert!(doc.cal_set_for(BeamerKey::Front, f1).is_none());
    }

    #[test]
    fn rear_uses_the_default_key() {
        let doc = Show::new("X");
        assert_eq!(doc.cal_key_for(BeamerKey::Rear), "default");
        let r1 = &doc.channels(BeamerKey::Rear)[0];
        assert!(doc.cal_set_for(BeamerKey::Rear, r1).is_none()); // none captured yet
    }

    #[test]
    fn ensure_cal_set_creates_lazily() {
        let mut doc = Show::new("X");
        let cid = doc.channels(BeamerKey::Rear)[0].id.clone();
        let set = doc.ensure_cal_set(BeamerKey::Rear, &cid).expect("creates");
        assert!(set.points.is_empty());
        let r1 = &doc.channels(BeamerKey::Rear)[0];
        assert!(doc.cal_set_for(BeamerKey::Rear, r1).is_some());
    }

    #[test]
    fn add_channel_takes_the_next_index() {
        let mut doc = Show::new("X");
        let ch = add_channel(&mut doc, BeamerKey::Front, Some("Extra"));
        assert_eq!(ch.osc_scale, "/front/scale/5");
        assert_eq!(ch.name, "Extra");
        assert_eq!(doc.channels(BeamerKey::Front).len(), 5);
    }

    #[test]
    fn delete_channel_works() {
        let mut doc = Show::new("X");
        let cid = doc.channels(BeamerKey::Front)[0].id.clone();
        delete_channel(&mut doc, BeamerKey::Front, &cid).expect("deletes");
        assert_eq!(doc.channels(BeamerKey::Front).len(), 3);
    }

    #[test]
    fn deleting_the_last_channel_is_refused() {
        let mut doc = Show::new("X");
        let ids: Vec<String> =
            doc.channels(BeamerKey::Rear)[1..].iter().map(|c| c.id.clone()).collect();
        for cid in ids {
            delete_channel(&mut doc, BeamerKey::Rear, &cid).expect("deletes");
        }
        let last = doc.channels(BeamerKey::Rear)[0].id.clone();
        assert!(delete_channel(&mut doc, BeamerKey::Rear, &last).is_err());
    }

    #[test]
    fn rename_works() {
        let mut doc = Show::new("X");
        let cid = doc.channels(BeamerKey::Front)[0].id.clone();
        rename_channel(&mut doc, BeamerKey::Front, &cid, "Scope").expect("renames");
        assert_eq!(doc.channel(BeamerKey::Front, &cid).expect("exists").name, "Scope");
    }

    #[test]
    fn set_osc_accepts_valid_and_refuses_invalid() {
        let mut doc = Show::new("X");
        let cid = doc.channels(BeamerKey::Front)[0].id.clone();
        let good = json!({"osc_scale": "/front/scale/9"});
        set_channel_osc(&mut doc, BeamerKey::Front, &cid, good.as_object().expect("obj"))
            .expect("accepts");
        assert_eq!(doc.channel(BeamerKey::Front, &cid).expect("exists").osc_scale, "/front/scale/9");

        let bad = json!({"osc_scale": "bad addr"});
        assert!(
            set_channel_osc(&mut doc, BeamerKey::Front, &cid, bad.as_object().expect("obj"))
                .is_err()
        );
        // and the good value survived the rejected write
        assert_eq!(doc.channel(BeamerKey::Front, &cid).expect("exists").osc_scale, "/front/scale/9");
    }

    #[test]
    fn save_load_round_trip_is_atomic() {
        let dir = Scratch::new();
        let path = dir.join("show.json");
        let saved = save_show(&path, &Show::new("Test")).expect("saves");
        assert!(saved.meta.saved_at.is_some());
        assert!(!dir.join("show.json.tmp").exists(), "temp file left behind");
        assert_eq!(load_show(&path).expect("loads"), saved);
    }

    #[test]
    fn load_garbage_is_an_error() {
        let dir = Scratch::new();
        let path = dir.join("bad.json");
        std::fs::write(&path, "{not json").expect("writes");
        assert!(load_show(&path).is_err());
    }

    #[test]
    fn load_missing_names_the_file() {
        let dir = Scratch::new();
        let e = load_show(&dir.join("nope.json")).expect_err("must fail");
        assert!(e.0.contains("nope.json"), "{}", e.0);
    }

    #[test]
    fn validators() {
        assert!(valid_layer_name("scope-front"));
        assert!(!valid_layer_name("has space"));
        assert!(valid_osc_addr("/front/scale/1"));
        assert!(!valid_osc_addr("front/scale/1"));
        assert!(!valid_osc_addr("/"));
    }

    #[test]
    fn startup_backup_rotates_and_prunes() {
        let dir = Scratch::new();
        let path = dir.join("s.json");
        save_show(&path, &Show::new("T")).expect("saves");
        assert!(startup_backup(&path, 10).is_some());
        assert_eq!(std::fs::read_dir(dir.join("backups")).expect("dir").count(), 1);
    }

    #[test]
    fn startup_backup_refuses_to_rotate_a_broken_file() {
        // The Python original copied blindly, so each relaunch after an unclean
        // shutdown pushed one good backup out of the ten-deep window.
        let dir = Scratch::new();
        let path = dir.join("s.json");
        save_show(&path, &Show::new("T")).expect("saves");
        startup_backup(&path, 10).expect("first rotation");
        std::fs::write(&path, "{truncated").expect("corrupt it");
        assert!(startup_backup(&path, 10).is_none(), "must not rotate a broken file");
        assert_eq!(
            std::fs::read_dir(dir.join("backups")).expect("dir").count(),
            1,
            "the good backup must survive"
        );
    }
}
