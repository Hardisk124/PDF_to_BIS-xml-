#!/usr/bin/env python3
"""Vyvojarske spustenie servera v prehliadaci.

Na kazdodenne pouzitie je urcena aplikacia app.py, ktora otvori vlastne okno.
"""

from __future__ import annotations

import uvicorn

from faktury.server import vytvor_aplikaciu

aplikacia = vytvor_aplikaciu()

if __name__ == "__main__":
    print("Otvor http://127.0.0.1:8000")
    uvicorn.run(aplikacia, host="127.0.0.1", port=8000, log_level="warning")
