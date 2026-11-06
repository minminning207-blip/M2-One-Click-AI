"""
auth.py - အကောင့်စနစ် (framework မလိုသော logic) - register / email အတည်ပြု / login / password reset / profile

လုံခြုံရေး:
  * password ကို scrypt ဖြင့် hash ၊ session / email-link token များကို hash သာ DB ထဲသိမ်း
  * Email ရှိ/မရှိ မသိစေရန် forgot / resend / register တွင် အဖြေတူ (generic) ပြန်
  * Login / register / reset တောင်းဆိုမှုများကို email နှင့် IP အလိုက် ကန့်သတ် (rate limit)
  * Password ပြောင်း / reset လုပ်လျှင် ကျန် session များကို ပိတ်
"""
import re
import time
from typing import Optional, Tuple

from app.core import config, db, mailer, security, videos

VERIFY_HOURS = 24
RESET_HOURS = 1
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")
_DUMMY_HASH = security.hash_password("dummy-password-for-timing")


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ----------------------------------------------------------------------------- rate limit
def _count(key: str, window: float) -> int:
    row = db.one("SELECT COUNT(*) AS n FROM attempts WHERE key=? AND ts>?", (key, time.time() - window))
    return row["n"]


def _hit(key: str) -> None:
    db.run("INSERT INTO attempts(key, ts) VALUES(?, ?)", (key, time.time()))


def _limit(key: str, maximum: int, window: float, message: str = "ကြိုးစားမှု များလွန်းနေပါသည်။ ခဏစောင့်ပြီး ပြန်စမ်းပါ။") -> None:
    if _count(key, window) >= maximum:
        raise AuthError("rate_limited", message, 429)


# ----------------------------------------------------------------------------- helpers
def norm_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    e = norm_email(email)
    if len(e) > 254 or not _EMAIL_RE.match(e):
        raise AuthError("bad_email", "Email လိပ်စာ မမှန်ပါ။")
    return e


def validate_password(password: str, email: str = "") -> None:
    if not password or len(password) < 8:
        raise AuthError("weak_password", "Password သည် အနည်းဆုံး ၈ လုံး ရှိရမည်။")
    if len(password) > 128:
        raise AuthError("weak_password", "Password သည် ၁၂၈ လုံးထက် မကျော်ရပါ။")
    if email and password.lower() == email.lower():
        raise AuthError("weak_password", "Password နှင့် Email မတူရပါ။")


def public(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "name": user["name"], "verified": bool(user["verified"]),
            "created": user["created"], "provider": user.get("auth_provider") or "local"}


def _link(path: str, token: str) -> str:
    return f"{config.APP_BASE_URL}{path}{token}"


def _make_token(user_id: int, kind: str, hours: float) -> str:
    token = security.new_token()
    db.run("DELETE FROM tokens WHERE user_id=? AND kind=?", (user_id, kind))      # အဟောင်းကို ပယ်
    db.run("INSERT INTO tokens(id,user_id,kind,expires,used) VALUES(?,?,?,?,0)",
           (security.token_hash(token), user_id, kind, time.time() + hours * 3600.0))
    return token


def _take_token(token: str, kind: str) -> Optional[int]:
    """token မှန်ပြီး မသုံးရသေး၊ သက်တမ်းမကုန်လျှင် သုံးပြီးအဖြစ်မှတ်၍ user_id ပြန်ပေးသည်။"""
    row = db.one("SELECT * FROM tokens WHERE id=? AND kind=?", (security.token_hash(token), kind))
    if not row or row["used"] or row["expires"] < time.time():
        return None
    db.run("UPDATE tokens SET used=1 WHERE id=?", (row["id"],))
    return row["user_id"]


def _send_verification(user: dict) -> None:
    token = _make_token(user["id"], "verify", VERIFY_HOURS)
    link = _link("/api/auth/verify?token=", token)
    mailer.send(user["email"], "Movie Recap Studio - Email အတည်ပြုပါ",
                f"မင်္ဂလာပါ {user['name'] or ''},\n\nအကောင့်ကို အတည်ပြုရန် အောက်ပါ link ကို နှိပ်ပါ ({VERIFY_HOURS} နာရီအတွင်း)။\n\n{link}\n\n"
                "ကိုယ်တိုင် မလုပ်ခဲ့ပါက ဒီ email ကို လျစ်လျူရှုပါ။",
                f'<p>မင်္ဂလာပါ {user["name"] or ""},</p><p>အကောင့်ကို အတည်ပြုရန် <a href="{link}">ဒီနေရာကို နှိပ်ပါ</a> ({VERIFY_HOURS} နာရီအတွင်း)။</p>'
                "<p>ကိုယ်တိုင် မလုပ်ခဲ့ပါက ဒီ email ကို လျစ်လျူရှုပါ။</p>")


