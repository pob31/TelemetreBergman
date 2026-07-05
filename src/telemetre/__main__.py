"""Entry point: `python -m telemetre` (used by the systemd service).

Reads host/port from config and launches uvicorn as a single process.
"""
from __future__ import annotations

import uvicorn

from .config import load_config


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "telemetre.app:app",
        host=cfg.web.host,
        port=cfg.web.port,
        workers=1,
        log_level="info",
        # Don't block shutdown on the long-lived /stream SSE. Without this,
        # uvicorn waits indefinitely for that connection to close, so every web
        # reboot / power-off hung until systemd's 90s stop timeout SIGKILLed it.
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
