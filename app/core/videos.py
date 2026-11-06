"""
videos.py - user တစ်ဦးချင်း၏ ထုတ်ပြီးဗီဒီယိုများ (စာရင်း၊ ကြည့်ရန်၊ ဖျက်ရန်၊ ၄၈ နာရီကျော် အလိုအလျောက်ဖျက်ရန်)

ဖိုင်များ: outputs/<user_id>/video_<job>.mp4 ၊ ...mp3 ၊ ...srt ၊ poster_<job>.jpg ၊ post_<job>.txt
"""
import json
import shutil
import time
from pathlib import Path
from typing import List, Optional

from app.core import config, db

_KEEP = ("mode", "duration", "video_url", "audio_url", "srt_url", "transcript_srt_url", "poster_url", "post",
         "post_url", "warnings")


def user_dir(user_id: int) -> Path:
    return config.OUTPUT_DIR / str(int(user_id))


def _job_files(user_id: int, job_id: str) -> List[Path]:
    d = user_dir(user_id)
    return [p for p in d.iterdir() if p.is_file() and job_id in p.name] if d.is_dir() else []


def add(user_id: int, job_id: str, title: str, result: dict) -> dict:
    files = _job_files(user_id, job_id)
    now = time.time()
    data = {k: result.get(k) for k in _KEEP if k in result}
    row = {"id": job_id, "user_id": user_id, "title": (title or "").strip()[:120] or f"Video {job_id[:6]}",
           "created": now, "expires": now + config.RETENTION_HOURS * 3600.0,
           "size": sum(p.stat().st_size for p in files), "data": json.dumps(data, ensure_ascii=False)}
    db.run("INSERT OR REPLACE INTO videos(id,user_id,title,created,expires,size,data) VALUES(?,?,?,?,?,?,?)",
           (row["id"], user_id, row["title"], row["created"], row["expires"], row["size"], row["data"]))
    return public(row)


def public(row: dict, now: Optional[float] = None) -> dict:
    now = now or time.time()
    return {"id": row["id"], "title": row["title"], "created": row["created"], "expires": row["expires"],
            "hours_left": max(0.0, round((row["expires"] - now) / 3600.0, 1)), "size": row["size"],
            **(json.loads(row["data"]) if isinstance(row["data"], str) else row["data"])}


def list_for(user_id: int) -> List[dict]:
    now = time.time()
    rows = db.all_("SELECT * FROM videos WHERE user_id=? AND expires>? ORDER BY created DESC", (user_id, now))
    return [public(r, now) for r in rows]


def get(user_id: int, video_id: str) -> Optional[dict]:
    row = db.one("SELECT * FROM videos WHERE id=? AND user_id=?", (video_id, user_id))
    return public(row) if row else None


def _remove_files(user_id: int, job_id: str) -> int:
    n = 0
    for p in _job_files(user_id, job_id):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def delete(user_id: int, video_id: str) -> bool:
    row = db.one("SELECT id FROM videos WHERE id=? AND user_id=?", (video_id, user_id))
    if not row:
        return False
    _remove_files(user_id, video_id)
    db.run("DELETE FROM videos WHERE id=? AND user_id=?", (video_id, user_id))
    return True


def delete_all(user_id: int) -> int:
    n = db.run("DELETE FROM videos WHERE user_id=?", (user_id,)).rowcount
    shutil.rmtree(user_dir(user_id), ignore_errors=True)
    return n


def purge_expired(now: Optional[float] = None) -> int:
    """သက်တမ်း (RETENTION_HOURS) ကျော်သော ဗီဒီယိုများကို ဖိုင်နှင့်တကွ ဖျက်သည်။"""
    now = now or time.time()
    rows = db.all_("SELECT id,user_id FROM videos WHERE expires<=?", (now,))
    for r in rows:
        _remove_files(r["user_id"], r["id"])
        db.run("DELETE FROM videos WHERE id=?", (r["id"],))
    return len(rows)


def usage(user_id: int) -> dict:
    row = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM videos WHERE user_id=? AND expires>?",
                 (user_id, time.time()))
    return {"count": row["n"], "bytes": row["bytes"]}
