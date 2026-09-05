//! Engine: 20 Hz tick — per channel: gate -> target -> slew -> send.
//!
//! Ported from `src/cadreur/engine.py`. Every beamer holds several channels
//! (one per Millumin layer), all driven continuously and independently. Each
//! tick takes an injected `now`, so tests drive it with a fake clock and a fake
//! OSC sender. Disarmed means TOTAL OSC silence except channels explicitly in
//! calibrate mode, which drive their manual values live so the operator can fit
//! every layer at one scrim position. Arming snaps; every other discontinuity
//! glides through the slew limiters.

use std::collections::HashMap;
use std::sync::Arc;

use crate::interp::{Clamped, Values, apply_trim, interpolate, round_for_send};
use crate::millumin::OscSender;
use crate::show::BeamerKey;
use crate::smoothing::{SendPolicy, SlewLimiter};
use crate::state::{ChannelRuntime, State};

/// 20 Hz.
pub const TICK_S: f64 = 0.05;

// Gate reasons — the UI maps these to i18n strings, so the exact wire strings
// matter and must stay identical to the Python.
pub const R_DISARMED: &str = "disarmed";
pub const R_DISABLED: &str = "disabled";
pub const R_UNCALIBRATED: &str = "uncalibrated";
pub const R_NO_POINTS: &str = "no_points";
pub const R_CALIBRATING: &str = "calibrating";
pub const R_NO_DISTANCE: &str = "no_distance";

#[derive(Debug, Clone, Copy, PartialEq)]
enum Pending {
    None,
    /// Jump immediately — used on Arm.
    Snap,
    /// Seed the limiters at these values, then glide from there.
    Seed(Values),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Mode {
    Idle,
    /// Calibrate drive.
    Manual,
    /// Interpolated tracking.
    Play,
}

struct ChannelRt {
    scale: SlewLimiter,
    pos_x: SlewLimiter,
    pos_y: SlewLimiter,
    policy: SendPolicy,
    pending: Pending,
    mode: Mode,
}

impl Default for ChannelRt {
    fn default() -> Self {
        Self {
            scale: SlewLimiter::new(0.05),
            pos_x: SlewLimiter::new(0.05),
            pos_y: SlewLimiter::new(0.05),
            policy: SendPolicy::new(),
            pending: Pending::None,
            mode: Mode::Idle,
        }
    }
}

impl ChannelRt {
    fn set_rate(&mut self, rate: f64) {
        self.scale.rate_per_s = rate;
        self.pos_x.rate_per_s = rate;
        self.pos_y.rate_per_s = rate;
    }

    fn snap_all(&mut self, v: Values) {
        self.scale.snap(v.scale);
        self.pos_x.snap(v.pos_x);
        self.pos_y.snap(v.pos_y);
    }

    fn step_all(&mut self, target: Values, dt: f64) -> Values {
        Values {
            scale: self.scale.step(target.scale, dt),
            pos_x: self.pos_x.step(target.pos_x, dt),
            pos_y: self.pos_y.step(target.pos_y, dt),
        }
    }
}

pub struct Engine {
    io: Arc<dyn OscSender>,
    rt: HashMap<String, ChannelRt>,
    prev_armed: bool,
    last_tick: Option<f64>,
}

impl Engine {
    pub fn new(io: Arc<dyn OscSender>) -> Self {
        Self { io, rt: HashMap::new(), prev_armed: false, last_tick: None }
    }

    // --- discontinuity hooks, called by the web layer ------------------------

    pub fn request_snap(&mut self) {
        for rt in self.rt.values_mut() {
            rt.pending = Pending::Snap;
        }
    }

    pub fn request_reseed(&mut self, key: &str, values: Option<Values>) {
        if let Some(rt) = self.rt.get_mut(key) {
            rt.pending = values.map_or(Pending::Snap, Pending::Seed);
        }
    }

    // --- the tick ------------------------------------------------------------

