//! Smoothing chain building blocks (PRD §8) — pure, clock passed in.
//!
//! Ported from `src/cadreur/smoothing.py`. Stages 1-2 (median-of-3 + tau-EMA)
//! run on SSE arrival with the measured dt; stages 4-5 (slew limiter + send
//! policy) run in the 20 Hz engine tick. At 4 cm/min the scrim moves 0.67 mm/s,
//! so seconds of filter lag are invisible.

use crate::interp::Values;

/// Median of the last 3 samples — SSE hiccup/replay insurance.
#[derive(Debug, Default, Clone)]
pub struct Median3 {
    buf: Vec<f64>,
}

impl Median3 {
    pub fn new() -> Self {
        Self { buf: Vec::with_capacity(3) }
    }

    pub fn update(&mut self, x: f64) -> f64 {
        self.buf.push(x);
        if self.buf.len() > 3 {
            self.buf.remove(0);
        }
        if self.buf.len() < 3 {
            return *self.buf.last().expect("just pushed");
        }
        let mut s = self.buf.clone();
        s.sort_by(|a, b| a.total_cmp(b));
        s[1]
    }
}

/// EMA parameterised in seconds: `alpha = dt / (tau + dt)` with the measured dt.
///
/// `tau = 0` is pass-through. Freezing on stale data is simply not calling
/// [`TauEma::update`] — the value holds and is never reset, so recovery resumes
/// from where it left off instead of snapping.
#[derive(Debug, Clone)]
pub struct TauEma {
    pub tau_s: f64,
    pub value: Option<f64>,
}

impl TauEma {
    pub fn new(tau_s: f64) -> Self {
        Self { tau_s, value: None }
    }

    pub fn update(&mut self, x: f64, dt: f64) -> f64 {
        match self.value {
            None => self.value = Some(x),
            Some(_) if self.tau_s <= 0.0 => self.value = Some(x),
            Some(v) if dt > 0.0 => {
                // dt <= 0 -> no time elapsed -> hold (never snap)
                let alpha = dt / (self.tau_s + dt);
                self.value = Some(v + alpha * (x - v));
            }
            Some(_) => {}
        }
        self.value.expect("set above")
    }
}

/// Moves the output toward the target at most `rate_per_s` — turns any
/// discontinuity (memory switch, point edit, staircase step) into a short
/// glide. [`SlewLimiter::snap`] jumps immediately; used on Arm.
#[derive(Debug, Clone)]
pub struct SlewLimiter {
    pub rate_per_s: f64,
    pub value: Option<f64>,
}

impl SlewLimiter {
    pub fn new(rate_per_s: f64) -> Self {
        Self { rate_per_s, value: None }
    }

    pub fn snap(&mut self, value: f64) -> f64 {
        self.value = Some(value);
        value
    }

    pub fn step(&mut self, target: f64, dt: f64) -> f64 {
        let Some(v) = self.value else {
            self.value = Some(target);
            return target;
        };
        let max_step = self.rate_per_s * dt.max(0.0);
        let delta = target - v;
        let next = if delta.abs() <= max_step {
            target
        } else if delta > 0.0 {
            v + max_step
        } else {
            v - max_step
        };
        self.value = Some(next);
        next
    }
}

/// Stage 5: send when any output moved at least its dead-band since the last
/// send, or the refresh period elapsed (absolute values self-heal a Millumin
/// restart). One decision per beamer per tick; `now` is injected.
#[derive(Debug, Default, Clone)]
pub struct SendPolicy {
    pub last: Option<Values>,
    pub last_t: Option<f64>,
}

impl SendPolicy {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn reset(&mut self) {
        self.last = None;
        self.last_t = None;
    }

    /// `scale`, `pos_x` and `pos_y` are all normalised 0..1, so one dead-band
    /// governs all three.
    pub fn due(&self, values: Values, now: f64, deadband: f64, refresh_hz: f64) -> bool {
        let (Some(last), Some(last_t)) = (self.last, self.last_t) else {
            return true;
        };
        if refresh_hz > 0.0 && now - last_t >= 1.0 / refresh_hz {
            return true;
        }
        (values.scale - last.scale).abs() >= deadband
            || (values.pos_x - last.pos_x).abs() >= deadband
            || (values.pos_y - last.pos_y).abs() >= deadband
    }

