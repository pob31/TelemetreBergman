"use strict";
(() => {
  // ---- i18n (same mechanism as the Pi readout) ----
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
  const T = Object.assign({}, I18N.en, I18N[LANG]);
  document.documentElement.lang = LANG;
  const sub = (key, vars) =>
    (T[key] || key).replace(/\{(\w+)\}/g, (_, k) => (vars && vars[k] != null ? vars[k] : "?"));

  // ---- help cards ----
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
      ev.preventDefault();
      ev.stopPropagation();
      if (helpFor === btn) hideHelp(); else showHelp(btn, key);
    });
    if (["BUTTON", "A", "INPUT", "LABEL"].includes(el.tagName)) el.insertAdjacentElement("afterend", btn);
    else el.appendChild(btn);
  }
  function applyI18n(root) {
    root.querySelectorAll("[data-i18n]").forEach((n) => {
      const k = n.getAttribute("data-i18n");
      if (T[k] != null) n.textContent = T[k];
    });
    root.querySelectorAll("[data-help]").forEach((el) => {
      if (!el.dataset.helpWired) { el.dataset.helpWired = "1"; addHelp(el, el.dataset.help); }
    });
  }
  applyI18n(document);
  document.addEventListener("click", (e) => { if (!e.target.closest(".help-card")) hideHelp(); });
  window.addEventListener("scroll", hideHelp, true);

  const $ = (id) => document.getElementById(id);
  const fmt = (v, n) => (v == null ? "—" : Number(v).toFixed(n));
  const MINUS = "−";
  const signed = (v, n) => (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(n);

  let snap = null;

  const api = (path, body) =>
    fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
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
    $("modal-body").replaceChildren(bodyEl);
    modalOk = onOk;
    $("modal-okbtn").style.display = onOk ? "" : "none";
    $("modal").classList.remove("hidden");
  }
  function hideModal() { $("modal").classList.add("hidden"); modalOk = null; }
  $("modal-cancel").addEventListener("click", hideModal);
  $("modal-okbtn").addEventListener("click", async () => {
    if (modalOk && (await modalOk()) === false) return;
    hideModal();
  });

  function fieldForm(fields, values) {
    const wrap = document.createElement("div");
    wrap.className = "point-form";
    const inputs = {};
    for (const [key, label, type] of fields) {
      const l = document.createElement("label");
      l.textContent = label;
      const inp = document.createElement("input");
      inp.type = type || "number";
      if (inp.type === "number") inp.step = "any";
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
          const inp = inputs[k];
          out[k] = inp.type === "number" ? parseFloat(inp.value) : inp.value;
        }
        return out;
      },
    };
  }

  function pointForm(values) {
    const f = fieldForm([
      ["distance_m", T.field_distance], ["scale", T.field_scale],
      ["pos_x", T.field_horizontal], ["pos_y", T.field_vertical],
    ], values);
    const read0 = f.read;
    f.read = () => {
      const o = read0();
      for (const k of ["distance_m", "scale", "pos_x", "pos_y"]) if (!isFinite(o[k])) return null;
      return o;
    };
    return f;
  }

  function openPointModal(beamer, cid, index, values) {
    const form = pointForm(values);
    const title = index == null ? T.modal_manual_title : T.modal_edit_title;
    showModal(title, form.el, async () => {
      const p = form.read();
      if (!p) return false;
      const body = index == null ? { op: "add", point: p } : { op: "edit", index, point: p };
      const r = await api(`/api/channel/${beamer}/${cid}/points`, body);
      if (!r.ok) apiErr(r);
      return true;
    });
  }

  function openOscModal(beamer, cid, ch) {
    const form = fieldForm([
      ["osc_scale", T.field_osc_scale, "text"],
      ["osc_posh", T.field_osc_posh, "text"],
      ["osc_posv", T.field_osc_posv, "text"],
      ["osc_show", T.field_osc_show, "text"],
    ], { osc_scale: ch.osc_scale, osc_posh: ch.osc_posh, osc_posv: ch.osc_posv, osc_show: ch.osc_show });
    showModal(T.modal_osc_title, form.el, async () => {
      const r = await api(`/api/channel/${beamer}/${cid}/osc`, form.read());
      if (!r.ok) { apiErr(r); return false; }
      return true;
    });
  }

  // ---- header ----
  $("btn-arm").addEventListener("click", () => { if (snap) api("/api/arm", { armed: !snap.armed }); });
  $("btn-test").addEventListener("click", async () => {
    const r = await api("/api/test_millumin");
    if (r.ok && r.note === "send-only") toast(T.toast_test_sendonly);
    else if (r.ok) toast(sub("toast_test_ok", { ms: r.latency_ms, layer: r.layer }));
    else apiErr(r);
  });
  $("btn-capture-all").addEventListener("click", async () => {
    const r = await api("/api/capture_all");
    if (r.ok) toast(sub("toast_captured_all", { n: r.count, d: fmt(r.distance_m, 3) }));
    else apiErr(r);
  });

  // ---- show bar ----
  const saveAs = () => {
    const cur = snap && snap.show.file ? snap.show.file.replace(/\.json$/, "") : (snap ? snap.show.name : "");
    const name = prompt(T.prompt_show_name, cur);
    if (name) api("/api/save_as", { name }).then((r) => (r.ok ? toast(sub("toast_saved", { file: r.file })) : apiErr(r)));
  };
  $("btn-save").addEventListener("click", async () => {
    const r = await api("/api/save");
    if (r.ok) toast(sub("toast_saved", { file: r.file })); else saveAs();
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

  // ---- smoothing drawer ----
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

  // ---- beamer columns: add-channel + lens chips ----
  document.querySelectorAll(".beamer-col").forEach((col) => {
    const beamer = col.dataset.beamer;
    col.querySelector(".add-channel").addEventListener("click", () => {
      const name = prompt(T.prompt_channel_name);
      api(`/api/beamer/${beamer}/channel/add`, name ? { name } : {}).then((r) => { if (!r.ok) apiErr(r); });
    });
    const chips = col.querySelector(".chips");
    if (chips) chips.addEventListener("click", (e) => {
      const mem = e.target.dataset && e.target.dataset.mem;
      if (mem) api("/api/lens_memory", { id: mem });
    });
  });

  // ---- channel cards (cloned from the <template>) ----
  const tpl = $("channel-tpl");
  const cards = {}; // "beamer/cid" -> refs

  // ---- Precision toggle: 10x finer drive-slider steps for fine tuning ----
  const COARSE_STEP = 0.001, FINE_STEP = 0.0001;
  let precision = false;
  try { precision = localStorage.getItem("cadreur_precision") === "1"; } catch (_) {}
  const driveStep = () => String(precision ? FINE_STEP : COARSE_STEP);
  function applyPrecision() {
    for (const key in cards) {
      const c = cards[key];
      c.driveScale.step = c.driveVpos.step = c.driveHpos.step = driveStep();
    }
    $("btn-precision").classList.toggle("active", precision);
  }
  $("btn-precision").addEventListener("click", () => {
    precision = !precision;
    try { localStorage.setItem("cadreur_precision", precision ? "1" : "0"); } catch (_) {}
    applyPrecision();
  });

  const findCh = (beamer, cid) => (snap && (snap.beamers[beamer] || []).find((c) => c.id === cid)) || null;

  function buildCard(beamer, cid) {
    const el = tpl.content.firstElementChild.cloneNode(true);
    el.dataset.cid = cid;
    applyI18n(el);
    const q = (s) => el.querySelector(s);
    const c = {
      el, beamer, cid, pointsSig: "",
      name: q(".ch-name"), enable: q(".enable-box"), oscBtn: q(".ch-osc"), delBtn: q(".ch-del"),
      statusDot: q(".status-dot"), statusText: q(".status-text"), live: q(".live-values"),
      calflag: q(".calflag"), manual: q(".manual"), calToggle: q(".cal-toggle"), capture: q(".capture"),
      driveScale: q(".drive-scale"), driveScaleVal: q(".drive-scale-val"),
      driveVpos: q(".drive-vpos"), driveVposVal: q(".drive-vpos-val"),
      driveHpos: q(".drive-hpos"), driveHposVal: q(".drive-hpos-val"),
      tbody: q(".points tbody"), trimScale: q(".trim-scale"), trimX: q(".trim-x"), trimY: q(".trim-y"),
      showBtn: q(".ch-show"),
    };
    const P = `/api/channel/${beamer}/${cid}`;
    c.shown = false;  // local "layer shown in Millumin" state (no readback)
    c.driveScale.step = c.driveVpos.step = c.driveHpos.step = driveStep();
    const updateShowBtn = () => {
      c.showBtn.textContent = c.shown ? T.hide_layer : T.show_layer;
      c.showBtn.classList.toggle("warn", c.shown);
    };
    c.showBtn.addEventListener("click", () => {
      c.shown = !c.shown;
      api(`${P}/show`, { on: c.shown }).then((r) => { if (!r.ok) apiErr(r); });
      updateShowBtn();
    });
    c.name.addEventListener("click", () => {
      const name = prompt(T.prompt_channel_name, c.name.textContent);
      if (name) api(`${P}/rename`, { name }).then((r) => { if (!r.ok) apiErr(r); });
    });
    c.enable.addEventListener("change", () => api(`${P}/enable`, { enabled: c.enable.checked }));
    c.oscBtn.addEventListener("click", () => { const ch = findCh(beamer, cid); if (ch) openOscModal(beamer, cid, ch); });
    c.delBtn.addEventListener("click", () => {
      if (confirm(T.confirm_delete_channel)) api(`${P}/delete`).then((r) => { if (!r.ok) apiErr(r); });
    });
    c.calToggle.addEventListener("click", () => {
      const ch = findCh(beamer, cid);
      api(`${P}/calibrate`, { on: !(ch && ch.calibrating) });
    });
    c.capture.addEventListener("click", async () => {
      const r = await api(`${P}/capture`);
      if (r.ok) toast(sub("toast_captured", {
        d: fmt(r.point.distance_m, 3), s: fmt(r.point.scale, 4),
        x: fmt(r.point.pos_x, 4), y: fmt(r.point.pos_y, 4),
      }) + (r.replaced ? T.toast_replaced : ""));
      else apiErr(r);
    });
    const wire = (sl, val, field) => sl.addEventListener("input", () => {
      const v = parseFloat(sl.value);
      val.textContent = v.toFixed(3);
      api(`${P}/manual`, { [field]: v });
    });
    wire(c.driveScale, c.driveScaleVal, "scale");
    wire(c.driveVpos, c.driveVposVal, "pos_v");
    wire(c.driveHpos, c.driveHposVal, "pos_h");
    el.querySelector(".add-manual").addEventListener("click", () => {
      const d = snap && snap.distance.abs_m;
      openPointModal(beamer, cid, null, d != null ? { distance_m: d.toFixed(3) } : null);
    });
    c.tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const idx = parseInt(btn.dataset.index, 10);
      const ch = findCh(beamer, cid);
      const row = ch && ch.points && ch.points[idx];
      if (!row) return;
      if (btn.dataset.action === "del") {
        if (confirm(T.confirm_delete_point)) {
          const r = await api(`${P}/points`, { op: "delete", index: idx });
          if (!r.ok) apiErr(r);
        }
      } else if (btn.dataset.action === "edit") {
        openPointModal(beamer, cid, idx, row);
      } else if (btn.dataset.action === "recap") {
        const live = snap && snap.distance.abs_m;
        if (confirm(sub("confirm_recapture", { old: fmt(row.distance_m, 3), new: fmt(live, 3) }))) {
          const r = await api(`${P}/points`, { op: "recapture", index: idx });
          if (!r.ok) apiErr(r);
        }
      }
    });
    el.querySelectorAll(".btn-nudge").forEach((btn) => btn.addEventListener("click", () => {
      const ch = findCh(beamer, cid);
      const trim = (ch && ch.trim) || { scale_mul: 1, dx_px: 0, dy_px: 0 };
      const key = btn.dataset.trim;
      const val = (trim[key] != null ? trim[key] : (key === "scale_mul" ? 1 : 0)) + parseFloat(btn.dataset.step);
      api(`${P}/trim`, { [key]: Math.round(val * 10000) / 10000 });
    }));
    el.querySelector(".trim-bake").addEventListener("click", () => {
      if (confirm(T.confirm_bake)) api(`${P}/trim/bake`).then((r) => { if (!r.ok) apiErr(r); });
    });
    el.querySelector(".trim-reset").addEventListener("click", () => {
      if (confirm(T.confirm_reset_trim)) api(`${P}/trim/reset`);
    });
    return c;
  }

  // ---- rendering ----
  const REASON_KEY = {
    disarmed: "st_disarmed", disabled: "st_disabled", uncalibrated: "st_uncalibrated",
    no_points: "st_no_points", calibrating: "st_calibrating", no_distance: "st_no_distance",
  };

  function render(d) {
    const src = d.distance.source;
    $("pi-dot").className = "dot " + (src === "live" ? "ok" : src === "stale" ? "stale" : "off");
    $("pi-text").textContent = (src === "live" ? T.pi_live : src === "stale" ? T.pi_stale : T.pi_disconnected)
      + (d.distance.abs_m != null ? " " + fmt(d.distance.abs_m, 3) + " m" : "");
    const mil = d.millumin;
    $("mil-dot").className = "dot " + (mil.ok == null ? "" : mil.ok ? "ok" : "off");
    $("mil-text").textContent = mil.ok == null ? T.mil_unknown
      : mil.ok ? T.mil_ok + (mil.latency_ms != null ? ` (${mil.latency_ms} ms)` : "") : T.mil_fail;
    const banner = $("banner");
    if (src !== "live") {
      banner.textContent = src === "stale" ? T.banner_stale : T.banner_disconnected;
      banner.classList.remove("hidden");
    } else if (mil.warning) {
      banner.textContent = "⚠ " + mil.warning;
      banner.classList.remove("hidden");
    } else banner.classList.add("hidden");
    const armBtn = $("btn-arm");
    armBtn.className = "btn arm " + (d.armed ? "armed" : "off");
    armBtn.firstElementChild.textContent = d.armed ? T.arm_on : T.arm_off;

    $("abs-m").textContent = fmt(d.distance.abs_m, 3);
    $("stage-m").textContent = d.distance.position_m == null ? "—.———" : signed(d.distance.position_m, 3);
    renderTravel(d);

    const anyCal = ["front", "rear"].some((b) => (d.beamers[b] || []).some((c) => c.calibrating));
    $("capture-all-bar").classList.toggle("hidden", !anyCal);

    renderBeamer("front", d);
    renderBeamer("rear", d);

    $("show-dot").className = "dot " + (d.show.dirty ? "stale" : "ok");
    $("show-name").textContent = d.show.name;
    $("show-file").textContent = (d.show.file || "(unsaved)")
      + (d.show.dirty ? "" : d.show.autosave ? " · autosaved" : "");
    for (const k of SMOOTH_KEYS) {
      const inp = smoothInputs[k];
      if (document.activeElement !== inp) inp.value = d.smoothing[k];
    }
  }

  function renderBeamer(beamer, d) {
    const col = document.querySelector(`.beamer-col[data-beamer="${beamer}"]`);
    const container = col.querySelector(".channels");
    const list = d.beamers[beamer] || [];

    const chips = col.querySelector(".chips");
    if (chips) {
      const sig = JSON.stringify([d.lens_memories, d.settings.active_lens_memory, list.map((c) => c.cal_key)]);
      if (chips.dataset.sig !== sig) {
        chips.dataset.sig = sig;
        chips.replaceChildren(...d.lens_memories.map((m) => {
          const b = document.createElement("button");
          const hasPoints = list.some((c) => (c.cal_key === m) && c.n_points);
          b.className = "chip" + (m === d.settings.active_lens_memory ? " active" : "") + (hasPoints ? "" : " hollow");
          b.dataset.mem = m;
          b.textContent = m;
          return b;
        }));
      }
    }

    const wantIds = list.map((c) => c.id);
    for (const key of Object.keys(cards)) {
      if (cards[key].beamer === beamer && !wantIds.includes(cards[key].cid)) {
        cards[key].el.remove();
        delete cards[key];
      }
    }
    list.forEach((ch, i) => {
      const key = beamer + "/" + ch.id;
      const c = cards[key] || (cards[key] = buildCard(beamer, ch.id));
      if (container.children[i] !== c.el) container.insertBefore(c.el, container.children[i] || null);
      updateCard(c, ch, d);
    });
  }

  function updateCard(c, ch, d) {
    if (document.activeElement !== c.name) c.name.textContent = ch.name;
    if (document.activeElement !== c.enable) c.enable.checked = !!ch.enabled;

    let key, cls;
    if (ch.reason) { key = REASON_KEY[ch.reason] || ch.reason; cls = ch.reason === "calibrating" ? "warn" : "off"; }
    else if (ch.clamped) { key = ch.clamped === "low" ? "st_clamped_low" : "st_clamped_high"; cls = "stale"; }
    else { key = "st_ok"; cls = "ok"; }
    c.statusDot.className = "status-dot dot " + cls;
    c.statusText.textContent = key === "st_uncalibrated" ? sub(key, { mem: ch.cal_key }) : (T[key] || key);

    const v = ch.values;
    c.live.textContent = !v ? "—"
      : `échelle ${fmt(v.scale, 4)} · H ${fmt(v.pos_x, 4)} · V ${fmt(v.pos_y, 4)}` + (ch.sending ? " · " + T.sending : "");

    const calOn = !!ch.calibrating;
    c.calflag.classList.toggle("hidden", !calOn);
    c.manual.classList.toggle("hidden", !calOn);
    c.calToggle.textContent = calOn ? T.calibrate_exit : T.calibrate_mode;
    c.calToggle.classList.toggle("warn", calOn);
    c.capture.disabled = !calOn || d.distance.source !== "live";

    const man = ch.manual;
    if (man) {
      const sync = (sl, val, x) => {
        if (x != null && document.activeElement !== sl) { sl.value = x; val.textContent = Number(x).toFixed(3); }
      };
      sync(c.driveScale, c.driveScaleVal, man.scale);
      sync(c.driveVpos, c.driveVposVal, man.pos_v);
      sync(c.driveHpos, c.driveHposVal, man.pos_h);
    }

    const pts = ch.points || [];
    const sig = JSON.stringify(pts);
    if (c.pointsSig !== sig) {
      c.pointsSig = sig;
      c.tbody.replaceChildren(...pts.map((pt, i) => {
        const tr = document.createElement("tr");
        for (const cell of [fmt(pt.distance_m, 3), fmt(pt.scale, 4), fmt(pt.pos_x, 4), fmt(pt.pos_y, 4)]) {
          const td = document.createElement("td");
          td.textContent = cell;
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

    const trim = ch.trim || { scale_mul: 1, dx_px: 0, dy_px: 0 };
    c.trimScale.textContent = fmt(trim.scale_mul, 3);
    c.trimX.textContent = signed(trim.dx_px, 4);  // H/V trim nudges are 10x finer
    c.trimY.textContent = signed(trim.dy_px, 4);
  }

  function renderTravel(d) {
    const front = (d.beamers.front || []).flatMap((c) => c.points || []);
    const rear = (d.beamers.rear || []).flatMap((c) => c.points || []);
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

  function connect() {
    const es = new EventSource("/stream");
    es.onmessage = (e) => { try { snap = JSON.parse(e.data); render(snap); } catch (_) {} };
    es.onerror = () => {
      $("pi-dot").className = "dot off";
      $("mil-dot").className = "dot off";
      const banner = $("banner");
      banner.textContent = T.banner_app_offline || "CADREUR OFFLINE — reconnecting…";
      banner.classList.remove("hidden");
    };
  }
  applyPrecision();  // reflect the persisted precision state on the button
  connect();
})();