    pub fn tick(&mut self, state: &mut State, now: f64) {
        let dt = match self.last_tick {
            None => TICK_S,
            Some(prev) => (now - prev).clamp(0.0, 0.25),
        };
        self.last_tick = Some(now);

        state.maybe_autosave(now);

        let sm = state.show.smoothing;
        let armed = state.armed;
        if armed && !self.prev_armed {
            self.request_snap(); // snap, don't slew, on Arm
        }
        self.prev_armed = armed;

        let (abs_m, ever_usable) = state.distance();

        let mut live_keys: Vec<String> = Vec::new();
        for b in BeamerKey::ALL {
            let ids: Vec<String> = state.show.channels(b).iter().map(|c| c.id.clone()).collect();
            for cid in ids {
                let key = State::chan_key(b, &cid);
                live_keys.push(key.clone());
                let out = self.tick_channel(state, b, &cid, &key, now, dt, sm, armed, abs_m, ever_usable);
                state.channels_state.insert(key, out);
            }
        }
        // Drop runtime and published state for channels that no longer exist.
        self.rt.retain(|k, _| live_keys.contains(k));
        state.channels_state.retain(|k, _| live_keys.contains(k));
    }

    #[allow(clippy::too_many_arguments)]
    fn tick_channel(
        &mut self,
        state: &mut State,
        b: BeamerKey,
        cid: &str,
        key: &str,
        now: f64,
        dt: f64,
        sm: crate::show::Smoothing,
        armed: bool,
        abs_m: Option<f64>,
        ever_usable: bool,
    ) -> ChannelRuntime {
        let rt = self.rt.entry(key.to_string()).or_default();
        rt.set_rate(sm.slew_scale_per_s);

        // Snapshot everything needed from the show before mutating state.
        let ch = state.show.channel(b, cid).expect("channel exists this tick");
        let (enabled, osc_scale, osc_posv, osc_posh) =
            (ch.enabled, ch.osc_scale.clone(), ch.osc_posv.clone(), ch.osc_posh.clone());
        let cset = state.show.cal_set_for(b, ch);
        let (points, trim) = match cset {
            Some(c) => (c.points.clone(), c.trim),
            None => (Vec::new(), crate::interp::Trim::default()),
        };
        let has_set = cset.is_some();
        let n_points = points.len();
        let calibrating = state.calibrate.contains(key);

        // Calibrate drives manual values independently of the master Arm.
        let reason: Option<&str> = if calibrating {
            Some(R_CALIBRATING)
        } else if !armed {
            Some(R_DISARMED)
        } else if !enabled {
            Some(R_DISABLED)
        } else if !has_set {
            Some(R_UNCALIBRATED) // never fall back to another memory's set
        } else if points.is_empty() {
            Some(R_NO_POINTS)
        } else if !ever_usable {
            Some(R_NO_DISTANCE)
        } else {
            None
        };
        let gate = reason.is_none();

        let mut target: Option<Values> = None;
        let mut clamped: Option<Clamped> = None;
        if !points.is_empty()
            && let Some(d) = abs_m
        {
            let (v, c) = interpolate(&points, d);
            clamped = c;
            target = v.map(|v| apply_trim(v, trim));
        }

        let mode = if calibrating {
            Mode::Manual
        } else if gate && target.is_some() {
            Mode::Play
        } else {
            Mode::Idle
        };
        if mode != rt.mode {
            rt.policy.reset();
            rt.mode = mode;
        }

        let mut values: Option<Values> = None;
        let mut sending = false;

        match mode {
            Mode::Manual => {
                let m = state.manual_of(key);
                let rt = self.rt.get_mut(key).expect("inserted above");
                let v = round_for_send(Values { scale: m.scale, pos_x: m.pos_h, pos_y: m.pos_v });
                // Keep the limiters seeded so the exit handover glides.
                rt.snap_all(v);
                rt.pending = Pending::None;
                if rt.policy.due(v, now, sm.deadband_scale, sm.refresh_hz) {
                    emit(&*self.io, &osc_scale, &osc_posh, &osc_posv, v);
                    rt.policy.mark_sent(v, now);
                    sending = true;
                }
                values = Some(v);
            }
            Mode::Play => {
                let t = target.expect("play implies a target");
                match rt.pending {
                    Pending::Snap => rt.snap_all(t),
                    Pending::Seed(seed) => rt.snap_all(seed),
                    Pending::None => {}
                }
                rt.pending = Pending::None;
                let v = round_for_send(rt.step_all(t, dt));
                if rt.policy.due(v, now, sm.deadband_scale, sm.refresh_hz) {
                    emit(&*self.io, &osc_scale, &osc_posh, &osc_posv, v);
                    rt.policy.mark_sent(v, now);
                    sending = true;
                }
                values = Some(v);
            }
            Mode::Idle => {
                // Zero OSC. Show the would-be values so the operator can see
                // what arming or enabling would send.
                values = target.map(round_for_send);
            }
        }

        ChannelRuntime {
            gate,
            reason: reason.map(str::to_string),
            clamped: clamped.map(|c| match c {
                Clamped::Low => "low".to_string(),
                Clamped::High => "high".to_string(),
            }),
            values,
            sending,
            n_points,
        }
    }
}

fn emit(io: &dyn OscSender, osc_scale: &str, osc_posh: &str, osc_posv: &str, v: Values) {
    io.send_value(osc_scale, v.scale);
    io.send_value(osc_posh, v.pos_x); // horizontal
    io.send_value(osc_posv, v.pos_y); // vertical
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::interp::Point;
    use crate::millumin::FakeSender;
    use crate::show::{CalSet, Show};
    use crate::state::Manual;
    use serde_json::json;

    const F1: &str = "front/front-1";
    const R1: &str = "rear/rear-1";
    const F_SCALE: &str = "/front/scale/1";
    const F_POSV: &str = "/front/positionV/1";
    const F_POSH: &str = "/front/positionH/1";
    const R_SCALE: &str = "/retro/scale/1";
    const R_POSV: &str = "/retro/positionV/1";

    fn pt(d: f64, s: f64, x: f64, y: f64) -> Point {
        Point { distance_m: d, scale: s, pos_x: x, pos_y: y }
    }

    fn cal(points: Vec<Point>) -> CalSet {
        CalSet { interp: "linear".into(), trim: crate::interp::Trim::default(), points }
    }

    struct Harness {
        state: State,
        io: Arc<FakeSender>,
        engine: Engine,
        now: f64,
    }

    impl Harness {
        fn new() -> Self {
            let mut cfg = Config::default();
            cfg.shows.autosave = false;
            let mut state = State::new(cfg);
            let mut doc = Show::new("Test");
            let f1 = doc.beamers.front.channels[0].id.clone();
            let r1 = doc.beamers.rear.channels[0].id.clone();
            doc.channel_mut(BeamerKey::Front, &f1)
                .expect("f1")
                .calibrations
                .insert("M1".into(), cal(vec![pt(2.0, 0.6, 0.5, 0.4), pt(4.0, 0.4, 0.5, 0.6)]));
            doc.channel_mut(BeamerKey::Rear, &r1)
                .expect("r1")
                .calibrations
                .insert("default".into(), cal(vec![pt(3.0, 0.7, 0.5, 0.5)]));
            state.show = doc;
            let io = Arc::new(FakeSender::default());
            let engine = Engine::new(io.clone());
            Self { state, io, engine, now: 100.0 }
        }

        fn feed(&mut self, abs_m: f64) {
            let now = self.now;
            self.state.sse_status(true);
            self.state.update_distance(
                abs_m,
                abs_m,
                Some(abs_m),
                json!({"connected": true, "stale": false}),
                now,
            );
        }

        fn run(&mut self, n: usize) {
            for _ in 0..n {
                self.engine.tick(&mut self.state, self.now);
                self.now += TICK_S;
            }
        }

        fn reason(&self, key: &str) -> Option<String> {
            self.state.channels_state[key].reason.clone()
        }

        fn values(&self, key: &str) -> Option<Values> {
            self.state.channels_state[key].values
        }
    }

    fn armed_harness() -> Harness {
        let mut h = Harness::new();
        h.feed(3.0);
        h
    }

    #[test]
    fn disarmed_is_total_silence() {
        let mut h = armed_harness();
        h.run(40);
        assert!(h.io.is_empty(), "disarmed must send nothing at all");
        assert_eq!(h.reason(F1).as_deref(), Some(R_DISARMED));
        // the would-be value is still shown to the operator
        assert!((h.values(F1).expect("values").scale - 0.5).abs() < 1e-9);
    }

    #[test]
    fn armed_sends_all_three_axes_on_channel_1() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(2);
        assert!(!h.io.values_to(F_SCALE).is_empty());
        assert!(!h.io.values_to(F_POSV).is_empty());
        assert!(!h.io.values_to(F_POSH).is_empty());
        assert!(!h.io.values_to(R_SCALE).is_empty(), "N=1 is a constant hold, not silence");
    }

