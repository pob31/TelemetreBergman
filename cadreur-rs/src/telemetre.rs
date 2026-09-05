//! Telemetre SSE client — connect, stream, reconnect with backoff.
//!
//! Ported from `src/cadreur/telemetre_client.py`. Mirrors the serial reader's
//! pattern on the Pi: connect -> stream -> on error mark disconnected, back off
//! (1 -> 5 s), retry. Each usable payload becomes `abs_m` (tare-independent,
//! PRD §5) and goes through stages 1-2 of the smoothing chain (median-of-3 +
//! tau-EMA) with the measured dt. On stale or disconnect the smoothed value
//! simply stops being updated — it holds, and is never reset.

use futures_util::StreamExt;
use serde_json::Value;
use tokio::time::{Duration, timeout};

use crate::config::Config;
use crate::smoothing::{Median3, TauEma};
use crate::state::{Shared, lock, monotonic};

/// Generous enough for the 20 Hz feed plus 15 s keepalives; a silently dead TCP
/// path is cut after this and reconnected.
const READ_TIMEOUT: Duration = Duration::from_secs(20);

/// `abs_m = position_m * sign + zero_cm / 100` — immune to Set Zero, Clear Zero
/// and Invert Direction on the Pi, since sign squared is 1.
pub fn reconstruct_abs_m(payload: &Value) -> Option<f64> {
    let pos = payload.get("position_m").and_then(Value::as_f64)?;
    let sign = if payload.get("sign").and_then(Value::as_f64).unwrap_or(1.0) < 0.0 { -1.0 } else { 1.0 };
    let zero_cm = payload.get("zero_cm").and_then(Value::as_f64).unwrap_or(0.0);
    Some(pos * sign + zero_cm / 100.0)
}

pub fn usable(payload: &Value) -> bool {
    payload.get("connected").and_then(Value::as_bool).unwrap_or(false)
        && !payload.get("stale").and_then(Value::as_bool).unwrap_or(false)
        && payload.get("position_m").is_some_and(|v| !v.is_null())
}

/// Joins the `data:` lines of one SSE frame and parses them. Returns `None` for
/// a keepalive comment or unparseable payload.
pub fn parse_event(event: &[u8]) -> Option<Value> {
    let mut data = Vec::new();
    for line in event.split(|&b| b == b'\n') {
        if let Some(rest) = line.strip_prefix(b"data:") {
            if !data.is_empty() {
                data.push(b'\n');
            }
            data.extend_from_slice(trim_ascii(rest));
        }
    }
    if data.is_empty() {
        return None;
    }
    serde_json::from_slice(&data).ok()
}

fn trim_ascii(mut s: &[u8]) -> &[u8] {
    while let [first, rest @ ..] = s
        && first.is_ascii_whitespace()
    {
        s = rest;
    }
    while let [rest @ .., last] = s
        && last.is_ascii_whitespace()
    {
        s = rest;
    }
    s
}

pub struct Telemetre {
    url: String,
    state: Shared,
    median: Median3,
    ema: TauEma,
    last_usable_t: Option<f64>,
}

impl Telemetre {
    pub fn new(cfg: &Config, state: Shared) -> Self {
        let tau = lock(&state).show.smoothing.ema_tau_s;
        Self {
            url: format!("{}/stream", cfg.telemetre.url.trim_end_matches('/')),
            state,
            median: Median3::new(),
            ema: TauEma::new(tau),
            last_usable_t: None,
        }
    }

    /// Runs until cancelled.
    pub async fn run(mut self) {
        let client = match reqwest::Client::builder().build() {
            Ok(c) => c,
            Err(e) => {
                eprintln!("Cannot build the HTTP client: {e}");
                return;
            }
        };
        let mut backoff = 1.0_f64;
        loop {
            match self.stream_once(&client).await {
                Ok(()) => backoff = 1.0, // a successful connection resets the backoff
                Err(e) => {
                    eprintln!("Telemetre stream error: {e}; reconnecting in {backoff:.0}s");
                }
            }
            lock(&self.state).sse_status(false);
            tokio::time::sleep(Duration::from_secs_f64(backoff)).await;
            backoff = (backoff * 2.0).min(5.0);
        }
    }

