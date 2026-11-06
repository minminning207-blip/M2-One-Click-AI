"""
main.py - FastAPI အဓိက ဖိုင်

Run (--reload မသုံးပါနှင့်၊ output ဖိုင်ရေးတိုင်း server ပြန်စတင်သွားနိုင်သည်):
    uvicorn main:app --host 127.0.0.1 --port 8000

API key များကို .env ဖိုင်ထဲတွင်သာ ထည့်ပါ။ browser သို့ ဘယ်တော့မှ မပို့ပါ။
"""
import importlib.util
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field, ValidationError

from app.core import auth, cleanup, config, db, videos
from app.api import auth_routes, video_routes
from app.api.deps import COOKIE, current_user
from app.ai import llm_fallback
from app.ai import music_service
from app.ai import gemini_service
from app.media import intro_cover
from app.core import jobs
from app import pipeline
from app.media import shapes
from app.ai import text_cleaner
from app.ai import transcribe
from app.ai import tts_engines

BASE_DIR = config.BASE_DIR
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = config.OUTPUT_DIR      # outputs/<user_id>/ ... (login ဝင်ထားသူ ကိုယ်ပိုင်ဖိုင်သာ ကြည့်/ဒေါင်းလို့ရ)
JOBS_DIR = config.JOBS_DIR
FONTS_DIR = BASE_DIR / "fonts"   # စာတန်းထိုး font (.ttf/.otf) များကို ဒီ folder ထဲ ထည့်နိုင်သည်
for d in (STATIC_DIR, OUTPUT_DIR, JOBS_DIR, FONTS_DIR):
    d.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(_app):
    db.init()          # database (users / sessions / videos) ဆောက်
    cleanup.start()    # ၄၈ နာရီပြည့်ဖိုင်များ အလိုအလျောက်ဖျက်ရန် နောက်ကွယ် thread
    yield


app = FastAPI(title="AI Movie Recap Studio", lifespan=lifespan)
# Google Login (OAuth state) အတွက် - Authlib သည် state ကို ဒီ signed cookie ထဲ ယာယီသိမ်းသည်
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, same_site="lax",
                   https_only=config.COOKIE_SECURE, max_age=600)
# မှတ်ချက်: .env ဖိုင်ရှိသော BASE_DIR ကို တိုက်ရိုက် mount မလုပ်ပါ - static နှင့် fonts သာ အများသိမြင်နိုင်သည်
# (outputs ကို mount မလုပ်တော့ပါ၊ app/api/video_routes.py က login စစ်ပြီးမှ ပေးသည်)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")
app.include_router(auth_routes.router)
app.include_router(video_routes.router)


@app.exception_handler(auth.AuthError)
async def auth_error_handler(_request: Request, exc: auth.AuthError):
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _font_family(p: Path) -> str:
    """font ဖိုင်ထဲက အစစ်အမှန် family အမည် (ASS/FFmpeg က ဒီအမည်နဲ့ရှာသည်)။"""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(str(p), 20).getname()[0]
    except Exception:  # noqa: BLE001
        return p.stem


@app.get("/api/fonts")
def list_fonts():
    return [{"file": p.name, "family": _font_family(p)}
            for p in sorted(FONTS_DIR.iterdir()) if p.suffix.lower() in (".ttf", ".otf")]


