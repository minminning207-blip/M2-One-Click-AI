"""
auth_routes.py - /api/auth/* (register၊ login၊ logout၊ email အတည်ပြု၊ password reset၊ profile)
logic အားလုံးသည် app/core/auth.py ထဲတွင်ရှိ၍ ဒီနေရာတွင် HTTP ဘက်ပိုင်းသာ ပါသည်။
"""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from authlib.integrations.starlette_client import OAuth, OAuthError

from app.api.deps import COOKIE, client_ip, csrf_guard, current_user
from app.core import auth, captcha, config, mailer

router = APIRouter(prefix="/api/auth", dependencies=[Depends(csrf_guard)])

oauth = OAuth()
if config.google_enabled():
    oauth.register(
        name="google",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=config.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE, path="/")


class RegisterBody(BaseModel):
    email: str
    password: str
    name: str = ""
    captcha: str = ""


class LoginBody(BaseModel):
    email: str
    password: str
    captcha: str = ""


class EmailBody(BaseModel):
    email: str
    captcha: str = ""


class ResetBody(BaseModel):
    token: str
    password: str


class ProfileBody(BaseModel):
    name: str = ""


class PasswordBody(BaseModel):
    current: str
    new: str


class DeleteBody(BaseModel):
    password: str


def _need_captcha(request: Request, token: str) -> None:
    if not captcha.verify(token, client_ip(request)):
        raise auth.AuthError("captcha_required", "“ကျွန်ုပ်သည် စက်ရုပ်မဟုတ်ပါ” (CAPTCHA) ကို အတည်ပြုပါ။", 400)


@router.get("/config")
def public_config():
    return {"recaptcha_site_key": config.RECAPTCHA_SITE_KEY if captcha.enabled() else "",
            "signup_enabled": config.SIGNUP_ENABLED,
            "verification_required": config.REQUIRE_EMAIL_VERIFICATION,
            "mail_configured": mailer.configured(),
            "google_enabled": config.google_enabled(),
            "retention_hours": config.RETENTION_HOURS}


@router.post("/register")
def register(body: RegisterBody, request: Request):
    _need_captcha(request, body.captcha)
    return auth.register(body.email, body.password, body.name, client_ip(request))


@router.get("/verify")
def verify(token: str = ""):
    ok = auth.verify_email(token)
    return RedirectResponse("/auth.html?verified=" + ("1" if ok else "0"), status_code=303)


@router.post("/resend")
def resend(body: EmailBody, request: Request):
    _need_captcha(request, body.captcha)
    return auth.resend_verification(body.email, client_ip(request))


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = client_ip(request)
    if captcha.enabled() and auth.login_captcha_needed(body.email, ip):
        _need_captcha(request, body.captcha)
    token, user = auth.login(body.email, body.password, ip, request.headers.get("user-agent", ""))
    _set_session_cookie(response, token)
    return {"ok": True, "user": user}


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/forgot")
def forgot(body: EmailBody, request: Request):
    _need_captcha(request, body.captcha)
    return auth.forgot(body.email, client_ip(request))


@router.post("/reset")
def reset(body: ResetBody):
    return auth.reset_password(body.token, body.password)


@router.get("/me")
def me(user: dict = Depends(current_user)):
    from app.core import videos
    return {"user": user, "usage": videos.usage(user["id"]), "retention_hours": config.RETENTION_HOURS}


@router.post("/profile")
def profile(body: ProfileBody, user: dict = Depends(current_user)):
    return {"user": auth.update_profile(user["id"], body.name)}


@router.post("/password")
def password(body: PasswordBody, request: Request, user: dict = Depends(current_user)):
    return auth.change_password(user["id"], body.current, body.new, request.cookies.get(COOKIE))


@router.post("/delete")
def delete_account(body: DeleteBody, response: Response, user: dict = Depends(current_user)):
    out = auth.delete_account(user["id"], body.password)
    response.delete_cookie(COOKIE, path="/")
    return out


# ----------------------------------------------------------------------------- Google OAuth
@router.get("/google")
async def google_start(request: Request):
    if not config.google_enabled():
        return RedirectResponse("/auth.html?google_error=google_off", status_code=303)
    return await oauth.google.authorize_redirect(request, config.GOOGLE_REDIRECT_URI)


@router.get("/google/callback")
async def google_callback(request: Request):
    if not config.google_enabled():
        return RedirectResponse("/auth.html?google_error=google_off", status_code=303)
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:            # အသုံးပြုသူက ပယ်ချခြင်း၊ state မကိုက်ခြင်း စသည်
        return RedirectResponse("/auth.html?google_error=google_denied", status_code=303)
    info = token.get("userinfo") or {}
    try:
        session_token, _user = await run_in_threadpool(
            auth.login_with_google, info, client_ip(request), request.headers.get("user-agent", ""))
    except auth.AuthError as e:
        return RedirectResponse(f"/auth.html?google_error={e.code}", status_code=303)
    resp = RedirectResponse("/", status_code=303)       # login ပြီးရင် သွားမည့်စာမျက်နှာ (လိုအပ်လျှင် ပြင်ပါ)
    _set_session_cookie(resp, session_token)
    return resp