# ----------------------------------------------------------------------------- register / verify
def register(email: str, password: str, name: str = "", ip: str = "") -> dict:
    if not config.SIGNUP_ENABLED:
        raise AuthError("signup_closed", "အကောင့်အသစ် ဖွင့်ခြင်းကို ယာယီ ပိတ်ထားပါသည်။", 403)
    _limit(f"reg:ip:{ip}", 10, 3600.0)
    e = validate_email(email)
    validate_password(password, e)
    _hit(f"reg:ip:{ip}")
    name = (name or "").strip()[:60]
    existing = db.one("SELECT * FROM users WHERE email=?", (e,))
    if existing:     # Email ရှိမရှိ မဖော်ပြဘဲ ပိုင်ရှင်ထံသာ အကြောင်းကြား
        mailer.send(e, "Movie Recap Studio - အကောင့်ရှိပြီးသား",
                    "ဒီ email ဖြင့် အကောင့်ရှိပြီးသားဖြစ်ပါသည်။ ဝင်ရောက်ပါ၊ သို့မဟုတ် Password မေ့လျှင် 'Password မေ့နေသလား' ကို သုံးပါ။\n"
                    f"{config.APP_BASE_URL}/auth.html\n\nကိုယ်တိုင် မလုပ်ခဲ့ပါက လျစ်လျူရှုပါ။")
        return {"ok": True, "verify_required": config.REQUIRE_EMAIL_VERIFICATION}
    verified = 0 if config.REQUIRE_EMAIL_VERIFICATION else 1
    cur = db.run("INSERT INTO users(email,name,pw_hash,verified,created) VALUES(?,?,?,?,?)",
                 (e, name, security.hash_password(password), verified, time.time()))
    if config.REQUIRE_EMAIL_VERIFICATION:
        _send_verification(db.one("SELECT * FROM users WHERE id=?", (cur.lastrowid,)))
    return {"ok": True, "verify_required": config.REQUIRE_EMAIL_VERIFICATION}


def verify_email(token: str) -> bool:
    uid = _take_token(token or "", "verify")
    if uid is None:
        return False
    db.run("UPDATE users SET verified=1 WHERE id=?", (uid,))
    return True


def resend_verification(email: str, ip: str = "") -> dict:
    e = norm_email(email)
    _limit(f"resend:ip:{ip}", 10, 3600.0)
    _limit(f"resend:email:{e}", 3, 3600.0)
    _hit(f"resend:ip:{ip}")
    _hit(f"resend:email:{e}")
    user = db.one("SELECT * FROM users WHERE email=?", (e,))
    if user and not user["verified"]:
        _send_verification(user)
    return {"ok": True}


# ----------------------------------------------------------------------------- login / session
def login(email: str, password: str, ip: str = "", ua: str = "") -> Tuple[str, dict]:
    e = norm_email(email)
    _limit(f"login:email:{e}", 8, 900.0, "Login မှားများလွန်းနေပါသည်။ ၁၅ မိနစ်ခန့် စောင့်ပြီး ပြန်စမ်းပါ။")
    _limit(f"login:ip:{ip}", 40, 900.0, "Login မှားများလွန်းနေပါသည်။ ၁၅ မိနစ်ခန့် စောင့်ပြီး ပြန်စမ်းပါ။")
    user = db.one("SELECT * FROM users WHERE email=?", (e,))
    ok = security.verify_password(password or "", user["pw_hash"] if user else _DUMMY_HASH) and user is not None
    if not ok:
        _hit(f"login:email:{e}")
        _hit(f"login:ip:{ip}")
        raise AuthError("bad_credentials", "Email သို့မဟုတ် Password မှားနေပါသည်။", 401)
    if config.REQUIRE_EMAIL_VERIFICATION and not user["verified"]:
        raise AuthError("not_verified", "Email ကို မအတည်ပြုရသေးပါ။ သင့် inbox ထဲက link ကို နှိပ်ပါ (သို့) အတည်ပြု email ပြန်ပို့ခိုင်းပါ။", 403)
    token = security.new_token()
    now = time.time()
    db.run("INSERT INTO sessions(id,user_id,created,expires,ua) VALUES(?,?,?,?,?)",
           (security.token_hash(token), user["id"], now, now + config.SESSION_DAYS * 86400.0, (ua or "")[:200]))
    db.run("UPDATE users SET last_login=? WHERE id=?", (now, user["id"]))
    db.run("DELETE FROM attempts WHERE key=?", (f"login:email:{e}",))
    return token, public(user)


