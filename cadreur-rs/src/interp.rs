//! Calibration-point math: piecewise-linear interpolation, trim, edits (PRD §7).
//!
//! Ported from `src/cadreur/interp.py`. Points are kept sorted by `distance_m`;
//! every function is pure — inputs are never mutated.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Loader: two points closer than this — the later one wins.
pub const DEDUP_M: f64 = 0.001;
/// Capture: an existing point this close is replaced (re-capture at a mark).
pub const REPLACE_M: f64 = 0.03;

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Point {
    pub distance_m: f64,
    pub scale: f64,
    pub pos_x: f64,
    pub pos_y: f64,
}

impl Point {
    pub fn values(&self) -> Values {
        Values { scale: self.scale, pos_x: self.pos_x, pos_y: self.pos_y }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Values {
    pub scale: f64,
    pub pos_x: f64,
    pub pos_y: f64,
}

/// Set when the distance falls outside the calibrated range.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Clamped {
    Low,
    High,
}

/// Live per-axis correction. `scale` multiplies, pixels add.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Trim {
    pub scale_mul: f64,
    pub dx_px: f64,
    pub dy_px: f64,
}

impl Default for Trim {
    fn default() -> Self {
        Self { scale_mul: 1.0, dx_px: 0.0, dy_px: 0.0 }
    }
}

/// Python's `float()` accepts numeric strings, so a show file written by an
/// older build with `"3.2"` still loads. Non-finite values are rejected —
/// a deliberate hardening over the Python original, which would let a NaN
/// through and make the sort order undefined.
fn coerce_f64(v: &Value) -> Option<f64> {
    let x = match v {
        Value::Number(n) => n.as_f64()?,
        Value::String(s) => s.trim().parse::<f64>().ok()?,
        _ => return None,
    };
    x.is_finite().then_some(x)
}

/// A well-formed point, or `None` if malformed.
pub fn clean_point(v: &Value) -> Option<Point> {
    let o = v.as_object()?;
    Some(Point {
        distance_m: coerce_f64(o.get("distance_m")?)?,
        scale: coerce_f64(o.get("scale")?)?,
        pos_x: coerce_f64(o.get("pos_x")?)?,
        pos_y: coerce_f64(o.get("pos_y")?)?,
    })
}

fn sort_points(points: &mut [Point]) {
    points.sort_by(|a, b| a.distance_m.total_cmp(&b.distance_m));
}

/// Defensive load: drop malformed, dedup within 1 mm (later in the list wins —
/// a re-capture overwrites), sort by distance.
pub fn normalize_points(raw: &[Value]) -> Vec<Point> {
    let mut kept: Vec<Point> = Vec::new();
    for r in raw {
        let Some(p) = clean_point(r) else { continue };
        match kept.iter().position(|q| (q.distance_m - p.distance_m).abs() < DEDUP_M) {
            Some(i) => kept[i] = p,
            None => kept.push(p),
        }
    }
    sort_points(&mut kept);
    kept
}

/// Sorted insert; an existing point within [`REPLACE_M`] is replaced.
/// Returns the new list and whether a replacement happened.
pub fn insert_point(points: &[Point], new: Point) -> (Vec<Point>, bool) {
    let mut out = points.to_vec();
    let mut nearest: Option<(usize, f64)> = None;
    for (i, q) in out.iter().enumerate() {
        let d = (q.distance_m - new.distance_m).abs();
        if d <= REPLACE_M && nearest.is_none_or(|(_, best)| d < best) {
            nearest = Some((i, d));
        }
    }
    match nearest {
        Some((i, _)) => out[i] = new,
        None => out.push(new),
    }
    sort_points(&mut out);
    (out, nearest.is_some())
}