@app.get("/")
def index(request: Request):
    if not auth.user_from_session(request.cookies.get(COOKIE)):     # Login မဝင်ရသေးလျှင် Login စာမျက်နှာသို့
        return RedirectResponse("/auth.html", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/auth.html")
def auth_page():
    return FileResponse(STATIC_DIR / "auth.html")


@app.get("/api/health")
def health():
    """UI ကို အခြေအနေပြရန်။ API key ကို ဘယ်တော့မှ မပြန်ပေးပါ - ရှိ/မရှိ (true/false) သာ။"""
    ocr_ok, ocr_why = text_cleaner.availability()
    return {
        "gemini_configured": config.gemini_configured() or llm_fallback.available(),
        "llm_fallback": llm_fallback.providers(),
        "cloud_tts_configured": config.cloud_tts_configured(),
        "whisper_available": transcribe.available()[0],
        "whisper_reason": transcribe.available()[1],
        "transcribe_backend": config.TRANSCRIBE_BACKEND,
        "ocr_available": ocr_ok,
        "ocr_reason": ocr_why,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "gemini_model": config.GEMINI_MODEL,
        "gemini_tts_model": config.GEMINI_TTS_MODEL,
    }


# --------------------------------------------------------------------------- #
# ဆက်တင် ပုံစံများ (UI မှ JSON အဖြစ်ပို့မည်)
# --------------------------------------------------------------------------- #
HEX = r"^#[0-9a-fA-F]{6}$"


class SubtitleStyle(BaseModel):
    enabled: bool = True
    font_family: str = Field("Liberation Sans", max_length=60)
    font_size: int = Field(56, ge=20, le=160)
    color: str = Field("#FFFFFF", pattern=HEX)
    outline_mode: str = "outline"          # outline | box | shadow | none
    outline_color: str = Field("#000000", pattern=HEX)
    box_opacity: float = Field(1.0, ge=0.0, le=1.0)
    position: str = "bottom"               # bottom | middle | top
    bold: bool = True
    pos_x: Optional[float] = Field(None, ge=0.0, le=1.0)   # စိတ်ကြိုက်နေရာ (အလယ်မှတ်၊ ၀-၁) - None = position အတိုင်း
    pos_y: Optional[float] = Field(None, ge=0.0, le=1.0)


class AutoEdit(BaseModel):
    enabled: bool = False
    remove_text: bool = False              # AI ဖြင့် ရေစာ + မူရင်းစာတန်းထိုး အလိုအလျောက် ဖယ်ရှား
    ocr_lang: str = "en"                   # မူရင်းဗီဒီယိုထဲက စာသား၏ ဘာသာစကား
    flip_speed: bool = False               # ဘယ်ညာလှန် + 1.03x
    zoom: bool = False                     # အနည်းငယ် Zoom
    rhythm: str = "off"                    # off | light | medium | strong  (ခဏရပ် + အနည်းငယ်မြန် လှုပ်ရှားမှု)


class BlurBox(BaseModel):
    enabled: bool = False
    x: float = Field(0.1, ge=0.0, le=1.0)   # ဘယ်ထိပ်ထောင့် (ဗီဒီယိုပုံ၏ ၀-၁ အချိုး)
    y: float = Field(0.78, ge=0.0, le=1.0)
    w: float = Field(0.8, ge=0.02, le=1.0)
    h: float = Field(0.1, ge=0.02, le=1.0)
    strength: int = Field(60, ge=1, le=100)


class Visualizer(BaseModel):
    enabled: bool = True
    position: str = "middle"               # top | middle | bottom
    style: str = "none"                    # none = shape မထည့် (မူလ)
    color: str = Field("#00D4FF", pattern=HEX)
    background: str = "none"               # none | dark | light | purple | blue | sunset


class Shade(BaseModel):
    enabled: bool = False
    mode: str = "auto"                     # auto | custom
    color: str = Field("#000000", pattern=HEX)
    opacity: int = Field(50, ge=0, le=100)

class TextLogo(BaseModel):
    enabled: bool = False
    text: str = Field("", max_length=60)
    font_family: str = Field("Liberation Sans", max_length=60)
    font_size: int = Field(48, ge=16, le=200)
    color: str = Field("#FFFFFF", pattern=HEX)
    opacity: float = Field(1.0, ge=0.1, le=1.0)
    position: str = "top-left"     # top-left|top-center|top-right|bottom-left|bottom-center|bottom-right
    x: Optional[float] = Field(None, ge=0.0, le=1.0)      # စိတ်ကြိုက်နေရာ (အလယ်မှတ်)
    y: Optional[float] = Field(None, ge=0.0, le=1.0)


class Cover(BaseModel):
    enabled: bool = False
    template: str = "yellowbox"
    title: str = Field("", max_length=120)
    tagline: str = Field("", max_length=80)
    auto_title: bool = True        # title ဗလာဆိုလျှင် Gemini က hook title ရေးပေး
    font_family: str = Field("Liberation Sans", max_length=60)
    font_size: int = Field(90, ge=30, le=200)
    seconds: float = Field(2.5, ge=1.0, le=6.0)
    poster: bool = False           # Poster / Cover ပုံ (jpg) ကို သီးခြား ထုတ်ပေးမလား


class Grade(BaseModel):
    enabled: bool = False
    tint_color: str = Field("#FF8800", pattern=HEX)
    tint_opacity: int = Field(0, ge=0, le=60)
    brightness: int = Field(0, ge=-50, le=50)
    contrast: int = Field(100, ge=50, le=150)
    saturation: int = Field(100, ge=0, le=200)
    vignette: int = Field(0, ge=0, le=100)

class Music(BaseModel):
    enabled: bool = False
    id: str = Field("", max_length=64)          # Openverse track id (server က ကိုယ်တိုင် ပြန်ရှာ download လုပ်သည်)
    volume: int = Field(20, ge=0, le=100)
    duck: bool = True                            # စကားပြောနေစဉ် ဂီတကို အလိုအလျောက် လျှော့

class Settings(BaseModel):
    script_on: bool = False                # Voiceover Script ON/OFF
    script_text: str = Field("", max_length=5000)
    recap_length: str = "original"         # original (ခလုတ် OFF) | short | medium | long | p75 | full
    engine: str = "edge"                   # edge | google | gemini | cloud
    voice: str = Field("", max_length=120)
    language: str = Field("", max_length=20)          # edge: my-MM ၊ google/gemini: my
    language_label: str = Field("", max_length=100)   # ဘာသာပြန်/ရေးသားရန် ဘာသာစကားအမည်
    voice_clone: bool = False
    consent_lang: str = "en-US"
    speed: float = Field(1.0, ge=0.5, le=4.0)
    aspect_ratio: str = "9:16"             # 9:16 | 16:9 | 1:1
    subtitle: SubtitleStyle = SubtitleStyle()
    auto_edit: AutoEdit = AutoEdit()
    add_logo: bool = False
    logo_position: str = "top-right"
    logo_x: Optional[float] = Field(None, ge=0.0, le=1.0)  # ပုံ logo စိတ်ကြိုက်နေရာ (အလယ်မှတ်)
    logo_y: Optional[float] = Field(None, ge=0.0, le=1.0)
    viz: Visualizer = Visualizer()
    shade: Shade = Shade()
    mode: str = "recap"                    # recap | dub | sub (Subtitle only)
    post_text: bool = True                 # Title / Caption / Hashtag ရေးပေးမလား
    blur: BlurBox = BlurBox()              # မူရင်းစာတန်း/ရေစာ မှုန်ဝါးအကွက်
    sub_translate: bool = True             # Subtitle only: ဘာသာပြန်မလား (False = မူရင်းစကားအတိုင်း)
    dub_keep_audio: bool = True            # Dubbing: မူရင်းအသံကို အသံတိုးတိုး ချန်မလား
    dub_bg_volume: int = Field(15, ge=0, le=100)
    text_logo: TextLogo = TextLogo()
    cover: Cover = Cover()
    grade: Grade = Grade()
    music: Music = Music()


# --------------------------------------------------------------------------- #
# ဖိုင်တင်ခြင်း
# --------------------------------------------------------------------------- #
def has_file(upload: Optional[UploadFile]) -> bool:
    return bool(upload and upload.filename)


def safe_suffix(filename: str, default: str) -> str:
    """ဖိုင်အမည်မှ လုံခြုံသော extension သာ ယူသည် (path ထိုးဖောက်မှု မဖြစ်စေရန်)။"""
    suffix = Path(filename or "").suffix.lower()          # ဥပမာ ".mp4"
    ok = 1 < len(suffix) <= 6 and suffix[1:].isalnum()
    return suffix if ok else default


def save_upload(upload: UploadFile, dest: Path) -> Path:
    """ဖိုင်ကို သိမ်းသည်။ MAX_UPLOAD_MB ထက်ကျော်လျှင် ရပ်ပြီး error ပြသည်။"""
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with open(dest, "wb") as f:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise HTTPException(413, f"ဖိုင်အရွယ်အစား {config.MAX_UPLOAD_MB} MB ထက် မကျော်ရပါ။")
            f.write(chunk)
    return dest


# --------------------------------------------------------------------------- #
# အသံစာရင်း / နမူနာ / ခွင့်ပြုချက်စာသား
# --------------------------------------------------------------------------- #
@app.get("/api/music/status")
def music_status(user: dict = Depends(current_user)):
    return {"enabled": config.MUSIC_ENABLED, "licenses": config.MUSIC_LICENSES}


@app.get("/api/music/search")
def music_search(q: str = Query("", max_length=80), page: int = Query(1, ge=1, le=12),
                 user: dict = Depends(current_user)):
    if not config.MUSIC_ENABLED:
        raise HTTPException(404, "Background music is disabled on this server.")
    try:
        return music_service.search(q, page)
    except music_service.MusicError as e:
        raise HTTPException(502, str(e))


@app.get("/api/tts/voices")
def tts_voices(engine: str = Query("edge"), user: dict = Depends(current_user)):
    if engine not in tts_engines.ENGINES:
        raise HTTPException(400, "Model မမှန်ပါ။")
    try:
        return tts_engines.list_voices(engine)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, config.scrub(f"အသံစာရင်း ရယူ၍မရပါ: {e}"))