def _random_pw_hash() -> str:
    """Google ဖြင့်ဖွင့်သော အကောင့်အတွက် ဘယ်သူမှ မသိနိုင်သော password hash (pw_hash NOT NULL ဖြစ်သဖြင့်)။"""
    return security.hash_password(security.new_token())


def _new_session(user_id: int, ua: str = "") -> str:
    token = security.new_token()
    now = time.time()
    db.run("INSERT INTO sessions(id,user_id,created,expires,ua) VALUES(?,?,?,?,?)",
           (security.token_hash(token), user_id, now, now + config.SESSION_DAYS * 86400.0, (ua or "")[:200]))
    db.run("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
    return token


def login_with_google(info: dict, ip: str = "", ua: str = "") -> Tuple[str, dict]:
    """Google (OpenID Connect) userinfo ဖြင့် login - အကောင့်မရှိလျှင် အလိုအလျောက် ဖန်တီး၊ email တူသောအကောင့်ရှိလျှင် ချိတ်ဆက်သည်။"""
    _limit(f"google:ip:{ip}", 30, 900.0)
    _hit(f"google:ip:{ip}")
    sub = str(info.get("sub") or "")
    email = norm_email(info.get("email") or "")
    if not sub or not email:
        raise AuthError("google_failed", "Google မှ အချက်အလက် မရရှိပါ။ ပြန်စမ်းပါ။", 400)
    if not info.get("email_verified"):
        raise AuthError("google_unverified", "ဒီ Google အကောင့်၏ Email ကို Google ဘက်တွင် မအတည်ပြုရသေးပါ။", 403)
    name = (info.get("name") or "").strip()[:60]

    user = db.one("SELECT * FROM users WHERE google_id=?", (sub,))
    if not user:
        user = db.one("SELECT * FROM users WHERE email=?", (email,))
        if user:
            if user.get("google_id") and user["google_id"] != sub:
                raise AuthError("google_conflict", "ဒီ Email သည် အခြား Google အကောင့်နှင့် ချိတ်ထားပြီးသားဖြစ်ပါသည်။", 409)
            if not user["verified"]:
                # Pre-hijack ကာကွယ်ရန် - တစ်ယောက်ယောက်က ဒီ email ဖြင့် password ကိုယ်တိုင်ပေးပြီး မအတည်ပြုရသေးဘဲ
                # register လုပ်ထားခဲ့လျှင် ၎င်း password၊ session နှင့် token များကို ဖျက်သည်
                db.run("UPDATE users SET pw_hash=?, auth_provider='google' WHERE id=?", (_random_pw_hash(), user["id"]))
                db.run("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                db.run("DELETE FROM tokens WHERE user_id=?", (user["id"],))
            db.run("UPDATE users SET google_id=?, verified=1, name=CASE WHEN name='' THEN ? ELSE name END WHERE id=?",
                   (sub, name, user["id"]))
        else:
            if not config.SIGNUP_ENABLED:
                raise AuthError("signup_closed", "အကောင့်အသစ် ဖွင့်ခြင်းကို ယာယီ ပိတ်ထားပါသည်။", 403)
            cur = db.run("INSERT INTO users(email,name,pw_hash,verified,created,google_id,auth_provider) "
                         "VALUES(?,?,?,1,?,?,'google')", (email, name, _random_pw_hash(), time.time(), sub))
            user = {"id": cur.lastrowid}
        user = db.one("SELECT * FROM users WHERE id=?", (user["id"],))
    return _new_session(user["id"], ua), public(user)


def login_captcha_needed(email: str, ip: str = "") -> bool:
    """Login မှားမှု ၃ ကြိမ်ထက်ပိုလျှင် (email အလိုက်) သို့မဟုတ် IP တစ်ခုမှ ၁၀ ကြိမ်ထက်ပိုလျှင် CAPTCHA တောင်းသည်။"""
    return _count(f"login:email:{norm_email(email)}", 900.0) >= 3 or _count(f"login:ip:{ip}", 900.0) >= 10


def user_from_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    row = db.one("SELECT u.*, s.expires AS s_exp FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?",
                 (security.token_hash(token),))
    if not row or row["s_exp"] < time.time():
        return None
    if config.REQUIRE_EMAIL_VERIFICATION and not row["verified"]:
        return None
    return public(row)


def logout(token: Optional[str]) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE id=?", (security.token_hash(token),))


# ----------------------------------------------------------------------------- password reset
def forgot(email: str, ip: str = "") -> dict:
    e = norm_email(email)
    _limit(f"forgot:ip:{ip}", 10, 3600.0)
    _limit(f"forgot:email:{e}", 3, 3600.0)
    _hit(f"forgot:ip:{ip}")
    _hit(f"forgot:email:{e}")
    user = db.one("SELECT * FROM users WHERE email=?", (e,))
    if user:
        token = _make_token(user["id"], "reset", RESET_HOURS)
        link = f"{config.APP_BASE_URL}/auth.html?mode=reset&token={token}"
        mailer.send(user["email"], "Movie Recap Studio - Password ပြန်သတ်မှတ်ရန်",
                    f"Password ပြန်သတ်မှတ်ရန် အောက်ပါ link ကို နှိပ်ပါ ({RESET_HOURS} နာရီအတွင်း၊ တစ်ကြိမ်သာသုံးနိုင်)။\n\n{link}\n\n"
                    "ကိုယ်တိုင် မတောင်းဆိုခဲ့ပါက ဒီ email ကို လျစ်လျူရှုပါ (Password မပြောင်းပါ)။",
                    f'<p>Password ပြန်သတ်မှတ်ရန် <a href="{link}">ဒီနေရာကို နှိပ်ပါ</a> ({RESET_HOURS} နာရီအတွင်း၊ တစ်ကြိမ်သာသုံးနိုင်)။</p>'
                    "<p>ကိုယ်တိုင် မတောင်းဆိုခဲ့ပါက လျစ်လျူရှုပါ။</p>")
    return {"ok": True}


def reset_password(token: str, new_password: str) -> dict:
    validate_password(new_password)
    uid = _take_token(token or "", "reset")
    if uid is None:
        raise AuthError("bad_token", "Link သက်တမ်းကုန်သွားပြီ (သို့) သုံးပြီးသားဖြစ်ပါသည်။ Password မေ့ကြောင်း ထပ်တောင်းပါ။", 400)
    db.run("UPDATE users SET pw_hash=?, verified=1, auth_provider='local' WHERE id=?", (security.hash_password(new_password), uid))   # email ထဲက link ကိုနှိပ်နိုင်ခြင်းဖြင့် email အတည်ပြုပြီးသားဖြစ်
    db.run("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


# ----------------------------------------------------------------------------- profile
def update_profile(user_id: int, name: str) -> dict:
    db.run("UPDATE users SET name=? WHERE id=?", ((name or "").strip()[:60], user_id))
    return public(db.one("SELECT * FROM users WHERE id=?", (user_id,)))


def change_password(user_id: int, current: str, new: str, keep_token: Optional[str] = None) -> dict:
    user = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    _limit(f"chpw:{user_id}", 8, 900.0)
    google_only = bool(user) and user.get("auth_provider") == "google"      # password မရှိသေးသူ
    if not user or (not google_only and not security.verify_password(current or "", user["pw_hash"])):
        _hit(f"chpw:{user_id}")
        raise AuthError("bad_credentials", "လက်ရှိ Password မှားနေပါသည်။", 401)
    validate_password(new, user["email"])
    db.run("UPDATE users SET pw_hash=?, auth_provider='local' WHERE id=?", (security.hash_password(new), user_id))
    keep = security.token_hash(keep_token) if keep_token else ""
    db.run("DELETE FROM sessions WHERE user_id=? AND id<>?", (user_id, keep))      # တခြားစက်များမှ logout
    return {"ok": True}


def delete_account(user_id: int, password: str) -> dict:
    user = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    _limit(f"delacc:{user_id}", 5, 900.0)
    google_only = bool(user) and user.get("auth_provider") == "google"      # password မရှိသူ - login session ရှိခြင်းဖြင့် လုံလောက်
    if not user or (not google_only and not security.verify_password(password or "", user["pw_hash"])):
        _hit(f"delacc:{user_id}")
        raise AuthError("bad_credentials", "Password မှားနေပါသည်။", 401)
    videos.delete_all(user_id)
    db.run("DELETE FROM users WHERE id=?", (user_id,))       # sessions / tokens / videos စာရင်းများ cascade ဖြင့် ပျက်
    return {"ok": True}
