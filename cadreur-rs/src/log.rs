//! The log file the bundled app never wrote.
//!
//! `config::log_path()` existed from the start but nothing ever called it, so
//! every diagnostic the app produced went to a stderr that does not exist: a
//! `.app` launched from the Finder has no terminal attached. That is why a
//! silently mis-parsed config, an unreachable Pi and a dead OSC socket all
//! looked identical from the régie — and why `scripts/diagnose_mac.sh`, which
//! reads a log file, had nothing to report.
//!
//! Every line now goes to both stderr (for `--headless` from a terminal) and
//! the file. Failing to open the file is never fatal: the show goes on.

use std::io::Write;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use time::OffsetDateTime;
use time::format_description::FormatItem;
use time::macros::format_description;

/// UTC, like `show.rs`'s `saved_at`. `now_local()` refuses to answer once the
/// process is multi-threaded, which it already is by the time `main` runs, and
/// a silent fallback would put two different clocks in one file.
const STAMP: &[FormatItem] = format_description!("[year]-[month]-[day] [hour]:[minute]:[second]Z");

/// Rotated at this size so a long run cannot fill the disk. One generation is
/// enough: the interesting lines are always from the launch being diagnosed.
const MAX_BYTES: u64 = 2_000_000;

static FILE: OnceLock<Option<Mutex<std::fs::File>>> = OnceLock::new();

/// Opens the log for appending. Call once, from `main`, before anything that
/// might have something to report.
pub fn init(path: PathBuf) {
    let _ = FILE.set(open(path));
}

fn open(path: PathBuf) -> Option<Mutex<std::fs::File>> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok()?;
    }
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > MAX_BYTES) {
        let mut backup = path.clone().into_os_string();
        backup.push(".1");
        let _ = std::fs::rename(&path, PathBuf::from(backup));
    }
    std::fs::OpenOptions::new().create(true).append(true).open(&path).ok().map(Mutex::new)
}

fn stamp() -> String {
    OffsetDateTime::now_utc().format(STAMP).unwrap_or_default()
}

/// `eprintln!`, but it also reaches the log file — which is the only copy an
/// operator or `scripts/diagnose_mac.sh` can actually read.
#[macro_export]
macro_rules! log_line {
    ($($arg:tt)*) => { $crate::log::line(format!($($arg)*)) };
}

/// One line, to stderr and to the log file.
pub fn line(msg: impl AsRef<str>) {
    let msg = msg.as_ref();
    eprintln!("{msg}");
    let Some(Some(file)) = FILE.get() else { return };
    let mut file = file.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    let _ = writeln!(file, "{} {msg}", stamp());
    let _ = file.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rotates_once_past_the_size_cap() {
        let dir = std::env::temp_dir().join("cadreur-log-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("tmp dir");
        let path = dir.join("cadreur.log");
        std::fs::write(&path, vec![b'x'; (MAX_BYTES + 1) as usize]).expect("seed");
        let handle = open(path.clone()).expect("opens");
        drop(handle);
        assert!(path.with_extension("log.1").exists(), "old log kept as .1");
        assert!(std::fs::metadata(&path).is_ok_and(|m| m.len() == 0), "fresh log starts empty");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_unwritable_path_is_not_fatal() {
        assert!(open(PathBuf::from("")).is_none());
    }
}
