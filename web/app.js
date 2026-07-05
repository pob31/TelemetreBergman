"use strict";
(() => {
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
      el.connText.textContent = d.port ? "lost " + d.port : "no sensor";
      setState("offline");
      el.banner.textContent = "SENSOR OFFLINE"; el.banner.classList.remove("hidden");
    } else if (d.stale) {
      el.connDot.className = "dot stale";
      el.connText.textContent = "weak / no target";
      setState("stale");
      el.banner.textContent = "NO SIGNAL"; el.banner.classList.remove("hidden");
    } else {
      el.connDot.className = "dot ok";
      el.connText.textContent = d.port || "connected";
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
      el.connText.textContent = "reconnecting…";
      setState("offline");
      if (rebooting) sawDrop = true;
    };
  }
  connect();

  // ---- controls ----
  const post = (path) => fetch(path, { method: "POST" }).catch(() => {});
  const flash = (btn) => { btn.classList.add("flash"); setTimeout(() => btn.classList.remove("flash"), 180); };
  const bind = (id, fn) => $(id).addEventListener("click", (e) => fn(e.currentTarget));

  bind("btn-zero", (b) => { flash(b); post("/api/tare"); });
  bind("btn-clear", (b) => { flash(b); post("/api/clear_zero"); });
  bind("btn-invert", (b) => { flash(b); post("/api/invert"); });

  const showOverlay = (msg) => { el.overlayMsg.textContent = msg; el.overlay.classList.remove("hidden"); };

  bind("btn-off", () => {
    if (confirm("Power OFF the Raspberry Pi?\n\nThe readout will go offline until someone powers it back on.")) {
      post("/api/poweroff");
      showOverlay("Powering off…\nWait for the Pi's LED to stop blinking, then it's safe to unplug.");
    }
  });
  bind("btn-reboot", () => {
    if (confirm("Reboot the Raspberry Pi?\n\nThe readout drops for ~30 s, then reconnects on its own.")) {
      rebooting = true; sawDrop = false;
      post("/api/reboot");
      showOverlay("Rebooting…\nThis page will reconnect automatically.");
    }
  });
})();
