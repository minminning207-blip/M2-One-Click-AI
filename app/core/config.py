"""
config.py - ဆက်တင်များနှင့် API key များကို လုံခြုံစွာ ဖတ်ယူရန်

လုံခြုံရေးစည်းမျဉ်း:
  * API key များကို server ဘက်မှာသာ သိမ်းသည် (.env ဖိုင် သို့မဟုတ် environment variable)
  * browser (HTML/JavaScript) ဆီသို့ ဘယ်တော့မှ မပို့ပါ
  * log နှင့် error စာသားထဲ key ပါလာပါက scrub() ဖြင့် ဖျက်သည်
  * .env ဖိုင်ကို GitHub သို့ မတင်ပါနှင့် (.gitignore ထဲ ထည့်ပြီးသား)
"""
import os
import re
import secrets
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]      # project root (.env, static/, outputs/, fonts/ ရှိရာ)


def _num(name: str, default, cast=float):
    """ဂဏန်း environment variable ကို ဖတ်သည်။ မှားရိုက်ထားလျှင် (ဥပမာ comment ပါနေ) မူလတန်ဖိုးကို သုံးသည်။"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return cast(re.sub(r"\s+#.*$", "", raw).strip())
    except ValueError:
        return default


def _load_env_file(path: Path) -> None:
    """.env ဖိုင်ကို ရိုးရိုးဖတ်သည် (python-dotenv မလိုပါ)။"""
    if not path.is_file():
        return
    # utf-8-sig: Notepad က BOM ထည့်ပြီး သိမ်းထားလျှင်လည်း ပထမစာကြောင်းကို မှန်မှန်ဖတ်နိုင်စေရန်
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]      # "..." ထဲက စာသားသာယူ (နောက်က comment ကို ပယ်)
        else:
            value = re.sub(r"\s+#.*$", "", value).strip()  # KEY=value   # မှတ်ချက် ပုံစံကို ဖယ်
        # စက်ထဲမှာ environment variable ရှိပြီးသားဆိုရင် အဲဒါကို ဦးစားပေးသည်
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")

# ---------------------------------------------------------------- Gemini ----
# model အမည်များ မကြာခဏ ပြောင်းတတ်သဖြင့် "latest" alias ကို စာသားရေးသားရန် မူလအဖြစ်သုံးသည်
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
# Google AI Studio (Gemini API) ၏ TTS model
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
# Gemini request များကို ထိန်းပို့ခြင်း (429/503 မဖြစ်စေရန်) - GEMINI_RPM = တစ်မိနစ်လျှင် အများဆုံး request (0 = မထိန်း)
GEMINI_RPM = _num("GEMINI_RPM", 20.0)
GEMINI_MIN_INTERVAL = (60.0 / GEMINI_RPM) if GEMINI_RPM > 0 else 0.0
# Gemini မရ (busy/quota) ဖြစ်ပြီးနောက် အရန် provider ဆီ ဒီစက္ကန့်ကြာ တိုက်ရိုက်ပို့ပြီး Gemini ကို ခေတ္တ မစမ်းတော့ပါ
GEMINI_COOLDOWN = _num("GEMINI_COOLDOWN_SECONDS", 120.0)

# ------------------------------------------------------------- Whisper ------
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")      # auto | cpu | cuda
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "")        # မထည့်ရင် cpu=int8 / cuda=float16
WHISPER_BEAM = _num("WHISPER_BEAM", 1, int)         # beam search အကျယ် (1 = အမြန်ဆုံး၊ 5 = ပိုတိကျသော်လည်း ၂-၃ ဆ နှေး)
WHISPER_THREADS = _num("WHISPER_THREADS", 0, int)   # CPU thread အရေအတွက် (0 = စက်ရဲ့ core အားလုံး)

# ------------------------------------------------ Speech-to-Text (API) -----
# local = faster-whisper (ကိုယ့်စက်မှာ run၊ GPU/CPU လို)  |  groq / openai = API သုံး (GPU မလို)
TRANSCRIBE_BACKEND = os.getenv("TRANSCRIBE_BACKEND", "local").strip().lower()
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "").strip()            # မထည့်ရင် backend အလိုက် မူလ model
TRANSCRIBE_BASE_URL = os.getenv("TRANSCRIBE_BASE_URL", "").strip()      # OpenAI-compatible အခြား provider သုံးလိုမှ
TRANSCRIBE_CHUNK_SECONDS = max(60, _num("TRANSCRIBE_CHUNK_SECONDS", 600, int))   # အသံကို ဒီစက္ကန့်စီ ဖြတ်ပို့ (ဖိုင်အရွယ်ကန့်သတ်ချက်ကြောင့်)
TRANSCRIBE_CONCURRENCY = max(1, _num("TRANSCRIBE_CONCURRENCY", 2, int))          # တပြိုင်နက်ပို့မည့် အပိုင်းအရေအတွက်
# backend -> (base url, မူလ model, key ၏ environment variable)
_TRANSCRIBE_DEFAULTS = {
    "groq": ("https://api.groq.com/openai/v1", "whisper-large-v3-turbo", "GROQ_API_KEY"),
    "openai": ("https://api.openai.com/v1", "whisper-1", "OPENAI_API_KEY"),
}

# ---------------------------------------------------------------- OCR -------
OCR_INTERVAL = min(3.0, max(0.25, _num("OCR_INTERVAL", 1.0)))   # 0.25-3 စက္ကန့်အတွင်းသာ (များလွန်းလျှင် စာတန်းထိုးကို လွတ်သွားမည်)     # စက္ကန့်ဘယ်လောက်တစ်ကြိမ် frame ကို စစ်မလဲ
OCR_GPU = os.getenv("OCR_GPU", "auto")                     # auto | true | false

# ---------------------------------------------------------------- TTS -------
# Rate limit / 403 မဖြစ်စေရန် တပြိုင်နက်ပို့မည့်အရေအတွက်နှင့် request အကြား အနည်းဆုံးစက္ကန့်
TTS_CONCURRENCY = _num("TTS_CONCURRENCY", 3, int)
TTS_MIN_INTERVAL = _num("TTS_MIN_INTERVAL", 0.6)

# တင်လို့ရသော ဖိုင်အများဆုံးအရွယ်အစား (MB)
MAX_UPLOAD_MB = _num("MAX_UPLOAD_MB", 2048, int)

# ------------------------------------------------------- အကောင့် / လုံခြုံရေး -----
def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() not in ("0", "false", "no", "off", "")


DATA_DIR = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data"))                 # SQLite database ထားရာ
OUTPUT_DIR = BASE_DIR / "outputs"                                              # ထုတ်ပြီးဖိုင်များ (user တစ်ဦးချင်း folder)
JOBS_DIR = BASE_DIR / "jobs"                                                   # ယာယီ အလုပ်ဖိုင်များ
CACHE_DIR = BASE_DIR / "cache"                                                 # Whisper ရလဒ် cache
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:8000").strip().rstrip("/")   # email ထဲက link အတွက်
SESSION_DAYS = _num("SESSION_DAYS", 14, int)
COOKIE_SECURE = _flag("COOKIE_SECURE", False)               # HTTPS နောက်က run လျှင် 1 ထားပါ
SIGNUP_ENABLED = _flag("SIGNUP_ENABLED", True)
TRUST_PROXY = _flag("TRUST_PROXY", False)                    # Nginx စသည့် proxy နောက်က run လျှင် 1 (X-Forwarded-For ကို ယုံ)
REQUIRE_EMAIL_VERIFICATION = _flag("REQUIRE_EMAIL_VERIFICATION", True)
RETENTION_HOURS = _num("RETENTION_HOURS", 48, int)           # ထုတ်ပြီးဗီဒီယိုကို ဒီနာရီပြည့်လျှင် အလိုအလျောက်ဖျက်
MAX_ACTIVE_JOBS_PER_USER = _num("MAX_ACTIVE_JOBS_PER_USER", 2, int)   # user တစ်ဦး တစ်ပြိုင်နက် တန်းစီ/run ခွင့်ပြုအရေအတွက်

# Email (SMTP) - မထည့်ထားလျှင် email ကို မပို့ဘဲ terminal ထဲ (link ပါ) ပြသည် (ကိုယ့်စက်မှာ စမ်းရန်)
SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = _num("SMTP_PORT", 587, int)
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or ""
SMTP_FROM = (os.getenv("SMTP_FROM") or SMTP_USER or "no-reply@localhost").strip()
SMTP_SECURITY = (os.getenv("SMTP_SECURITY") or "starttls").strip().lower()    # starttls | ssl | none

# Google reCAPTCHA v2 (checkbox) - site key / secret မထည့်လျှင် CAPTCHA ပိတ်ထားသည်
RECAPTCHA_SITE_KEY = (os.getenv("RECAPTCHA_SITE_KEY") or "").strip()
RECAPTCHA_SECRET = (os.getenv("RECAPTCHA_SECRET") or "").strip()
RECAPTCHA_VERIFY_URL = (os.getenv("RECAPTCHA_VERIFY_URL") or "https://www.google.com/recaptcha/api/siteverify").strip()

# Google OAuth ("Continue with Google") - Google Cloud Console > Credentials > OAuth client ID (Web application)
GOOGLE_CLIENT_ID = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
GOOGLE_REDIRECT_URI = (os.getenv("GOOGLE_REDIRECT_URI") or f"{APP_BASE_URL}/api/auth/google/callback").strip()
# SessionMiddleware (OAuth state) အတွက် secret။ worker အများကြီး / restart လုပ်လျှင် .env ၌ ပုံသေတန်ဖိုး ထည့်ပါ
SESSION_SECRET = (os.getenv("SESSION_SECRET") or "").strip() or secrets.token_urlsafe(48)

# Background music (Openverse API) - key မလိုပါ။ OPENVERSE_CLIENT_ID / SECRET ထည့်လျှင် rate limit မြင့်သည်
MUSIC_ENABLED = _flag("MUSIC_ENABLED", True)
MUSIC_LICENSES = [x.strip().lower() for x in (os.getenv("MUSIC_LICENSES") or "cc0,pdm,by").split(",") if x.strip()]
MUSIC_MAX_MB = _num("MUSIC_MAX_MB", 40, int)
MUSIC_CACHE_DIR = CACHE_DIR / "music"
OPENVERSE_CLIENT_ID = (os.getenv("OPENVERSE_CLIENT_ID") or "").strip()
OPENVERSE_CLIENT_SECRET = (os.getenv("OPENVERSE_CLIENT_SECRET") or "").strip()

# .env.example ထဲက နမူနာတန်ဖိုးကို key အစစ်အဖြစ် မမှတ်ရန်
_PLACEHOLDERS = {"", "your-key-here", "your_api_key_here", "changeme"}


def _clean_key(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return None if value.lower() in _PLACEHOLDERS else value


def get_gemini_key() -> Optional[str]:
    """Gemini API key (Google AI Studio) ကို ပြန်ပေးသည်။ မရှိလျှင် None။"""
    return _clean_key(os.getenv("GEMINI_API_KEY")) or _clean_key(os.getenv("GOOGLE_API_KEY"))


def get_cloud_tts_key() -> Optional[str]:
    """Google Cloud Text-to-Speech key။ မသတ်မှတ်ထားလျှင် Gemini key ကိုပဲ ပြန်သုံးကြည့်သည်။"""
    return _clean_key(os.getenv("GOOGLE_TTS_API_KEY")) or get_gemini_key()


def gemini_configured() -> bool:
    return get_gemini_key() is not None


def cloud_tts_configured() -> bool:
    return get_cloud_tts_key() is not None


def transcribe_api() -> bool:
    return TRANSCRIBE_BACKEND in _TRANSCRIBE_DEFAULTS


def get_transcribe_key() -> Optional[str]:
    """Speech-to-Text API key (TRANSCRIBE_API_KEY သို့မဟုတ် GROQ_API_KEY / OPENAI_API_KEY)။"""
    if not transcribe_api():
        return None
    return _clean_key(os.getenv("TRANSCRIBE_API_KEY")) or _clean_key(os.getenv(_TRANSCRIBE_DEFAULTS[TRANSCRIBE_BACKEND][2]))


def transcribe_endpoint():
    """(base_url, model) - .env ၌ ထည့်ထားလျှင် ၎င်းကို၊ မထည့်လျှင် backend ၏ မူလတန်ဖိုး။"""
    base, model, _ = _TRANSCRIBE_DEFAULTS.get(TRANSCRIBE_BACKEND, ("", "", ""))
    return (TRANSCRIBE_BASE_URL or base), (TRANSCRIBE_MODEL or model)


# ကျန် secret များ (log / error ထဲ ပါလာလျှင် scrub() ဖြင့် ဖျက်ရန်)
_EXTRA_SECRET_ENVS = ("ANTHROPIC_API_KEY", "LLM_OPENAI_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
                      "RECAPTCHA_SECRET", "SMTP_PASSWORD", "GOOGLE_CLIENT_SECRET", "OPENVERSE_CLIENT_SECRET")


def _secrets() -> List[str]:
    extra = [(os.getenv(n) or "").strip() for n in _EXTRA_SECRET_ENVS]
    return [k for k in {get_gemini_key(), get_cloud_tts_key(), get_transcribe_key(), *extra} if k and len(k) >= 8]


def scrub(text) -> str:
    """error စာသားထဲ API key ပါနေလျှင် *** ဖြင့် အစားထိုးသည်။"""
    text = str(text)
    for key in _secrets():
        text = text.replace(key, "***")
    # Google API key ပုံစံ (AIza...) ကိုလည်း ဖျက်သည်
    return re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "***", text)


def google_enabled() -> bool:
    """Google Login ကို သုံးနိုင်မလား (Client ID နှင့် Secret နှစ်ခုလုံး အစစ်ထည့်ထားမှ)။"""
    return _clean_key(GOOGLE_CLIENT_ID) is not None and _clean_key(GOOGLE_CLIENT_SECRET) is not None
