"""Telemetre Bergman — LiDAR cart-position server for the Bergman stage.

Keep this package __init__ import-light: the pure modules (frames, filters)
must import with zero third-party dependencies so they can be unit-tested on
the dev machine without installing FastAPI/uvicorn/pyserial.
"""

__version__ = "0.1.0"
