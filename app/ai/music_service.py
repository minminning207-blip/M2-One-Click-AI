"""
music_service.py - Royalty-free background music from the Openverse API (https://api.openverse.org/v1)

  * No API key is needed (anonymous: ~20 requests/minute, 200/day per server IP). Searches are cached in memory.
    Put OPENVERSE_CLIENT_ID / OPENVERSE_CLIENT_SECRET in .env to get a token and higher limits.
  * Only licenses listed in MUSIC_LICENSES are offered (default: cc0, pdm, by). "by" tracks need a credit line,
    which fetch_track() returns so it can be shown to the user.
  * The browser only ever sends a track *id*. The server looks the track up on Openverse itself and downloads it,
    so users cannot make the server fetch arbitrary URLs.
"""
import ipaddress
import re
import socket
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from app.core import config

API = "https://api.openverse.org/v1"
UA = "AIMovieRecapStudio/1.0 (background-music)"
_ID_RE = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_AUDIO_EXT = (".mp3", ".ogg", ".oga", ".wav", ".m4a", ".flac", ".opus")
_CACHE_TTL = 3600.0
_MIN_GAP = 3.2          # seconds between uncached anonymous calls (keeps us under 20/minute)

_lock = threading.Lock()
_cache: Dict[str, Tuple[float, object]] = {}
_last_call = [0.0]
_token = {"value": "", "exp": 0.0}


class MusicError(Exception):
    pass


# ----------------------------------------------------------------------------- HTTP helpers
def _auth_header() -> Dict[str, str]:
    if not (config.OPENVERSE_CLIENT_ID and config.OPENVERSE_CLIENT_SECRET):
        return {}
    with _lock:
        if _token["value"] and _token["exp"] > time.time() + 60:
            return {"Authorization": f"Bearer {_token['value']}"}
    try:
        r = httpx.post(f"{API}/auth_tokens/token/", timeout=20, headers={"User-Agent": UA},
                       data={"grant_type": "client_credentials", "client_id": config.OPENVERSE_CLIENT_ID,
                             "client_secret": config.OPENVERSE_CLIENT_SECRET})
        r.raise_for_status()
        j = r.json()
        with _lock:
            _token["value"] = j["access_token"]
            _token["exp"] = time.time() + float(j.get("expires_in", 3600))
        return {"Authorization": f"Bearer {_token['value']}"}
    except Exception:
        return {}            # fall back to anonymous access


def _get_json(path: str, params: dict) -> dict:
    key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]  # type: ignore[return-value]
    headers = {"User-Agent": UA, "Accept": "application/json"}
    headers.update(_auth_header())
    if "Authorization" not in headers:           # anonymous: pace the calls
        with _lock:
            wait = _last_call[0] + _MIN_GAP - time.time()
            _last_call[0] = max(time.time(), _last_call[0]) + (_MIN_GAP if wait > 0 else 0)
        if wait > 0:
            time.sleep(min(wait, 6.0))
    try:
        r = httpx.get(API + path, params=params, headers=headers, timeout=25, follow_redirects=True)
    except httpx.HTTPError as e:
        raise MusicError(f"Could not reach Openverse ({e.__class__.__name__}).")
    if r.status_code == 429:
        raise MusicError("Openverse rate limit reached. Please wait a minute and try again.")
    if r.status_code == 404:
        raise MusicError("Track not found on Openverse.")
    if r.status_code != 200:
        raise MusicError(f"Openverse returned an error ({r.status_code}).")
    data = r.json()
    with _lock:
        _cache[key] = (time.time() + _CACHE_TTL, data)
        if len(_cache) > 300:
            for k in sorted(_cache, key=lambda x: _cache[x][0])[:100]:
                _cache.pop(k, None)
    return data


# ----------------------------------------------------------------------------- track helpers
def _allowed(item: dict) -> bool:
    return str(item.get("license") or "").lower() in config.MUSIC_LICENSES


def _credit(item: dict) -> str:
    title = item.get("title") or "Untitled"
    creator = item.get("creator") or "Unknown artist"
    lic = str(item.get("license") or "").lower()
    ver = item.get("license_version") or ""
    page = item.get("foreign_landing_url") or ""
    if lic in ("cc0", "pdm"):
        label = "CC0 / Public Domain"
        return f'"{title}" by {creator} ({label}) {page}'.strip()
    label = f"CC {lic.upper()} {ver}".strip()
    lurl = item.get("license_url") or ""
    return f'"{title}" by {creator} - {label} {lurl} - {page}'.replace("  ", " ").strip()