/// `abs_m` -> values over sorted points.
///
/// N=0 inhibits the beamer entirely, N=1 holds a constant, N>=2 is
/// piecewise-linear and clamped at both ends.
pub fn interpolate(points: &[Point], d: f64) -> (Option<Values>, Option<Clamped>) {
    let (Some(first), Some(last)) = (points.first(), points.last()) else {
        return (None, None);
    };
    if points.len() == 1 {
        return (Some(first.values()), None);
    }
    if d <= first.distance_m {
        return (Some(first.values()), (d < first.distance_m).then_some(Clamped::Low));
    }
    if d >= last.distance_m {
        return (Some(last.values()), (d > last.distance_m).then_some(Clamped::High));
    }
    for w in points.windows(2) {
        let (a, b) = (&w[0], &w[1]);
        if d <= b.distance_m {
            let t = (d - a.distance_m) / (b.distance_m - a.distance_m);
            return (
                Some(Values {
                    scale: a.scale + t * (b.scale - a.scale),
                    pos_x: a.pos_x + t * (b.pos_x - a.pos_x),
                    pos_y: a.pos_y + t * (b.pos_y - a.pos_y),
                }),
                None,
            );
        }
    }
    // Unreachable with sorted points; defensive anyway.
    (Some(last.values()), Some(Clamped::High))
}

/// Post-interpolation correction: scale multiplies, pixels add.
pub fn apply_trim(v: Values, trim: Trim) -> Values {
    Values {
        scale: v.scale * trim.scale_mul,
        pos_x: v.pos_x + trim.dx_px,
        pos_y: v.pos_y + trim.dy_px,
    }
}

/// Fold trim into every point. Distance is untouched.
pub fn bake_trim(points: &[Point], trim: Trim) -> Vec<Point> {
    points
        .iter()
        .map(|p| Point {
            distance_m: p.distance_m,
            scale: p.scale * trim.scale_mul,
            pos_x: p.pos_x + trim.dx_px,
            pos_y: p.pos_y + trim.dy_px,
        })
        .collect()
}

/// Round half to even, matching Python's `round(x, dp)`. Rust's `f64::round`
/// rounds half away from zero, which would disagree on exact ties.
pub fn round_dp(x: f64, dp: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let f = 10f64.powi(dp);
    let y = x * f;
    let fl = y.floor();
    let frac = y - fl;
    let tie = (frac - 0.5).abs() <= f64::EPSILON * y.abs().max(1.0);
    let r = if tie {
        if (fl as i64) % 2 == 0 { fl } else { fl + 1.0 }
    } else if frac > 0.5 {
        fl + 1.0
    } else {
        fl
    };
    r / f
}

