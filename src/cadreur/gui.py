"""Native-window wrapper: the same web UI in a macOS WKWebView (pywebview).

`python -m cadreur` stays the headless server (browser/tablet UI); `cadreur-gui`
runs that same server and opens it in a real desktop window. Closing the window
shuts the server down. If the server is already running (port in use), only the
window is opened. Requires the optional gui extra: pip install -e '.[gui]'
"""
from __future__ import annotations

import logging
import socket
import threading
import time

import uvicorn

from .config import load_config

log = logging.getLogger("cadreur.gui")


def _port_open(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex((probe_host, port)) == 0


def main() -> None:
    import webview  # here so `python -m cadreur` never needs the gui extra

    cfg = load_config()
    server = None
    if _port_open(cfg.web.host, cfg.web.port):
        log.info("Server already running on :%d — opening a window on it", cfg.web.port)
    else:
        server = uvicorn.Server(uvicorn.Config(
            "cadreur.app:app",
            host=cfg.web.host,
            port=cfg.web.port,
            workers=1,
            log_level="info",
            timeout_graceful_shutdown=3,
        ))
        # uvicorn skips signal-handler setup off the main thread, which is what
        # we want: pywebview owns the main thread and the Cocoa event loop.
        threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _port_open(cfg.web.host, cfg.web.port):
            time.sleep(0.1)

    url_host = "127.0.0.1" if cfg.web.host == "0.0.0.0" else cfg.web.host
    webview.create_window(
        "Cadreur Bergman",
        f"http://{url_host}:{cfg.web.port}",
        width=1180,
        height=1100,
        min_size=(900, 700),
        background_color="#0b0f14",
    )
    webview.start()  # blocks until the window is closed
    if server is not None:
        server.should_exit = True
        time.sleep(0.5)  # let the lifespan shutdown run (engine, threads, OSC)


if __name__ == "__main__":
    main()
