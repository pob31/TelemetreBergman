//! Machine settings (PRD §13) and where the operator's data lives.
//!
//! Ported from `src/cadreur/config.py`. Everything the operator *edits* lives
//! in the show file; this is per-machine wiring only. Every key has a code
//! default, so a missing file or a missing key never blocks startup, and
//! unknown keys are ignored so an old config never crashes a newer build.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// Written into a fresh data directory on first run, so the app is usable with
/// no setup step. Comments are in French: the operator is a French video tech.
pub const DEFAULT_CONFIG_TOML: &str = r#"# Cadreur — réglages machine/réseau.
# Chaque clé a une valeur par défaut dans le code : un fichier ou une clé
# manquante n'empêche jamais le démarrage.

[telemetre]
url = "http://192.168.0.51"     # le Pi ; Cadreur ajoute /stream
stale_after_ms = 1500           # au-delà, la distance est considérée figée

[millumin]
host = "127.0.0.1"
port = 5000                     # entrée OSC de Millumin
# Le retour d'info ne marche qu'avec l'API standard /layer:NOM. Avec des
# adresses d'Interaction personnalisées (le cas par défaut), laisser à false.
feedback = false
feedback_port = 8001
feedback_timeout_ms = 1500

[web]
host = "127.0.0.1"              # 0.0.0.0 pour piloter depuis une tablette
port = 8080

[shows]
dir = "shows"                   # relatif au dossier de données
autosave = true
autosave_debounce_s = 5.0
"#;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct TelemetreCfg {
    /// Cadreur appends `/stream`.
    pub url: String,
    pub stale_after_ms: u64,
}

