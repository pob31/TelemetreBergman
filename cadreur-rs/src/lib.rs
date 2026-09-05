//! Cadreur — keeps projected video locked to a moving scrim.
//!
//! Rust port of the Python implementation in `src/cadreur/`. The web UI
//! (`src/cadreur/web/`) is reused verbatim and served over HTTP, so the
//! browser/tablet access path is preserved alongside the native window.

pub mod api;
pub mod config;
pub mod engine;
pub mod interp;
pub mod millumin;
pub mod permissions;
pub mod show;
pub mod smoothing;
pub mod state;
pub mod telemetre;
pub mod window;
