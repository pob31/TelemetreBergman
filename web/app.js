"use strict";
(() => {
  // ---- i18n: French when the device's default language is French, else English.
  // Strings live in the <script id="i18n"> JSON block in index.html (UTF-8).
  const I18N = (() => {
    try { return JSON.parse(document.getElementById("i18n").textContent); }
    catch (_) { return { en: {}, fr: {} }; }
  })();
  // Language: ?lang=fr|en forces + remembers a choice; otherwise the device's
  // preferred language decides (French => fr, anything else => en).
  const pref = (() => {
    const q = new URLSearchParams(location.search).get("lang");
    if (q) { try { localStorage.setItem("tb_lang", q); } catch (_) {} return q; }
    try { const s = localStorage.getItem("tb_lang"); if (s) return s; } catch (_) {}
    return (navigator.languages && navigator.languages[0]) || navigator.language || "en";
  })();
  const LANG = String(pref).toLowerCase().startsWith("fr") ? "fr" : "en";
  const T = Object.assign({}, I18N.en, I18N[LANG]); // English fills any missing key
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((n) => {
    const k = n.getAttribute("data-i18n");
    if (T[k] != null) n.textContent = T[k];
  });

  const app = document.querySelector(".app");
  const $ = (id) => document.getElementById(id);
  const el = {
    connDot: $("conn-dot"), connText: $("conn-text"), banner: $("banner"),
    position: $("position"), raw: $("raw"), zero: $("zero"), dir: $("dir"),
    signalFill: $("signal-fill"), signalVal: $("signal-val"), temp: $("temp"),
    overlay: $("overlay"), overlayMsg: $("overlay-msg"),
  };

  const MINUS = "−"; // real minus sign, matches "+" width
  const fmtSigned = (v) => (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(2);
  const fmt2 = (v) => (v == null ? "—.—" : Math.abs(v).toFixed(2));
  const setState = (name) => { app.dataset.state = name; };

  // Power-action overlay handling: only clear it after a real drop+recover,
  // so the last few buffered frames don't flicker it away.
  let rebooting = false, sawDrop = false;

  function render(d) {
    if (!d.connected) {
      el.connDot.className = "dot off";
      el.connText.textContent = d.port ? T.lost + " " + d.port : T.no_sensor;
      setState("offline");
      el.banner.textContent = T.sensor_offline; el.banner.classList.remove("hidden");
    } else if (d.stale) {
      el.connDot.className = "dot stale";
      el.connText.textContent = T.weak;
      setState("stale");
      el.banner.textContent = T.no_signal; el.banner.classList.remove("hidden");
    } else {
      el.connDot.className = "dot ok";
      el.connText.textContent = d.port || T.connected;
      setState("live");
      el.banner.classList.add("hidden");
    }

    el.position.textContent =
      (d.position_m == null || d.stale || !d.connected) ? "—.—" : fmtSigned(d.position_m);
    el.raw.textContent = fmt2(d.raw_m);
    el.zero.textContent = ((d.zero_cm || 0) / 100).toFixed(2);
    el.dir.textContent = d.sign < 0 ? "←" : "→";

    const strength = d.strength || 0;
    el.signalFill.style.width = Math.max(0, Math.min(100, strength / 1000 * 100)) + "%";
    el.signalVal.textContent = strength;
    el.temp.textContent = (d.temp_c != null ? Math.round(d.temp_c) : "—") + "°C";

    if (rebooting && sawDrop && d.connected && !d.stale) {
      el.overlay.classList.add("hidden"); rebooting = false;
    }
  }

  // ---- live stream (EventSource auto-reconnects) ----
  function connect() {
    const es = new EventSource("/stream");
    es.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
    es.onerror = () => {
      el.connDot.className = "dot off";
      el.connText.textContent = T.reconnecting;
      setState("offline");
      if (rebooting) sawDrop = true;
    };
  }
  connect();

  // ---- controls ----
  const post = (path) => fetch(path, { method: "POST" }).catch(() => {});
  // Power actions: surface an explicit backend failure. A dropped connection is
  // expected on success, so treat that as "in progress" rather than an error.
  const postPower = (path) =>
    fetch(path, { method: "POST" })
      .then((r) => (r.ok ? { ok: true } : r.json().catch(() => ({})).then((j) => ({ ok: false, error: j.error }))))
      .catch(() => ({ ok: true }));
  const flash = (btn) => { btn.classList.add("flash"); setTimeout(() => btn.classList.remove("flash"), 180); };
  const bind = (id, fn) => $(id).addEventListener("click", (e) => fn(e.currentTarget));

  bind("btn-zero", (b) => { flash(b); post("/api/tare"); });
  bind("btn-clear", (b) => { flash(b); post("/api/clear_zero"); });
  bind("btn-invert", (b) => { flash(b); post("/api/invert"); });

  const showOverlay = (msg) => { el.overlayMsg.textContent = msg; el.overlay.classList.remove("hidden"); };
  const failOverlay = (msg, err) => {
    showOverlay(err ? msg + "\n" + err : msg);
    setTimeout(() => el.overlay.classList.add("hidden"), 6000);
  };

  bind("btn-off", async () => {
    if (!confirm(T.confirm_off)) return;
    showOverlay(T.overlay_off);
    const res = await postPower("/api/poweroff");
    if (res.ok === false) failOverlay(T.off_failed, res.error);
  });
  bind("btn-reboot", async () => {
    if (!confirm(T.confirm_reboot)) return;
    rebooting = true; sawDrop = false;
    showOverlay(T.overlay_reboot);
    const res = await postPower("/api/reboot");
    if (res.ok === false) { rebooting = false; failOverlay(T.reboot_failed, res.error); }
  });
})();