/// All outputs are normalised 0..1 -> 4 dp, below the dead-band so rounding
/// never fights the send policy.
pub fn round_for_send(v: Values) -> Values {
    Values { scale: round_dp(v.scale, 4), pos_x: round_dp(v.pos_x, 4), pos_y: round_dp(v.pos_y, 4) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pt(d: f64, s: f64, x: f64, y: f64) -> Point {
        Point { distance_m: d, scale: s, pos_x: x, pos_y: y }
    }

    /// The PRD §6 example set.
    fn points() -> Vec<Point> {
        vec![
            pt(2.10, 0.620, 960.0, 540.0),
            pt(3.20, 0.535, 960.0, 574.0),
            pt(4.30, 0.458, 960.0, 610.0),
        ]
    }

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-7, "{a} != {b}");
    }

    #[test]
    fn exact_at_points() {
        for p in points() {
            let (v, clamped) = interpolate(&points(), p.distance_m);
            assert_eq!(clamped, None);
            let v = v.unwrap();
            close(v.scale, p.scale);
            close(v.pos_x, p.pos_x);
            close(v.pos_y, p.pos_y);
        }
    }

    #[test]
    fn midpoint_worked_example() {
        // PRD §7: at 2.65, t=0.5 -> scale 0.5775, pos_y 557.0
        let (v, clamped) = interpolate(&points(), 2.65);
        assert_eq!(clamped, None);
        let v = v.unwrap();
        close(v.scale, 0.5775);
        close(v.pos_y, 557.0);
    }

    #[test]
    fn clamp_low() {
        let (v, clamped) = interpolate(&points(), 1.00);
        assert_eq!(clamped, Some(Clamped::Low));
        let v = v.unwrap();
        close(v.scale, 0.620);
        close(v.pos_y, 540.0);
    }

    #[test]
    fn clamp_high() {
        let (v, clamped) = interpolate(&points(), 9.99);
        assert_eq!(clamped, Some(Clamped::High));
        let v = v.unwrap();
        close(v.scale, 0.458);
        close(v.pos_y, 610.0);
    }

    #[test]
    fn n0_inhibits() {
        let (v, clamped) = interpolate(&[], 3.0);
        assert!(v.is_none());
        assert_eq!(clamped, None);
    }

    #[test]
    fn n1_constant_hold() {
        let (v, clamped) = interpolate(&points()[1..2], 99.0);
        assert_eq!(clamped, None);
        close(v.unwrap().scale, 0.535);
    }

    #[test]
    fn sorted_insert() {
        let (pts, replaced) = insert_point(&points(), pt(2.80, 0.57, 960.0, 555.0));
        assert!(!replaced);
        let ds: Vec<f64> = pts.iter().map(|p| p.distance_m).collect();
        assert_eq!(ds, vec![2.10, 2.80, 3.20, 4.30]);
    }

    #[test]
    fn merge_replace_within_3cm() {
        let (pts, replaced) = insert_point(&points(), pt(3.21, 0.540, 961.0, 575.0));
        assert!(replaced);
        assert_eq!(pts.len(), 3);
        close(pts[1].distance_m, 3.21);
        close(pts[1].scale, 0.540);
    }

    #[test]
    fn no_merge_beyond_3cm() {
        let (pts, replaced) = insert_point(&points(), pt(3.24, 0.540, 961.0, 575.0));
        assert!(!replaced);
        assert_eq!(pts.len(), 4);
    }

    #[test]
    fn dedup_1mm_keeps_later() {
        let raw = vec![
            json!({"distance_m": 3.2000, "scale": 0.5, "pos_x": 0, "pos_y": 0}),
            json!({"distance_m": 3.2005, "scale": 0.6, "pos_x": 1, "pos_y": 1}),
        ];
        let pts = normalize_points(&raw);
        assert_eq!(pts.len(), 1);
        close(pts[0].scale, 0.6); // later one wins
    }

    #[test]
    fn defensive_sort_and_malformed_drop() {
        let raw = vec![
            json!({"distance_m": 4.0, "scale": 0.4, "pos_x": 0, "pos_y": 0}),
            json!({"distance_m": "junk"}),
            json!({"distance_m": 2.0, "scale": 0.6, "pos_x": 0, "pos_y": 0}),
        ];
        let ds: Vec<f64> = normalize_points(&raw).iter().map(|p| p.distance_m).collect();
        assert_eq!(ds, vec![2.0, 4.0]);
    }

    #[test]
    fn numeric_strings_still_load() {
        let raw = vec![json!({"distance_m": "3.2", "scale": "0.5", "pos_x": 0, "pos_y": 0})];
        assert_eq!(normalize_points(&raw).len(), 1);
    }

    #[test]
    fn non_finite_rejected() {
        let raw = vec![json!({"distance_m": "nan", "scale": 0.5, "pos_x": 0, "pos_y": 0})];
        assert!(normalize_points(&raw).is_empty());
    }

    #[test]
    fn trim_applies() {
        let v = apply_trim(
            Values { scale: 0.5, pos_x: 960.0, pos_y: 540.0 },
            Trim { scale_mul: 1.02, dx_px: -3.0, dy_px: 5.0 },
        );
        close(v.scale, 0.51);
        close(v.pos_x, 957.0);
        close(v.pos_y, 545.0);
    }

    #[test]
    fn trim_bakes() {
        let baked = bake_trim(&points(), Trim { scale_mul: 2.0, dx_px: 1.0, dy_px: -1.0 });
        close(baked[0].scale, 1.240);
        close(baked[0].pos_x, 961.0);
        close(baked[0].pos_y, 539.0);
        close(baked[0].distance_m, 2.10); // distance untouched
    }

    #[test]
    fn rounding() {
        let v = round_for_send(Values { scale: 0.123456, pos_x: 0.0, pos_y: 0.876543 });
        assert_eq!(v.scale, 0.1235);
        assert_eq!(v.pos_y, 0.8765);
    }

    #[test]
    fn rounding_is_half_to_even_like_python() {
        // Python: round(0.00125, 4) == 0.0012, round(0.00135, 4) == 0.0014
        assert_eq!(round_dp(2.5, 0), 2.0);
        assert_eq!(round_dp(3.5, 0), 4.0);
        assert_eq!(round_dp(-2.5, 0), -2.0);
    }
}
