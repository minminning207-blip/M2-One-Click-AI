"""
cleanup.py - ၄၈ နာရီ (RETENTION_HOURS) ပြည့်သော ဖိုင်များကို အလိုအလျောက် ဖျက်ခြင်း

  * သက်တမ်းကုန် ဗီဒီယိုများ (ဖိုင် + စာရင်း)
  * စာရင်းမရှိဘဲ ကျန်နေသော output ဖိုင်များ (mtime အရ) နှင့် ကျန်ရစ်သော jobs/ ယာယီ folder များ
  * သက်တမ်းကုန် session / token / login ကြိုးစားမှတ်တမ်း ၊ ၁၄ ရက်ကျော် Whisper cache
"""
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from app.core import config, db, videos

_started = False


def _old_files(root: Path, cutoff: float) -> int:
    n = 0
    if not root.is_dir():
        return 0
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


def run_once(now: Optional[float] = None) -> dict:
    now = now or time.time()
    cutoff = now - config.RETENTION_HOURS * 3600.0
    out = {"videos": videos.purge_expired(now)}
    out["orphan_files"] = _old_files(config.OUTPUT_DIR, cutoff)
    # ဖိုင်မရှိတော့သော user folder အလွတ်များ
    if config.OUTPUT_DIR.is_dir():
        for d in config.OUTPUT_DIR.iterdir():
            try:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass
    jobs = 0
    if config.JOBS_DIR.is_dir():
        for d in config.JOBS_DIR.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    jobs += 1
            except OSError:
                pass
    out["job_dirs"] = jobs
    out["cache_files"] = _old_files(config.CACHE_DIR, now - 14 * 86400.0)
    db.run("DELETE FROM sessions WHERE expires<=?", (now,))
    db.run("DELETE FROM tokens WHERE expires<=? OR used=1", (now,))
    db.run("DELETE FROM attempts WHERE ts<?", (now - 86400.0,))
    return out


def _loop(interval: float) -> None:
    while True:
        try:
            res = run_once()
            if any(res.values()):
                print(f"[Cleanup] {res}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[Cleanup] error: {config.scrub(e)}", flush=True)
        time.sleep(interval)


def start(interval: float = 1800.0) -> None:
    """ဖွင့်ချိန်တစ်ကြိမ်ချက်ချင်း၊ ပြီးလျှင် ၃၀ မိနစ်တစ်ကြိမ် နောက်ကွယ်မှ ရှင်းလင်းသည်။"""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(interval,), daemon=True, name="cleanup").start()