    async fn stream_once(&mut self, client: &reqwest::Client) -> Result<(), String> {
        let resp = client
            .get(&self.url)
            .header("Accept", "text/event-stream")
            .send()
            .await
            .map_err(|e| e.to_string())?;
        if !resp.status().is_success() {
            return Err(format!("GET {} -> HTTP {}", self.url, resp.status()));
        }
        lock(&self.state).sse_status(true);
        eprintln!("Telemetre stream connected: {}", self.url);

        let mut stream = resp.bytes_stream();
        let mut buf: Vec<u8> = Vec::new();
        loop {
            let chunk = timeout(READ_TIMEOUT, stream.next())
                .await
                .map_err(|_| "read timed out".to_string())?
                .ok_or_else(|| "stream closed by server".to_string())?
                .map_err(|e| e.to_string())?;
            buf.extend_from_slice(&chunk);
            while let Some(i) = find_frame_end(&buf) {
                let event: Vec<u8> = buf.drain(..i + 2).collect();
                self.handle_event(&event[..i]);
            }
        }
    }

    fn handle_event(&mut self, event: &[u8]) {
        let Some(payload) = parse_event(event) else { return };
        let now = monotonic();

        if !usable(&payload) {
            self.last_usable_t = None; // measure dt across the gap correctly
            lock(&self.state).note_unusable(payload);
            return;
        }
        let Some(abs_m) = reconstruct_abs_m(&payload) else {
            lock(&self.state).note_unusable(payload);
            return;
        };
        // After a gap (first payload, or stale recovery) use one nominal 20 Hz
        // tick, so the EMA absorbs a small jump instead of snapping to it.
        let dt = match self.last_usable_t {
            None => 0.05,
            Some(prev) => (now - prev).max(0.0),
        };
        self.last_usable_t = Some(now);

        let position_m = payload.get("position_m").and_then(Value::as_f64);
        let mut st = lock(&self.state);
        self.ema.tau_s = st.show.smoothing.ema_tau_s;
        let smoothed = self.ema.update(self.median.update(abs_m), dt);
        st.update_distance(smoothed, abs_m, position_m, payload, now);
    }
}

fn find_frame_end(buf: &[u8]) -> Option<usize> {
    buf.windows(2).position(|w| w == b"\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn abs_m_is_immune_to_tare_and_inversion() {
        // sign squared is 1, so a tared, inverted Pi still yields the same abs_m
        let plain = json!({"position_m": 3.0, "sign": 1, "zero_cm": 0.0});
        assert_eq!(reconstruct_abs_m(&plain), Some(3.0));

        let tared = json!({"position_m": 1.0, "sign": 1, "zero_cm": 200.0});
        assert_eq!(reconstruct_abs_m(&tared), Some(3.0));

        let inverted = json!({"position_m": -1.0, "sign": -1, "zero_cm": 200.0});
        assert_eq!(reconstruct_abs_m(&inverted), Some(3.0));
    }

    #[test]
    fn abs_m_needs_a_position() {
        assert_eq!(reconstruct_abs_m(&json!({"sign": 1})), None);
        assert_eq!(reconstruct_abs_m(&json!({"position_m": null})), None);
    }

    #[test]
    fn usable_requires_connected_not_stale_and_a_position() {
        assert!(usable(&json!({"connected": true, "stale": false, "position_m": 1.0})));
        assert!(!usable(&json!({"connected": false, "stale": false, "position_m": 1.0})));
        assert!(!usable(&json!({"connected": true, "stale": true, "position_m": 1.0})));
        assert!(!usable(&json!({"connected": true, "stale": false, "position_m": null})));
        assert!(!usable(&json!({})));
    }

    #[test]
    fn parses_a_single_data_line() {
        let v = parse_event(b"data: {\"position_m\": 2.5}").expect("parses");
        assert_eq!(v["position_m"], 2.5);
    }

    #[test]
    fn joins_multiline_data() {
        let v = parse_event(b"data: {\ndata: \"a\": 1\ndata: }").expect("parses");
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn keepalive_comments_are_ignored() {
        assert!(parse_event(b": keepalive").is_none());
        assert!(parse_event(b"event: ping").is_none());
        assert!(parse_event(b"").is_none());
    }

    #[test]
    fn malformed_json_is_ignored() {
        assert!(parse_event(b"data: {not json").is_none());
    }

    #[test]
    fn frames_split_on_a_blank_line() {
        let buf = b"data: {\"a\":1}\n\ndata: {\"a\":2}\n\n";
        assert_eq!(find_frame_end(buf), Some(13));
    }

    #[test]
    fn url_gets_the_stream_suffix_exactly_once() {
        let mut cfg = Config::default();
        cfg.telemetre.url = "http://192.168.0.51/".into();
        let t = Telemetre::new(&cfg, crate::state::shared(cfg.clone()));
        assert_eq!(t.url, "http://192.168.0.51/stream");
    }
}