    #[test]
    fn arming_snaps_so_the_first_send_is_the_target() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(1);
        // interpolated at 3.0, with no glide
        assert!((h.io.values_to(F_SCALE)[0] - 0.5).abs() < 1e-9);
    }

    #[test]
    fn a_disabled_channel_is_silent() {
        let mut h = armed_harness();
        let f1 = h.state.show.beamers.front.channels[0].id.clone();
        h.state.show.channel_mut(BeamerKey::Front, &f1).expect("f1").enabled = false;
        h.state.armed = true;
        h.run(5);
        assert!(!h.io.any_to(&[F_SCALE, F_POSV, F_POSH]));
        assert!(h.io.any_to(&[R_SCALE, R_POSV]), "the rear keeps running");
    }

    #[test]
    fn an_uncalibrated_memory_inhibits_with_no_fallback() {
        let mut h = armed_harness();
        h.state.show.settings.active_lens_memory = "M2".into();
        h.state.armed = true;
        h.run(5);
        assert!(!h.io.any_to(&[F_SCALE, F_POSV, F_POSH]));
        assert_eq!(h.reason(F1).as_deref(), Some(R_UNCALIBRATED));
    }

    #[test]
    fn empty_points_inhibit() {
        let mut h = armed_harness();
        let f1 = h.state.show.beamers.front.channels[0].id.clone();
        h.state
            .show
            .channel_mut(BeamerKey::Front, &f1)
            .expect("f1")
            .calibrations
            .get_mut("M1")
            .expect("M1")
            .points
            .clear();
        h.state.armed = true;
        h.run(5);
        assert!(!h.io.any_to(&[F_SCALE, F_POSV, F_POSH]));
        assert_eq!(h.reason(F1).as_deref(), Some(R_NO_POINTS));
    }

    #[test]
    fn calibrate_drives_manual_values_even_disarmed() {
        let mut h = armed_harness();
        h.state.manual.insert(F1.into(), Manual { scale: 0.8, pos_v: 0.3, pos_h: 0.7 });
        h.state.calibrate.insert(F1.into());
        h.run(5);
        assert!((h.io.values_to(F_SCALE).last().copied().expect("sent") - 0.8).abs() < 1e-9);
        assert!((h.io.values_to(F_POSV).last().copied().expect("sent") - 0.3).abs() < 1e-9);
        assert!((h.io.values_to(F_POSH).last().copied().expect("sent") - 0.7).abs() < 1e-9);
        assert_eq!(h.reason(F1).as_deref(), Some(R_CALIBRATING));
        assert!(!h.io.any_to(&[R_SCALE, R_POSV]), "the rear stays disarmed and silent");
    }

    #[test]
    fn two_channels_calibrate_at_once() {
        let mut h = armed_harness();
        h.state.manual.insert(F1.into(), Manual { scale: 0.8, pos_v: 0.5, pos_h: 0.5 });
        h.state.manual.insert(R1.into(), Manual { scale: 0.2, pos_v: 0.5, pos_h: 0.5 });
        h.state.calibrate.insert(F1.into());
        h.state.calibrate.insert(R1.into());
        h.run(3);
        assert!((h.io.values_to(F_SCALE).last().copied().expect("sent") - 0.8).abs() < 1e-9);
        assert!((h.io.values_to(R_SCALE).last().copied().expect("sent") - 0.2).abs() < 1e-9);
    }

    #[test]
    fn at_rest_only_the_refresh_cadence_sends() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(1);
        h.io.clear();
        h.run(64); // ~3.2 s at refresh_hz = 1.0
        assert_eq!(h.io.values_to(F_SCALE).len(), 3, "one per refresh period, not one per tick");
    }

    #[test]
    fn movement_beyond_the_deadband_sends() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(1);
        h.io.clear();
        h.feed(3.1);
        h.run(4);
        assert!(h.io.any_to(&[F_SCALE, F_POSV, F_POSH]));
    }

    #[test]
    fn stale_holds_the_value_and_keeps_refreshing() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(2);
        h.io.clear();
        h.state.sse_status(false); // SSE drops; the distance value holds
        h.run(60); // 3 s
        let sends = h.io.values_to(F_SCALE);
        assert!(sends.len() >= 2, "the refresh cadence must continue");
        for v in sends {
            assert!((v - 0.5).abs() < 1e-9, "held value must not drift");
        }
    }

    #[test]
    fn a_point_edit_glides_instead_of_jumping() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(2);
        // Same channel, very different calibration -> a discontinuity.
        let f1 = h.state.show.beamers.front.channels[0].id.clone();
        h.state
            .show
            .channel_mut(BeamerKey::Front, &f1)
            .expect("f1")
            .calibrations
            .insert("M1".into(), cal(vec![pt(2.0, 0.9, 0.5, 0.2), pt(4.0, 0.9, 0.5, 0.2)]));
        h.io.clear();
        h.run(20); // 1 s at slew 0.05/s: scale may move at most 0.05
        let scales = h.io.values_to(F_SCALE);
        assert!(!scales.is_empty());
        let max = scales.iter().copied().fold(f64::MIN, f64::max);
        assert!(max < 0.5 + 0.06, "jumped to {max} instead of gliding");
        assert!(max > 0.5, "should be gliding upward");
    }

    #[test]
    fn reseed_after_leaving_calibrate_glides_from_the_seed() {
        let mut h = armed_harness();
        h.state.armed = true;
        h.run(2);
        h.state.calibrate.insert(F1.into());
        h.run(5);
        h.state.calibrate.remove(F1);
        h.engine.request_reseed(F1, Some(Values { scale: 0.8, pos_x: 0.5, pos_y: 0.5 }));
        h.io.clear();
        h.run(1);
        // 0.8 gliding toward 0.5 at 0.05/s over one 0.05 s tick
        assert!((h.io.values_to(F_SCALE)[0] - 0.7975).abs() < 1e-3);
    }

    #[test]
    fn deleted_channels_stop_being_published() {
        let mut h = armed_harness();
        h.run(1);
        assert!(h.state.channels_state.contains_key(F1));
        let f1 = h.state.show.beamers.front.channels[0].id.clone();
        crate::show::delete_channel(&mut h.state.show, BeamerKey::Front, &f1).expect("deletes");
        h.run(1);
        assert!(!h.state.channels_state.contains_key(F1), "stale runtime state must be dropped");
    }
}
