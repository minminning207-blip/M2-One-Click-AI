"""
deps.py - route များ မျှသုံးသော dependency (login စစ်ခြင်း၊ CSRF ကာကွယ်ခြင်း၊ client IP)
"""
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from app.core import auth, config

COOKIE = "session"


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def current_user(request: Request) -> dict:
    user = auth.user_from_session(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Login ဝင်ရန် လိုအပ်ပါသည်။")
    return user


def csrf_guard(request: Request) -> None:
    """
    cookie သုံး session ကို တခြားဝက်ဘ်ဆိုက်မှ အတင်းခိုင်းမစေရန် - POST/PUT/DELETE တွင် Origin (သို့ Referer) သည်
    ကိုယ့်ဆာဗာနှင့် တူရမည်။ (cookie ကိုလည်း SameSite=Lax ထားသည်)
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("sec-fetch-site", "") == "cross-site":
        raise HTTPException(403, "တောင်းဆိုမှု ရင်းမြစ် မမှန်ပါ။")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return
    host = urlparse(origin).netloc.lower()
    allowed = {request.headers.get("host", "").lower(), urlparse(config.APP_BASE_URL).netloc.lower()}
    if config.TRUST_PROXY and request.headers.get("x-forwarded-host"):
        allowed.add(request.headers["x-forwarded-host"].split(",")[0].strip().lower())
    if host not in allowed:
        raise HTTPException(403, "တောင်းဆိုမှု ရင်းမြစ် မမှန်ပါ။")
