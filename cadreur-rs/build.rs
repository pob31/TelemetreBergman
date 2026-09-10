//! Tells cargo to rebuild when the embedded web UI changes.
//!
//! `include_dir!` reads `src/cadreur/web/` at compile time but emits no
//! dependency information, so cargo had no way to know the UI had changed and
//! would happily reuse a stale object file. A build could therefore ship an
//! interface that did not match the source tree — silently, and with every
//! test still passing, because the tests never look at the embedded copy.

use std::path::Path;

fn main() {
    let web = Path::new(env!("CARGO_MANIFEST_DIR")).join("../src/cadreur/web");
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed={}", web.display());
    if let Ok(entries) = std::fs::read_dir(&web) {
        for e in entries.flatten() {
            println!("cargo:rerun-if-changed={}", e.path().display());
        }
    }
}
