"""Entry point: `python -m cadreur` (operator-started, no launchd unit in v1).

Reads host/port from cadreur.toml and launches uvicorn as a single process.
"""
from __future__ import annotations

import uvicorn

from .config import load_config


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "cadreur.app:app",
        host=cfg.web.host,
        port=cfg.web.port,
        workers=1,
        log_level="info",
        # Don't block shutdown on the long-lived /stream SSE (same lesson as
        # the Pi app: uvicorn otherwise waits for that connection forever).
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