    pub fn mark_sent(&mut self, values: Values, now: f64) {
        self.last = Some(values);
        self.last_t = Some(now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;

    const V: Values = Values { scale: 0.5, pos_x: 0.0, pos_y: 0.5 };

    fn decide(sp: &SendPolicy, values: Values, now: f64) -> bool {
        sp.due(values, now, 0.0005, 1.0)
    }

    #[test]
    fn median3_rejects_single_hiccup() {
        let mut m = Median3::new();
        m.update(3.0);
        m.update(3.0);
        assert_eq!(m.update(9.9), 3.0); // one wild SSE sample
    }

    #[test]
    fn median3_tracks_level() {
        let mut m = Median3::new();
        let mut out = 0.0;
        for _ in 0..3 {
            out = m.update(2.5);
        }
        assert_eq!(out, 2.5);
    }

    #[test]
    fn ema_attenuates_pendulum_sway() {
        // 0.5 Hz +/-1 cm sway with tau=5 s -> ~16x attenuation, residual < 1 mm.
        let mut ema = TauEma::new(5.0);
        let dt = 0.05; // 20 Hz feed
        let mut residual: f64 = 0.0;
        let n = (60.0 / dt) as i32;
        for i in 0..n {
            let t = f64::from(i) * dt;
            let x = 3.0 + 0.01 * (2.0 * PI * 0.5 * t).sin();
            let y = ema.update(x, dt);
            if t > 50.0 {
                residual = residual.max((y - 3.0).abs());
            }
        }
        assert!(residual < 0.001, "residual {residual}");
    }

    #[test]
    fn ema_ramp_lag_is_tau_times_v() {
        // 4 cm/min ramp: steady-state lag = tau*v ~ 3.3 mm.
        let (tau, v, dt) = (5.0, 0.04 / 60.0, 0.05);
        let mut ema = TauEma::new(tau);
        let (mut x, mut y) = (0.0, 0.0);
        for i in 0..(60.0 / dt) as i32 {
            x = v * f64::from(i) * dt;
            y = ema.update(x, dt);
        }
        assert!(((x - y) - tau * v).abs() < 0.0005, "lag {}", x - y);
    }

    #[test]
    fn ema_freeze_resumes_without_reset() {
        let mut ema = TauEma::new(5.0);
        for _ in 0..100 {
            ema.update(3.0, 0.05);
        }
        let held = ema.value.unwrap();
        // Stale: no updates happen; the value simply holds.
        assert_eq!(ema.value.unwrap(), held);
        // Recovery near the held value: no jump.
        let y = ema.update(3.01, 0.05);
        assert!((y - held).abs() < 0.001);
    }

    #[test]
    fn ema_zero_tau_is_passthrough() {
        let mut ema = TauEma::new(0.0);
        ema.update(1.0, 0.05);
        assert_eq!(ema.update(2.0, 0.05), 2.0);
    }

    #[test]
    fn slew_step_response_duration() {
        // 0.1 step at 0.05/s -> 2 s glide.
        let mut sl = SlewLimiter::new(0.05);
        sl.snap(0.5);
        let dt = 0.05;
        let mut ticks = 0;
        while sl.step(0.6, dt) != 0.6 {
            ticks += 1;
            assert!(ticks < 100, "never reached target");
        }
        assert!((f64::from(ticks) * dt - 2.0).abs() <= 2.0 * dt);
    }

    #[test]
    fn slew_snap_on_arm() {
        let mut sl = SlewLimiter::new(0.05);
        sl.snap(0.9); // arming snaps, no glide
        assert_eq!(sl.value, Some(0.9));
        assert_eq!(sl.step(0.9, 0.05), 0.9);
    }

    #[test]
    fn slew_never_limits_tracking_speed() {
        // Normal tracking (~0.0001 scale/s) is far below the slew rate.
        let mut sl = SlewLimiter::new(0.05);
        sl.snap(0.5);
        assert_eq!(sl.step(0.5001, 0.05), 0.5001);
    }

    #[test]
    fn send_first_is_always_due() {
        assert!(decide(&SendPolicy::new(), V, 0.0));
    }

    #[test]
    fn send_deadband_suppresses() {
        let mut sp = SendPolicy::new();
        sp.mark_sent(V, 0.0);
        let tiny = Values { scale: 0.5002, pos_x: 0.0, pos_y: 0.5002 };
        assert!(!decide(&sp, tiny, 0.5));
    }

    #[test]
    fn send_deadband_exceeded_on_vertical() {
        let mut sp = SendPolicy::new();
        sp.mark_sent(V, 0.0);
        let moved = Values { scale: 0.5, pos_x: 0.0, pos_y: 0.5006 };
        assert!(decide(&sp, moved, 0.1));
    }

    #[test]
    fn send_refresh_fires_even_at_rest() {
        let mut sp = SendPolicy::new();
        sp.mark_sent(V, 0.0);
        assert!(!decide(&sp, V, 0.9));
        assert!(decide(&sp, V, 1.0));
    }
}