impl Default for TelemetreCfg {
    fn default() -> Self {
        Self { url: "http://192.168.1.36".into(), stale_after_ms: 1500 }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct MilluminCfg {
    pub host: String,
    /// Millumin's OSC input.
    pub port: u16,
    /// Feedback/readback only works with the standard `/layer:NAME` API.
    /// Custom Interaction addresses (the default) don't answer `/?` queries,
    /// so this is off: no listener is bound and the armed probe is disabled.
    pub feedback: bool,
    /// 8000 is often taken; must match Millumin's feedback destination.
    pub feedback_port: u16,
    pub feedback_timeout_ms: u64,
}

impl Default for MilluminCfg {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".into(),
            port: 5000,
            feedback: false,
            feedback_port: 8001,
            feedback_timeout_ms: 1500,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct WebCfg {
    /// `0.0.0.0` hands control to the stage LAN — a deliberate choice, used
    /// when the operator drives from a tablet.
    pub host: String,
    pub port: u16,
}

impl Default for WebCfg {
    fn default() -> Self {
        Self { host: "127.0.0.1".into(), port: 8080 }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct ShowsCfg {
    /// Relative to the data directory, or absolute.
    pub dir: String,
    pub autosave: bool,
    pub autosave_debounce_s: f64,
}

impl Default for ShowsCfg {
    fn default() -> Self {
        Self { dir: "shows".into(), autosave: true, autosave_debounce_s: 5.0 }
    }
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub telemetre: TelemetreCfg,
    pub millumin: MilluminCfg,
    pub web: WebCfg,
    pub shows: ShowsCfg,
}

/// True when this binary sits inside `Something.app/Contents/MacOS/`.
///
/// Checked from the executable's own path rather than an environment variable
/// the launcher has to remember to set — there is no launcher script any more,
/// the binary *is* `CFBundleExecutable`.
pub fn running_in_bundle() -> bool {
    let Ok(exe) = std::env::current_exe() else { return false };
    let mut up = exe.parent(); // .../Contents/MacOS
    if up.and_then(Path::file_name) != Some(OsStr::new("MacOS")) {
        return false;
    }
    up = up.and_then(Path::parent); // .../Contents
    if up.and_then(Path::file_name) != Some(OsStr::new("Contents")) {
        return false;
    }
    up.and_then(Path::parent) // .../Something.app
        .is_some_and(|p| p.extension() == Some(OsStr::new("app")))
}

/// Where the operator's data lives: shows, config, log.
///
/// Inside a bundle that is `~/Library/Application Support/Cadreur` — never the
/// bundle itself, which is disposable and replaced wholesale on update. From a
/// source checkout it stays the repo root, so development behaves as it always
/// did. `CADREUR_DATA_DIR` overrides both; point it at an existing Python
/// install to keep using that install's `shows/` unchanged.
pub fn data_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("CADREUR_DATA_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    if running_in_bundle()
        && let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty())
    {
        return PathBuf::from(home).join("Library/Application Support/Cadreur");
    }
    // Dev: the repo root, matching the Python's REPO_ROOT.
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// The log file. Inside a bundle macOS convention is `~/Library/Logs`.
pub fn log_path() -> PathBuf {
    if running_in_bundle()
        && let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty())
    {
        return PathBuf::from(home).join("Library/Logs/Cadreur/cadreur.log");
    }
    data_dir().join("cadreur.log")
}

pub fn config_path() -> PathBuf {
    if let Some(p) = std::env::var_os("CADREUR_CONFIG").filter(|p| !p.is_empty()) {
        return PathBuf::from(p);
    }
    data_dir().join("cadreur.toml")
}

impl Config {
    /// Unknown keys are ignored, and a malformed file falls back to defaults
    /// with a warning rather than refusing to start — the show must go on.
    pub fn load_from(path: &Path) -> Self {
        let Ok(text) = std::fs::read_to_string(path) else { return Self::default() };
        match toml::from_str(&text) {
            Ok(cfg) => cfg,
            Err(e) => {
                eprintln!("Ignoring unreadable {}: {e}", path.display());
                Self::default()
            }
        }
    }

    pub fn load() -> Self {
        Self::load_from(&config_path())
    }

    pub fn shows_dir(&self) -> PathBuf {
        let p = Path::new(&self.shows.dir);
        if p.is_absolute() { p.to_path_buf() } else { data_dir().join(p) }
    }

    /// The address the window should point at. `0.0.0.0` is a bind address,
    /// not something a browser can open.
    pub fn web_url(&self) -> String {
        let host = if self.web.host == "0.0.0.0" { "127.0.0.1" } else { &self.web.host };
        format!("http://{host}:{}", self.web.port)
    }
}

/// First run: create the data directory and seed a config so the app opens
/// with no setup step at all. Never overwrites an existing file.
pub fn ensure_data_dir() -> std::io::Result<PathBuf> {
    let dir = data_dir();
    std::fs::create_dir_all(&dir)?;
    std::fs::create_dir_all(dir.join("shows"))?;
    let cfg = config_path();
    if !cfg.exists() {
        if let Some(parent) = cfg.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&cfg, DEFAULT_CONFIG_TOML)?;
    }
    Ok(dir)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_the_python() {
        let c = Config::default();
        assert_eq!(c.telemetre.url, "http://192.168.1.36");
        assert_eq!(c.telemetre.stale_after_ms, 1500);
        assert_eq!(c.millumin.port, 5000);
        assert!(!c.millumin.feedback);
        assert_eq!(c.millumin.feedback_port, 8001);
        assert_eq!(c.web.host, "127.0.0.1");
        assert_eq!(c.web.port, 8080);
        assert_eq!(c.shows.dir, "shows");
        assert!(c.shows.autosave);
    }

    #[test]
    fn partial_toml_keeps_defaults_for_the_rest() {
        let c: Config = toml::from_str("[web]\nport = 8090\n").expect("parses");
        assert_eq!(c.web.port, 8090);
        assert_eq!(c.web.host, "127.0.0.1"); // untouched
        assert_eq!(c.millumin.port, 5000);
    }

    #[test]
    fn unknown_keys_are_ignored() {
        let c: Config = toml::from_str("[web]\nport = 9000\nmystery = 1\n\n[nonsense]\nx = 2\n")
            .expect("parses");
        assert_eq!(c.web.port, 9000);
    }

    #[test]
    fn the_shipped_example_config_parses() {
        let c: Config = toml::from_str(DEFAULT_CONFIG_TOML).expect("example config parses");
        assert_eq!(c.telemetre.url, "http://192.168.0.51");
        assert_eq!(c.web.port, 8080);
        assert_eq!(c.shows.autosave_debounce_s, 5.0);
    }

    #[test]
    fn missing_file_yields_defaults() {
        let c = Config::load_from(Path::new("/nonexistent/cadreur.toml"));
        assert_eq!(c, Config::default());
    }

    #[test]
    fn absolute_shows_dir_is_respected() {
        let mut c = Config::default();
        c.shows.dir = "/tmp/elsewhere".into();
        assert_eq!(c.shows_dir(), Path::new("/tmp/elsewhere"));
    }

    #[test]
    fn web_url_never_offers_the_bind_wildcard() {
        let mut c = Config::default();
        c.web.host = "0.0.0.0".into();
        assert_eq!(c.web_url(), "http://127.0.0.1:8080");
    }
}