@app.post("/api/tts/preview")
def tts_preview(req: dict, user: dict = Depends(current_user)):
    engine = str(req.get("engine", "edge"))
    voice = str(req.get("voice", ""))[:120]
    language = str(req.get("language", ""))[:20]
    if engine not in tts_engines.ENGINES:
        raise HTTPException(400, "Invalid engine.")
    if not (voice or language):
        raise HTTPException(400, "အသံ မရွေးရသေးပါ။")
    if language == "*":
        language = ""
    try:
        audio, mime = tts_engines.synth_preview(engine, voice, language)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Preview failed: {config.scrub(e)}")
    return Response(content=audio, media_type=mime)


@app.get("/api/consent")
def consent_statements():
    """Voice Clone အတွက် အသံပိုင်ရှင်က ကိုယ်တိုင်ဖတ်ပြီး အသံသွင်းရမည့် ခွင့်ပြုချက်စာသားများ။"""
    return [{"locale": k, "label": v[0], "statement": v[1]} for k, v in tts_engines.CONSENT.items()]


# --------------------------------------------------------------------------- #
# ဗီဒီယိုထုတ်လုပ်ရန် (တစ်ခုတည်းသော endpoint)
# --------------------------------------------------------------------------- #
def make_steps(s: Settings):
    """Job ၏ အဆင့်စာရင်း (pipeline.py ခေါ်သော step key များနှင့် ကိုက်ရမည်)။"""
    clean = s.auto_edit.enabled and s.auto_edit.remove_text
    if not s.script_on and s.mode == "sub":
        steps = [("whisper", "Whisper: exact transcription + SRT"),
                 ("gemini", "Gemini: translate subtitles")]
        if clean:
            steps.append(("clips", "AI: remove watermark & subtitles"))
        steps.append(("render", "Render video (FFmpeg)"))
        return steps
    if not s.script_on:
        return [("whisper", "Whisper: exact transcription + SRT"),
                ("gemini", "Gemini: recap script & highlight scenes"),
                ("voice", "Voiceover (TTS)"),
                ("clips", "Cut, clean & sync scenes"),
                ("render", "Render video (FFmpeg)")]
    steps = [("voice", "Voiceover (TTS)")]
    if clean:
        steps.append(("clean", "AI: remove watermark & subtitles"))
    steps.append(("render", "Render video (FFmpeg)"))
    return steps


