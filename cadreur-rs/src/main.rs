//! Cadreur — keeps projected video locked to a moving scrim.
//!
//! Single process: the telemetre SSE client, the 20 Hz engine and the HTTP
//! server all run as tokio tasks. `--headless` skips the native window and
//! serves the UI to a browser or tablet, which is what the Python's
//! `python -m cadreur` did.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use cadreur::api::{self, App};
use cadreur::config::{self, Config};
use cadreur::engine::{Engine, TICK_S};
use cadreur::millumin::MilluminIo;
use cadreur::show;
use cadreur::state::{Shared, lock, monotonic, shared};
use cadreur::telemetre::Telemetre;

/// Reopens the last show. Both failure paths are reported to the operator
/// rather than swallowed: the Python logged a warning at most, which is how a
/// missing show came to look like a working-but-empty interface.
fn load_startup_show(state: &Shared) {
    let last = { lock(state).load_last_show_path() };
    let Some(path) = last else {
        eprintln!("No previous show to reopen — start from the interface.");
        return;
    };
    // Rotating backups before touching the file, but never rotating a broken
    // one out over the good ones.
    show::startup_backup(&path, 10);
    match show::load_show(&path) {
        Ok(doc) => {
            let mut st = lock(state);
            st.show = doc;
            st.show_path = Some(path.clone());
            eprintln!("Loaded show {}", path.display());
        }
        Err(e) => eprintln!("Could not load last show {}: {e}", path.display()),
    }
}

#[tokio::main]
async fn main() {
    let headless = std::env::args().any(|a| a == "--headless");

    if let Err(e) = config::ensure_data_dir() {
        eprintln!("Cannot create the data directory: {e}");
    }
    let cfg = Config::load();
    let state = shared(cfg.clone());
    let io = Arc::new(MilluminIo::new(&cfg.millumin));
    let engine = Arc::new(Mutex::new(Engine::new(io.clone())));

    load_startup_show(&state);

    // 20 Hz engine tick.
    let engine_task = {
        let (state, engine) = (state.clone(), engine.clone());
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(Duration::from_secs_f64(TICK_S));
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            loop {
                ticker.tick().await;
                let mut st = lock(&state);
                let mut eng = engine.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
                eng.tick(&mut st, monotonic());
            }
        })
    };

    // Telemetre SSE client.
    let telemetre_task = tokio::spawn(Telemetre::new(&cfg, state.clone()).run());

    let app = App { state: state.clone(), engine, io, cfg: cfg.clone() };
    let addr = format!("{}:{}", cfg.web.host, cfg.web.port);
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("Cannot bind {addr}: {e}");
            eprintln!("Another program is probably using port {}.", cfg.web.port);
            std::process::exit(1);
        }
    };
    eprintln!(
        "Cadreur up on {addr} (millumin {}:{}, telemetre {})",
        cfg.millumin.host, cfg.millumin.port, cfg.telemetre.url
    );
    eprintln!("Data directory: {}", config::data_dir().display());

    let server = axum::serve(listener, api::router(app));
    if headless {
        eprintln!("Open {}", cfg.web_url());
        if let Err(e) = server.await {
            eprintln!("Server error: {e}");
        }
    } else {
        // The window owns the main thread; the server runs behind it.
        tokio::spawn(async move {
            if let Err(e) = server.await {
                eprintln!("Server error: {e}");
            }
        });
        cadreur::window::run(&cfg.web_url());
    }
    engine_task.abort();
    telemetre_task.abort();
}
