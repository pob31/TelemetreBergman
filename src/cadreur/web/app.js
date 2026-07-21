"use strict";
(() => {
  // ---- i18n: same mechanism as the Pi readout (see web/app.js there) ----
  const I18N = (() => {
    try { return JSON.parse(document.getElementById("i18n").textContent); }
    catch (_) { return { en: {}, fr: {} }; }
  })();
  const pref = (() => {
    const q = new URLSearchParams(location.search).get("lang");
    if (q) { try { localStorage.setItem("cadreur_lang", q); } catch (_) {} return q; }
    try { const s = localStorage.getItem("cadreur_lang"); if (s) return s; } catch (_) {}
    return (navigator.languages && navigator.languages[0]) || navigator.language || "en";
  })();
  const LANG = String(pref).toLowerCase().startsWith("fr") ? "fr" : "en";
  const T = Object.assign({}, I18N.en, I18N[LANG]); // English fills any missing key
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((n) => {
    const k = n.getAttribute("data-i18n");
    if (T[k] != null) n.textContent = T[k];
  });
  const sub = (key, vars) =>
    (T[key] || key).replace(/\{(\w+)\}/g, (_, k) => (vars && vars[k] != null ? vars[k] : "?"));

  // ---- help cards: tap "?" to open, tap anywhere else to close ----
  // Strings live in the i18n block as "Title|Body". Tap-based (not hover):
  // this page is also the tablet control surface.
  const helpCard = document.createElement("div");
  helpCard.className = "help-card hidden";
  helpCard.append(document.createElement("div"), document.createElement("div"));
  helpCard.firstElementChild.className = "help-card-title";
  helpCard.lastElementChild.className = "help-card-body";
  document.body.appendChild(helpCard);
  let helpFor = null;
  const hideHelp = () => { helpCard.classList.add("hidden"); helpFor = null; };
  function showHelp(btn, key) {
    const raw = T[key] || key;
    const bar = raw.indexOf("|");
    helpCard.firstElementChild.textContent = bar < 0 ? "" : raw.slice(0, bar);
    helpCard.lastElementChild.textContent = bar < 0 ? raw : raw.slice(bar + 1);
    helpCard.classList.remove("hidden");
    const r = btn.getBoundingClientRect();
    const left = Math.max(10, Math.min(r.left, window.innerWidth - helpCard.offsetWidth - 10));
    let top = r.bottom + 8;
    if (top + helpCard.offsetHeight > window.innerHeight - 10)
      top = Math.max(10, r.top - helpCard.offsetHeight - 8);
    helpCard.style.left = left + "px";
    helpCard.style.top = top + "px";
    helpFor = btn;
  }
  function addHelp(el, key) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "help-btn";
    btn.textContent = "?";
    btn.setAttribute("aria-label", "help");
    btn.addEventListener("click", (ev) => {
      ev.preventDefault(); // keep host labels/checkboxes from reacting
      ev.stopPropagation();
      if (helpFor === btn) hideHelp(); else showHelp(btn, key);
    });
    // Buttons/inputs/labels get the "?" as a sibling; static containers inline.
    if (["BUTTON", "A", "INPUT", "LABEL"].includes(el.tagName)) el.insertAdjacentElement("afterend", btn);
    else el.appendChild(btn);
  }
  document.querySelectorAll("[data-help]").forEach((el) => addHelp(el, el.dataset.help));
  document.addEventListener("click", (e) => { if (!e.target.closest(".help-card")) hideHelp(); });
  window.addEventListener("scroll", hideHelp, true);

  const $ = (id) => document.getElementById(id);
  const fmt = (v, n) => (v == null ? "—" : Number(v).toFixed(n));
  const MINUS = "−";
  const signed = (v, n) => (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(n);

  let snap = null; // latest snapshot from /stream

  // ---- API helper ----
  const api = (path, body) =>
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    })
      .then(async (r) => { try { return await r.json(); } catch (_) { return { ok: r.ok }; } })
      .catch(() => ({ ok: false, error: "network" }));
  const apiErr = (r) => toast(sub("toast_error", { msg: (r && r.error) || "?" }), 5000);

  // ---- toast ----
  let toastTimer = null;
  function toast(msg, ms = 3500) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
  }

  // ---- modal ----
  let modalOk = null;
  function showModal(title, bodyEl, onOk) {
    $("modal-title").textContent = title;
    const body = $("modal-body");
    body.replaceChildren(bodyEl);
    modalOk = onOk;
    $("modal-okbtn").style.display = onOk ? "" : "none";
    $("modal").classList.remove("hidden");
  }
  function hideModal() { $("modal").classList.add("hidden"); modalOk = null; }
  $("modal-cancel").addEventListener("click", hideModal);
  $("modal-okbtn").addEventListener("click", async () => {
    if (modalOk && (await modalOk()) === false) return; // keep open on invalid input
    hideModal();
  });

  function pointForm(values) {
    const wrap = document.createElement("div");
    wrap.className = "point-form";
    const fields = [
      ["distance_m", T.field_distance, 3], ["scale", T.field_scale, 4],
      ["pos_x", T.field_horizontal, 4], ["pos_y", T.field_vertical, 4],
    ];
    const inputs = {};
    for (const [key, label] of fields) {
      const l = document.createElement("label");
      l.textContent = label;
      const inp = document.createElement("input");
      inp.type = "number";
      inp.step = "any";
      if (values && values[key] != null) inp.value = values[key];
      inputs[key] = inp;
      l.appendChild(inp);
      wrap.appendChild(l);
    }
    return {
      el: wrap,
      read() {
        const out = {};
        for (const k in inputs) {
          const v = parseFloat(inputs[k].value);
          if (!isFinite(v)) return null;
          out[k] = v;
        }
        return out;
      },
    };
  }

  function openPointModal(beamer, index, values) {
    const form = pointForm(values);
    const title = index == null ? T.modal_manual_title : T.modal_edit_title;
    showModal(title, form.el, async () => {
      const p = form.read();
      if (!p) return false;
      const body = index == null ? { op: "add", point: p } : { op: "edit", index, point: p };
      const r = await api(`/api/beamer/${beamer}/points`, body);
      if (!r.ok) apiErr(r);
      return true;
    });
  }

  function openCaptureTimeoutModal(beamer, resp) {
    const box = document.createElement("div");
    const msg = document.createElement("p");
    msg.className = "checklist";
    msg.textContent = resp.checklist || "";
    const sep = document.createElement("p");
    sep.textContent = T.enter_manually;
    sep.className = "muted";
    const form = pointForm({ distance_m: resp.distance_m });
    box.append(msg, sep, form.el);
    showModal(T.modal_capture_title, box, async () => {
      const p = form.read();
      if (!p) return false;
      const r = await api(`/api/beamer/${beamer}/points`, { op: "add", point: p });
      if (!r.ok) apiErr(r);
      return true;
    });
  }

  // ---- header ----
  $("btn-arm").addEventListener("click", () => {
    if (!snap) return;
    api("/api/arm", { armed: !snap.armed });
  });
  $("btn-test").addEventListener("click", async () => {
    const r = await api("/api/test_millumin");
    if (r.ok && r.note === "send-only") toast(T.toast_test_sendonly);
    else if (r.ok) toast(sub("toast_test_ok", { ms: r.latency_ms, layer: r.layer }));
    else if (r.checklist) {
      const p = document.createElement("p");
      p.className = "checklist";
      p.textContent = r.checklist;
      showModal(T.modal_capture_title, p, null);
    } else apiErr(r);
  });

  // ---- look bar ----
  const lookSelect = $("look-select");
  lookSelect.addEventListener("change", () => api("/api/look", { id: lookSelect.value }));
  $("look-new").addEventListener("click", () => {
    const name = prompt(T.prompt_look_name);
    if (name) api("/api/looks", { op: "create", name });
  });
  $("look-dup").addEventListener("click", () => {
    if (snap) api("/api/looks", { op: "duplicate", id: snap.settings.active_look });
  });
  $("look-ren").addEventListener("click", () => {
    if (!snap) return;
    const cur = (snap.looks.find((l) => l.id === snap.settings.active_look) || {}).name || "";
    const name = prompt(T.prompt_look_name, cur);
    if (name) api("/api/looks", { op: "rename", id: snap.settings.active_look, name });
  });
  $("look-del").addEventListener("click", () => {
    if (!snap || !confirm(T.confirm_delete_look)) return;
    api("/api/looks", { op: "delete", id: snap.settings.active_look }).then((r) => {
      if (!r.ok) apiErr(r);
    });
  });

  // ---- show bar ----
  const saveAs = () => {
    const cur = snap && snap.show.file ? snap.show.file.replace(/\.json$/, "") : (snap ? snap.show.name : "");
    const name = prompt(T.prompt_show_name, cur);
    if (name) api("/api/save_as", { name }).then((r) => (r.ok ? toast(sub("toast_saved", { file: r.file })) : apiErr(r)));
  };
  $("btn-save").addEventListener("click", async () => {
    const r = await api("/api/save");
    if (r.ok) toast(sub("toast_saved", { file: r.file }));
    else saveAs(); // no file yet
  });
  $("btn-saveas").addEventListener("click", saveAs);
  $("btn-load").addEventListener("click", async () => {
    const r = await fetch("/api/shows").then((x) => x.json()).catch(() => null);
    const list = document.createElement("div");
    list.className = "show-list";
    if (!r || !r.shows.length) {
      list.textContent = T.no_shows;
    } else {
      for (const f of r.shows) {
        const b = document.createElement("button");
        b.className = "btn";
        b.textContent = f + (f === r.current ? " •" : "");
        b.addEventListener("click", async () => {
          hideModal();
          const res = await api("/api/load", { name: f });
          res.ok ? toast(sub("toast_loaded", { file: res.file })) : apiErr(res);
        });
        list.appendChild(b);
      }
    }
    showModal(T.modal_load_title, list, null);
  });
  $("btn-import").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    let doc;
    try { doc = JSON.parse(await file.text()); }
    catch (_) { return toast(sub("toast_error", { msg: "not JSON" }), 5000); }
    const r = await api("/api/import", doc);
    r.ok ? toast(T.toast_imported, 6000) : apiErr(r);
  });

  // ---- smoothing drawer (built once) ----
  const SMOOTH_KEYS = ["ema_tau_s", "deadband_scale", "slew_scale_per_s", "refresh_hz"];
  const smoothInputs = {};
  for (const k of SMOOTH_KEYS) {
    const l = document.createElement("label");
    l.textContent = T["sm_" + k] || k;
    const inp = document.createElement("input");
    inp.type = "number";
    inp.step = "any";
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (isFinite(v)) api("/api/smoothing", { [k]: v }).then((r) => { if (!r.ok) apiErr(r); });
    });
    smoothInputs[k] = inp;
    l.appendChild(inp);
    addHelp(inp, "help_sm_" + k);
    $("smoothing").appendChild(l);
  }

  // ---- beamer panels ----
  const panels = {};
  document.querySelectorAll(".panel").forEach((root) => {
    const b = root.dataset.beamer;
    const q = (sel) => root.querySelector(sel);
    panels[b] = {
      root, chips: q(".chips"), layer: q(".layer-name"), enable: q(".enable-box"),
      statusDot: q(".status-dot"), statusText: q(".status-text"),
      liveValues: q(".live-values"), calflag: q(".calflag"), calToggle: q(".cal-toggle"),
      capture: q(".capture"), tbody: q(".points tbody"), pointsSig: "",
      manual: q(".manual"),
      driveScale: q(".drive-scale"), driveScaleVal: q(".drive-scale-val"),
      driveVpos: q(".drive-vpos"), driveVposVal: q(".drive-vpos-val"),
      driveHpos: q(".drive-hpos"), driveHposVal: q(".drive-hpos-val"),
      trimScale: q(".trim-scale"), trimX: q(".trim-x"), trimY: q(".trim-y"),
    };
    const p = panels[b];

    // Drive-from-Cadreur: the sliders send manual values live while calibrating.
    const wireDrive = (slider, valSpan, field) =>
      slider.addEventListener("input", () => {
        const v = parseFloat(slider.value);
        valSpan.textContent = v.toFixed(3);
        api(`/api/beamer/${b}/manual`, { [field]: v });
      });
    wireDrive(p.driveScale, p.driveScaleVal, "scale");
    wireDrive(p.driveVpos, p.driveVposVal, "pos_v");
    wireDrive(p.driveHpos, p.driveHposVal, "pos_h");

    p.layer.addEventListener("click", () => {
      const name = prompt(T.prompt_layer, p.layer.textContent === "—" ? "" : p.layer.textContent);
      if (name) api(`/api/beamer/${b}/layer`, { name }).then((r) => { if (!r.ok) apiErr(r); });
    });
    p.enable.addEventListener("change", () => api(`/api/beamer/${b}/enable`, { enabled: p.enable.checked }));
    p.calToggle.addEventListener("click", () => {
      if (snap) api(`/api/beamer/${b}/calibrate`, { on: !snap.calibrate[b] });
    });
    p.capture.addEventListener("click", async () => {
      const r = await api(`/api/beamer/${b}/capture`);
      if (r.ok) {
        toast(sub("toast_captured", {
          d: fmt(r.point.distance_m, 3), s: fmt(r.point.scale, 4),
          x: fmt(r.point.pos_x, 4), y: fmt(r.point.pos_y, 4),
        }) + (r.replaced ? T.toast_replaced : ""));
      } else if (r.error === "timeout") openCaptureTimeoutModal(b, r);
      else apiErr(r);
    });
    root.querySelector(".add-manual").addEventListener("click", () => {
      const d = snap && snap.distance.abs_m;
      openPointModal(b, null, d != null ? { distance_m: d.toFixed(3) } : null);
    });
    p.chips.addEventListener("click", (e) => {
      const mem = e.target.dataset && e.target.dataset.mem;
      if (mem) api("/api/lens_memory", { id: mem });
    });
    p.tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const idx = parseInt(btn.dataset.index, 10);
      const pts = currentPoints(b);
      const row = pts[idx];
      if (!row) return;
      if (btn.dataset.action === "del") {
        if (confirm(T.confirm_delete_point)) {
          const r = await api(`/api/beamer/${b}/points`, { op: "delete", index: idx });
          if (!r.ok) apiErr(r);
        }
      } else if (btn.dataset.action === "edit") {
        openPointModal(b, idx, row);
      } else if (btn.dataset.action === "recap") {
        const live = snap && snap.distance.abs_m;
        const msg = sub("confirm_recapture",
                        { old: fmt(row.distance_m, 3), new: fmt(live, 3) });
        if (confirm(msg)) {
          const r = await api(`/api/beamer/${b}/points`, { op: "recapture", index: idx });
          if (!r.ok) apiErr(r);
        }
      }
    });
    root.querySelectorAll(".btn-nudge").forEach((btn) => {
      btn.addEventListener("click", () => {
        const cset = currentCalSet(b);
        if (!cset) return;
        const key = btn.dataset.trim;
        const val = (cset.trim[key] || (key === "scale_mul" ? 1 : 0)) + parseFloat(btn.dataset.step);
        api(`/api/beamer/${b}/trim`, { [key]: Math.round(val * 10000) / 10000 });
      });
    });
    root.querySelector(".trim-bake").addEventListener("click", () => {
      if (confirm(T.confirm_bake)) api(`/api/beamer/${b}/trim/bake`).then((r) => { if (!r.ok) apiErr(r); });
    });
    root.querySelector(".trim-reset").addEventListener("click", () => {
      if (confirm(T.confirm_reset_trim)) api(`/api/beamer/${b}/trim/reset`);
    });
  });
  panels.front.chips.hidden = false; // lens-memory chips are a front-only thing

  function currentCalSet(b) {
    if (!snap || !snap.look) return null;
    const beamer = snap.look.beamers[b];
    const st = snap.beamers[b];
    if (!beamer || !st) return null;
    return beamer.calibrations[st.cal_key] || null;
  }
  const currentPoints = (b) => { const c = currentCalSet(b); return c ? c.points : []; };

  // ---- rendering ----
  const REASON_KEY = {
    disarmed: "st_disarmed", no_beamer: "st_no_beamer", disabled: "st_disabled",
    uncalibrated: "st_uncalibrated", no_points: "st_no_points",
    calibrating: "st_calibrating", no_distance: "st_no_distance",
  };
  let lookSig = "";

  function render(d) {
    // header: Pi
    const src = d.distance.source;
    $("pi-dot").className = "dot " + (src === "live" ? "ok" : src === "stale" ? "stale" : "off");
    $("pi-text").textContent = (src === "live" ? T.pi_live : src === "stale" ? T.pi_stale : T.pi_disconnected)
      + (d.distance.abs_m != null ? " " + fmt(d.distance.abs_m, 3) + " m" : "");
    // header: Millumin
    const mil = d.millumin;
    $("mil-dot").className = "dot " + (mil.ok == null ? "" : mil.ok ? "ok" : "off");
    $("mil-text").textContent = mil.ok == null ? T.mil_unknown
      : mil.ok ? T.mil_ok + (mil.latency_ms != null ? ` (${mil.latency_ms} ms)` : "") : T.mil_fail;
    // banner: distance problems, then Millumin probe warning
    const banner = $("banner");
    if (src !== "live") {
      banner.textContent = src === "stale" ? T.banner_stale : T.banner_disconnected;
      banner.classList.remove("hidden");
    } else if (mil.warning) {
      banner.textContent = "⚠ " + mil.warning;
      banner.classList.remove("hidden");
    } else banner.classList.add("hidden");
    // arm
    const armBtn = $("btn-arm");
    armBtn.className = "btn arm " + (d.armed ? "armed" : "off");
    armBtn.firstElementChild.textContent = d.armed ? T.arm_on : T.arm_off;

    // distance
    $("abs-m").textContent = fmt(d.distance.abs_m, 3);
    $("stage-m").textContent = d.distance.position_m == null ? "—.———"
      : signed(d.distance.position_m, 3);
    renderTravel(d);

    // look selector (skip rebuild while the operator has it open)
    const sig = JSON.stringify([d.looks, d.settings.active_look]);
    if (sig !== lookSig && document.activeElement !== lookSelect) {
      lookSig = sig;
      lookSelect.replaceChildren(...d.looks.map((lk) => {
        const o = document.createElement("option");
        o.value = lk.id;
        o.textContent = lk.name;
        return o;
      }));
      lookSelect.value = d.settings.active_look;
    }

    renderPanel("front", d);
    renderPanel("rear", d);

    // show bar
    $("show-dot").className = "dot " + (d.show.dirty ? "stale" : "ok");
    $("show-name").textContent = d.show.name;
    $("show-file").textContent = (d.show.file || "(unsaved)")
      + (d.show.dirty ? "" : d.show.autosave ? " · autosaved" : "");

    // smoothing (don't clobber a field being edited)
    for (const k of SMOOTH_KEYS) {
      const inp = smoothInputs[k];
      if (document.activeElement !== inp) inp.value = d.smoothing[k];
    }
  }

  function renderTravel(d) {
    const front = calPoints(d, "front"), rear = calPoints(d, "rear");
    const all = front.concat(rear).map((p) => p.distance_m);
    const cart = $("cart");
    if (!all.length) {
      $("ticks-front").replaceChildren();
      $("ticks-rear").replaceChildren();
      $("range-min").textContent = $("range-max").textContent = "—";
      cart.classList.add("hidden");
      return;
    }
    let min = Math.min(...all), max = Math.max(...all);
    if (max - min < 0.2) { min -= 0.1; max += 0.1; }
    const pos = (v) => Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100)) + "%";
    const ticks = (pts) => pts.map((p) => {
      const t = document.createElement("span");
      t.className = "tick";
      t.style.left = pos(p.distance_m);
      return t;
    });
    $("ticks-front").replaceChildren(...ticks(front));
    $("ticks-rear").replaceChildren(...ticks(rear));
    $("range-min").textContent = fmt(min, 2);
    $("range-max").textContent = fmt(max, 2);
    if (d.distance.abs_m != null) {
      cart.style.left = pos(d.distance.abs_m);
      cart.classList.remove("hidden");
    } else cart.classList.add("hidden");
  }
  function calPoints(d, b) {
    const beamer = d.look && d.look.beamers[b];
    const st = d.beamers[b];
    if (!beamer || !st) return [];
    const cset = beamer.calibrations[st.cal_key];
    return cset ? cset.points : [];
  }

  function renderPanel(b, d) {
    const p = panels[b];
    const st = d.beamers[b] || {};
    const beamer = d.look && d.look.beamers[b];

    p.layer.textContent = (beamer && beamer.layer) || "—";
    if (document.activeElement !== p.enable) p.enable.checked = !!(beamer && beamer.enabled);

    if (b === "front") {
      const chipSig = JSON.stringify([d.lens_memories, d.settings.active_lens_memory,
                                      beamer ? Object.keys(beamer.calibrations) : null]);
      if (p.chipSig !== chipSig) {
        p.chipSig = chipSig;
        p.chips.replaceChildren(...d.lens_memories.map((m) => {
          const c = document.createElement("button");
          c.className = "chip" + (m === d.settings.active_lens_memory ? " active" : "")
            + (beamer && beamer.calibrations[m] && beamer.calibrations[m].points.length ? "" : " hollow");
          c.dataset.mem = m;
          c.textContent = m;
          return c;
        }));
      }
    }

    // status line
    let key, cls;
    if (st.reason) {
      key = REASON_KEY[st.reason] || st.reason;
      cls = st.reason === "calibrating" ? "warn" : "off";
    } else if (st.clamped) {
      key = st.clamped === "low" ? "st_clamped_low" : "st_clamped_high";
      cls = "stale";
    } else { key = "st_ok"; cls = "ok"; }
    p.statusDot.className = "status-dot dot " + cls;
    p.statusText.textContent = key === "st_uncalibrated"
      ? sub(key, { mem: st.cal_key }) : (T[key] || key);

    const v = st.values;
    p.liveValues.textContent = !v ? "—"
      : `échelle ${fmt(v.scale, 4)}   ·   H ${fmt(v.pos_x, 4)}   ·   V ${fmt(v.pos_y, 4)}`
        + (st.sending ? " · " + T.sending : "");

    const calOn = !!d.calibrate[b];
    p.calflag.classList.toggle("hidden", !calOn);
    p.manual.classList.toggle("hidden", !calOn);
    // Reflect the current drive values on the sliders unless the operator is
    // dragging one right now.
    const man = d.manual && d.manual[b];
    const syncSlider = (slider, valSpan, val) => {
      if (val != null && document.activeElement !== slider) {
        slider.value = val;
        valSpan.textContent = Number(val).toFixed(3);
      }
    };
    if (man) {
      syncSlider(p.driveScale, p.driveScaleVal, man.scale);
      syncSlider(p.driveVpos, p.driveVposVal, man.pos_v);
      syncSlider(p.driveHpos, p.driveHposVal, man.pos_h);
    }
    p.calToggle.textContent = calOn ? T.calibrate_exit : T.calibrate_mode;
    p.calToggle.classList.toggle("warn", calOn);
    p.capture.disabled = !calOn || d.distance.source !== "live";

    // points table
    const pts = currentPoints(b);
    const sig = JSON.stringify(pts);
    if (p.pointsSig !== sig) {
      p.pointsSig = sig;
      p.tbody.replaceChildren(...pts.map((pt, i) => {
        const tr = document.createElement("tr");
        const cells = [fmt(pt.distance_m, 3), fmt(pt.scale, 4), fmt(pt.pos_x, 4), fmt(pt.pos_y, 4)];
        for (const c of cells) {
          const td = document.createElement("td");
          td.textContent = c;
          tr.appendChild(td);
        }
        const td = document.createElement("td");
        td.className = "row-actions";
        for (const [action, label] of [["edit", "✎"], ["recap", "↻"], ["del", "🗑"]]) {
          const btn = document.createElement("button");
          btn.dataset.action = action;
          btn.dataset.index = i;
          btn.textContent = label;
          td.appendChild(btn);
        }
        tr.appendChild(td);
        return tr;
      }));
    }

    const cset = currentCalSet(b);
    const trim = cset ? cset.trim : { scale_mul: 1, dx_px: 0, dy_px: 0 };
    p.trimScale.textContent = "×" + fmt(trim.scale_mul, 3);
    p.trimX.textContent = signed(trim.dx_px, 3);
    p.trimY.textContent = signed(trim.dy_px, 3);
  }

  // ---- live stream (EventSource auto-reconnects) ----
  function connect() {
    const es = new EventSource("/stream");
    es.onmessage = (e) => {
      try { snap = JSON.parse(e.data); render(snap); } catch (_) {}
    };
    es.onerror = () => {
      $("pi-dot").className = "dot off";
      $("mil-dot").className = "dot off";
      const banner = $("banner");
      banner.textContent = T.banner_app_offline || "CADREUR OFFLINE — reconnecting…";
      banner.classList.remove("hidden");
    };
  }
  connect();
})();