@app.post("/api/create")
def create(
    settings: str = Form(...),
    video: Optional[UploadFile] = File(None),
    reference_audio: Optional[UploadFile] = File(None),
    consent_audio: Optional[UploadFile] = File(None),
    logo: Optional[UploadFile] = File(None),
    user: dict = Depends(current_user),
):
    if jobs.active_count(user["id"]) >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"တန်းစီ/ပြုလုပ်နေသော အလုပ် {config.MAX_ACTIVE_JOBS_PER_USER} ခု ရှိနေပါပြီ။ ပြီးမှ ထပ်တင်ပါ (သို့) Cancel လုပ်ပါ။")
    # ---- ဆက်တင်များ စစ်ဆေးခြင်း ----
    try:
        s = Settings.model_validate_json(settings)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise HTTPException(400, f"ဆက်တင် မမှန်ပါ: {msg}")

    if s.music.enabled:
        if not config.MUSIC_ENABLED:
            raise HTTPException(400, "Background music is disabled on this server.")
        if not s.music.id:
            raise HTTPException(400, "Choose a background music track first.")

    if s.aspect_ratio not in ("9:16", "16:9", "1:1"):
        raise HTTPException(400, "Video size သည် 9:16 / 16:9 / 1:1 ဖြစ်ရမည်။")
    if s.engine not in tts_engines.ENGINES:
        raise HTTPException(400, "Model မမှန်ပါ။")
    if s.subtitle.position not in ("bottom", "middle", "top"):
        raise HTTPException(400, "Subtitle position မမှန်ပါ။")
    if s.subtitle.outline_mode not in ("outline", "box", "shadow", "none"):
        raise HTTPException(400, "Subtitle outline မမှန်ပါ။")
    if s.viz.position not in ("top", "middle", "bottom"):
        raise HTTPException(400, "Visualizer position မမှန်ပါ။")
    if s.viz.style != "none" and s.viz.style not in shapes.STYLES:   # "none" = shape မထည့်
        raise HTTPException(400, "Shape style မမှန်ပါ။")
    if s.viz.background not in shapes.BACKGROUNDS:
        raise HTTPException(400, "Background မမှန်ပါ။")
    if s.shade.mode not in ("auto", "custom"):
        raise HTTPException(400, "Shade mode မမှန်ပါ။")
    if s.auto_edit.rhythm not in ("off", "light", "medium", "strong"):
        raise HTTPException(400, "Rhythm မမှန်ပါ။")
    if s.auto_edit.ocr_lang not in text_cleaner.OCR_LANGS:
        raise HTTPException(400, "မူရင်းစာသားဘာသာစကား မမှန်ပါ။")
    if s.recap_length not in gemini_service.LENGTH_RATIO:
        raise HTTPException(400, "Recap length မမှန်ပါ။")
    if s.logo_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
        raise HTTPException(400, "Logo position မမှန်ပါ။")
    if s.mode not in ("recap", "dub", "sub"):
        raise HTTPException(400, "Mode မမှန်ပါ။")
    if s.text_logo.position not in ("top-left", "top-center", "top-right",
                                    "bottom-left", "bottom-center", "bottom-right"):
        raise HTTPException(400, "Text logo position မမှန်ပါ။")
    if s.cover.template not in intro_cover.TEMPLATES:
        raise HTTPException(400, "Cover template မမှန်ပါ။")

    has_video = has_file(video)
    if s.script_on:
        # Voiceover Script ON: စာသားထည့်ရမည် (ဗီဒီယိုမတင်လည်းရ)
        if not s.script_text.strip():
            raise HTTPException(400, "Voiceover Script ON ဖြစ်နေသည် - စာသား ထည့်ပေးပါ။")
    else:
        # Voiceover Script OFF: ဗီဒီယိုလိုပြီး Whisper လိုသည်
        if not has_video:
            raise HTTPException(400, "Voiceover Script OFF (Auto Recap) အတွက် ဗီဒီယိုဖိုင် ရွေးပေးပါ။")
        ok_stt, why_stt = transcribe.available()
        if not ok_stt:
            raise HTTPException(400, why_stt)

    sub_only = s.mode == "sub" and not s.script_on     # Subtitle only: အသံထုတ်စရာမလို
    if sub_only:
        s.voice_clone = False
    if s.voice_clone:
        if not (has_file(reference_audio) and has_file(consent_audio)):
            raise HTTPException(400, "Voice Clone ON ဖြစ်နေသည် - အသံနမူနာနှင့် ခွင့်ပြုချက်အသံသွင်းမှု နှစ်ဖိုင်လုံး တင်ပေးပါ။")
        if s.consent_lang not in tts_engines.CONSENT:
            raise HTTPException(400, "ခွင့်ပြုချက်ဘာသာစကား မမှန်ပါ။")
        if not config.gemini_configured():
            raise HTTPException(400, "Voice Clone အတွက် GEMINI_API_KEY လိုအပ်သည်။")
    elif not sub_only and s.engine in ("edge", "gemini", "cloud") and not s.voice:
        raise HTTPException(400, "အသံ (Voice) ရွေးပေးပါ။")
    if not sub_only and s.engine == "google" and not s.language and not s.voice_clone:
        raise HTTPException(400, "Language ရွေးပေးပါ။")
    if s.add_logo and not has_file(logo):
        raise HTTPException(400, "Custom Logo ON ဖြစ်နေသည် - logo ပုံ တင်ပေးပါ။")

    job = jobs.create_job("create", make_steps(s), owner=user["id"])
    job_dir = JOBS_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        video_path = ref_path = consent_path = logo_path = None
        if has_video:
            video_path = save_upload(video, job_dir / ("input" + safe_suffix(video.filename, ".mp4")))
        if s.voice_clone:
            ref_path = save_upload(reference_audio, job_dir / ("ref" + safe_suffix(reference_audio.filename, ".wav")))
            consent_path = save_upload(consent_audio, job_dir / ("consent" + safe_suffix(consent_audio.filename, ".wav")))
        if s.add_logo and has_file(logo):
            logo_path = save_upload(logo, job_dir / ("logo" + safe_suffix(logo.filename, ".png")))
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        job.status = "error"          # မစတင်ရသော job သည် active အဖြစ် မကျန်ရစ်စေရန်
        raise
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(job_dir, ignore_errors=True)
        job.status = "error"
        raise HTTPException(500, f"ဖိုင်သိမ်းမရပါ: {e}")

    data = s.model_dump()

    uid = user["id"]
    user_out = videos.user_dir(uid)
    user_out.mkdir(parents=True, exist_ok=True)
    title = Path(video.filename).stem if has_video and video.filename else (s.script_text.strip()[:40] or "Voiceover video")

    def work(progress):
        return pipeline.run_pipeline(data, video_path, ref_path, consent_path, logo_path, job_dir, user_out,
                                     BASE_DIR, job.id, progress, url_base=f"/outputs/{uid}")

    jobs.submit(job, work, cleanup=lambda: shutil.rmtree(job_dir, ignore_errors=True),
                on_done=lambda j: videos.add(uid, j.id, title, j.result or {}))
    return {"job_id": job.id}
