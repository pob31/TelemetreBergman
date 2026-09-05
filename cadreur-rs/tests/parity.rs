//! Differential test against the Python reference implementation.
//!
//! `tests/fixtures/interp_cases.json` holds 400 pseudo-random cases with the
//! outputs produced by `src/cadreur/interp.py`. The port must reproduce them
//! exactly — this is what makes the rewrite defensible on the maths that keeps
//! the projection locked to the scrim.
//!
//! Regenerate with `.venv/bin/python cadreur-rs/tests/gen_fixtures.py`.

use cadreur::interp::{
    Clamped, Point, insert_point, interpolate, normalize_points, round_for_send,
};
use serde_json::Value;

fn point_of(v: &Value) -> Point {
    Point {
        distance_m: v["distance_m"].as_f64().expect("distance_m"),
        scale: v["scale"].as_f64().expect("scale"),
        pos_x: v["pos_x"].as_f64().expect("pos_x"),
        pos_y: v["pos_y"].as_f64().expect("pos_y"),
    }
}

/// Python writes floats via repr; compare on a tolerance well below the
/// 0.0005 dead-band rather than demanding bit equality.
fn close(a: f64, b: f64, what: &str, case: usize) {
    assert!((a - b).abs() < 1e-9, "case {case}: {what}: rust {a} != python {b}");
}

#[test]
fn matches_python_reference() {
    let raw = include_str!("fixtures/interp_cases.json");
    let doc: Value = serde_json::from_str(raw).expect("fixture parses");
    let cases = doc["cases"].as_array().expect("cases array");
    assert_eq!(cases.len(), 400, "fixture size changed unexpectedly");

    for (i, c) in cases.iter().enumerate() {
        // --- normalize_points: dedup within 1 mm, later wins, then sort ---
        let input = c["raw"].as_array().expect("raw array");
        let got = normalize_points(input);
        let want: Vec<Point> =
            c["normalized"].as_array().expect("normalized").iter().map(point_of).collect();
        assert_eq!(got.len(), want.len(), "case {i}: normalized length");
        for (g, w) in got.iter().zip(&want) {
            close(g.distance_m, w.distance_m, "normalized.distance_m", i);
            close(g.scale, w.scale, "normalized.scale", i);
            close(g.pos_x, w.pos_x, "normalized.pos_x", i);
            close(g.pos_y, w.pos_y, "normalized.pos_y", i);
        }

        // --- interpolate + round_for_send ---
        let query = c["query"].as_f64().expect("query");
        let (values, clamped) = interpolate(&got, query);
        match (&c["values"], values) {
            (Value::Null, None) => {}
            (want, Some(v)) => {
                let v = round_for_send(v);
                close(v.scale, want["scale"].as_f64().expect("scale"), "values.scale", i);
                close(v.pos_x, want["pos_x"].as_f64().expect("pos_x"), "values.pos_x", i);
                close(v.pos_y, want["pos_y"].as_f64().expect("pos_y"), "values.pos_y", i);
            }
            (want, got) => panic!("case {i}: values mismatch: rust {got:?} vs python {want}"),
        }
        let want_clamped = match c["clamped"].as_str() {
            Some("low") => Some(Clamped::Low),
            Some("high") => Some(Clamped::High),
            _ => None,
        };
        assert_eq!(clamped, want_clamped, "case {i}: clamped");

        // --- insert_point: replace within 3 cm, else sorted insert ---
        let (inserted, replaced) = insert_point(&got, point_of(&c["insert"]));
        assert_eq!(replaced, c["replaced"].as_bool().expect("replaced"), "case {i}: replaced");
        let want_ins: Vec<Point> =
            c["inserted"].as_array().expect("inserted").iter().map(point_of).collect();
        assert_eq!(inserted.len(), want_ins.len(), "case {i}: inserted length");
        for (g, w) in inserted.iter().zip(&want_ins) {
            close(g.distance_m, w.distance_m, "inserted.distance_m", i);
            close(g.scale, w.scale, "inserted.scale", i);
        }
    }
}

/// The Rust loader must agree with the Python on a real show file, not just on
/// generated cases. Compared as `Value`, so key ordering is irrelevant.
#[test]
fn loads_the_real_example_show_like_python() {
    let want: Value = serde_json::from_str(include_str!("fixtures/example_show_normalized.json"))
        .expect("fixture parses");
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("shows")
        .join("example-show.json");
    let got = cadreur::show::load_show(&path).expect("loads").to_value();
    assert_eq!(got, want, "Rust and Python disagree on the example show");
}