def _slim(item: dict) -> dict:
    dur_ms = item.get("duration") or 0
    return {
        "id": item.get("id"),
        "title": item.get("title") or "Untitled",
        "creator": item.get("creator") or "Unknown artist",
        "duration": int(round(dur_ms / 1000.0)) if dur_ms else 0,
        "license": str(item.get("license") or "").lower(),
        "license_version": item.get("license_version") or "",
        "source": item.get("source") or item.get("provider") or "",
        "url": item.get("url") or "",                       # used only for the in-browser preview player
        "landing": item.get("foreign_landing_url") or "",
        "tags": [t.get("name") for t in (item.get("tags") or [])[:4] if isinstance(t, dict) and t.get("name")],
        "credit": _credit(item),
    }


# ----------------------------------------------------------------------------- public API
def search(q: str, page: int = 1, page_size: int = 12) -> dict:
    q = (q or "").strip()[:80] or "ambient"
    params = {"q": q, "category": "music", "license": ",".join(config.MUSIC_LICENSES), "mature": "false",
              "page": max(1, min(int(page), 12)), "page_size": max(3, min(int(page_size), 20))}
    data = _get_json("/audio/", params)
    results: List[dict] = []
    for it in data.get("results") or []:
        if _allowed(it) and str(it.get("url") or "").startswith("https://") and it.get("id"):
            results.append(_slim(it))
    return {"results": results, "page": params["page"], "page_count": int(data.get("page_count") or 1),
            "result_count": int(data.get("result_count") or 0)}


def get(track_id: str) -> dict:
    if not _ID_RE.match(track_id or ""):
        raise MusicError("Invalid track id.")
    return _get_json(f"/audio/{track_id}/", {})


def _public_https(url: str) -> bool:
    """https only, and the host must not resolve to a private / local address."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        return False
    try:
        for fam, _t, _p, _c, sa in socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(sa[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except Exception:
        return False
    return True


def _prune(folder: Path, days: float = 7.0) -> None:
    cutoff = time.time() - days * 86400
    for f in folder.glob("*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def fetch_track(track_id: str) -> Tuple[Path, str]:
    """Download (or reuse from cache) a track. Returns (file path, credit line)."""
    item = get(track_id)
    if not _allowed(item):
        raise MusicError("This track's license is not allowed.")
    url = item.get("url") or ""
    suffix = Path(urlparse(url).path).suffix.lower()
    suffix = suffix if suffix in _AUDIO_EXT else ".mp3"
    folder = Path(config.MUSIC_CACHE_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{track_id}{suffix}"
    credit = _credit(item)
    if dest.is_file() and dest.stat().st_size > 1024:
        return dest, credit
    _prune(folder)

    limit = int(config.MUSIC_MAX_MB) * 1024 * 1024
    tmp = dest.with_suffix(dest.suffix + ".part")
    cur = url
    try:
        with httpx.Client(timeout=60, headers={"User-Agent": UA}, follow_redirects=False) as c:
            for _ in range(4):                       # follow a few redirects manually, re-checking each host
                if not _public_https(cur):
                    raise MusicError("Track file location is not allowed.")
                with c.stream("GET", cur) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        cur = urljoin(cur, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise MusicError(f"Could not download the track ({r.status_code}).")
                    ctype = (r.headers.get("content-type") or "").lower()
                    if not (ctype.startswith("audio/") or "ogg" in ctype or "octet-stream" in ctype):
                        raise MusicError("The track file is not an audio file.")
                    size = 0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(65536):
                            size += len(chunk)
                            if size > limit:
                                raise MusicError(f"Track is larger than {config.MUSIC_MAX_MB} MB.")
                            f.write(chunk)
                    break
            else:
                raise MusicError("Too many redirects while downloading the track.")
        if tmp.stat().st_size < 1024:
            raise MusicError("Downloaded track is empty.")
        tmp.replace(dest)
    except httpx.HTTPError as e:
        raise MusicError(f"Could not download the track ({e.__class__.__name__}).")
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest, credit
