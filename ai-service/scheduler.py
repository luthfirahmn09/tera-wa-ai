"""Scheduler internal: jalankan run_sync() berkala di background thread.

Ini pengganti cron OS — cukup hidup bareng app, tanpa container/crontab
tambahan. Interval diatur via SYNC_INTERVAL_MINUTES (0 = matikan).
"""
from __future__ import annotations

import threading
import time

from config import SYNC_INTERVAL_MINUTES
from sync import run_sync


def _loop(interval_sec: int) -> None:
    while True:
        time.sleep(interval_sec)
        try:
            run_sync()
        except Exception as exc:  # jangan biarkan thread mati
            print(f"[scheduler] sync gagal: {exc}")


def start() -> None:
    """Mulai scheduler (no-op kalau interval <= 0)."""
    if SYNC_INTERVAL_MINUTES <= 0:
        print("[scheduler] dimatikan (SYNC_INTERVAL_MINUTES=0)")
        return
    interval = SYNC_INTERVAL_MINUTES * 60
    t = threading.Thread(target=_loop, args=(interval,), daemon=True)
    t.start()
    print(f"[scheduler] aktif, sync tiap {SYNC_INTERVAL_MINUTES} menit")
