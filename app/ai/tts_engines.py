"""
tts_engines.py - အသံထုတ်လုပ်ရေး engine လေးမျိုး

  edge    : Microsoft Edge-TTS (အခမဲ့၊ အသံအမည်များ)
  google  : Google Translate TTS (gTTS၊ အခမဲ့၊ key မလို)
  gemini  : Google AI Studio - Gemini TTS (GEMINI_API_KEY ကိုသုံး) + Voice Replication (Voice Clone)
  cloud   : Google Cloud Text-to-Speech (Chirp 3 HD စသည်၊ GOOGLE_TTS_API_KEY)

Server ဘက်က ပိတ်ပင်တာ (403 / 429 rate limit) မဖြစ်စေရန်:
  * စာသားများကို chunk (အစုလိုက်) ပြုလုပ်ပြီး request အရေအတွက်ကို လျှော့သည်
  * asyncio Semaphore ဖြင့် တပြိုင်နက်ပို့မည့် အရေအတွက်ကို ကန့်သတ်သည်
  * Throttle ဖြင့် request အကြား အနည်းဆုံးအချိန် ချန်သည်
  * 429/5xx တွင် exponential backoff + jitter (Retry-After ကို လေးစား) ဖြင့် ပြန်ကြိုးစားသည်
"""
import asyncio
import base64
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core import config
from app.core.services import SAMPLE_RATE, edge_batch_one, run, write_wav

# --------------------------------------------------------------------------- #
# ဘာသာစကားအမည်များ
# --------------------------------------------------------------------------- #
LANG_NAMES = {
    "my": "Burmese", "en": "English", "th": "Thai", "zh": "Chinese", "cmn": "Chinese (Mandarin)", "yue": "Cantonese",
    "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "ur": "Urdu", "ne": "Nepali",
    "si": "Sinhala", "km": "Khmer", "lo": "Lao", "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay",
    "fil": "Filipino", "jv": "Javanese", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "ru": "Russian", "uk": "Ukrainian", "tr": "Turkish",
    "ar": "Arabic", "fa": "Persian", "he": "Hebrew", "el": "Greek", "cs": "Czech", "hu": "Hungarian",
    "ro": "Romanian", "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "no": "Norwegian", "fi": "Finnish",
    "sw": "Swahili", "am": "Amharic", "af": "Afrikaans", "bg": "Bulgarian", "hr": "Croatian", "sk": "Slovak",
    "sl": "Slovenian", "sr": "Serbian", "ca": "Catalan", "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian",
}

# Gemini TTS ထောက်ပံ့သော ဘာသာစကားများ (model က ဘာသာစကားကို စာသားမှ အလိုအလျောက် သိသည်)
GEMINI_LANGS = [
    ("my", "Burmese"), ("th", "Thai"), ("en", "English"), ("zh-CN", "Chinese (Simplified)"),
    ("zh-TW", "Chinese (Traditional)"), ("yue", "Cantonese"), ("ja", "Japanese"), ("ko", "Korean"),
    ("hi", "Hindi"), ("bn", "Bangla"), ("ta", "Tamil"), ("te", "Telugu"), ("mr", "Marathi"),
    ("gu", "Gujarati"), ("kn", "Kannada"), ("ml", "Malayalam"), ("ne", "Nepali"), ("si", "Sinhala"),
    ("km", "Khmer"), ("lo", "Lao"), ("vi", "Vietnamese"), ("id", "Indonesian"), ("ms", "Malay"),
    ("fil", "Filipino"), ("jv", "Javanese"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("pl", "Polish"), ("ru", "Russian"),
    ("tr", "Turkish"), ("ar", "Arabic"), ("fa", "Persian"), ("he", "Hebrew"), ("el", "Greek"),
    ("cs", "Czech"), ("hu", "Hungarian"), ("ro", "Romanian"), ("sv", "Swedish"), ("da", "Danish"),
    ("nb", "Norwegian"), ("fi", "Finnish"), ("sw", "Swahili"), ("am", "Amharic"), ("af", "Afrikaans"),
]

# Gemini TTS ၏ ပါပြီးသား အသံ ၃၀ (နာမည်, လိင်, ဖော်ပြချက်)
# လိင်သည် Google Cloud ၏ Chirp 3 HD အသံစာရင်းနှင့် ကိုက်ညီသည်
GEMINI_VOICES = [
    ("Zephyr", "Female", "Bright"), ("Puck", "Male", "Upbeat"), ("Charon", "Male", "Informative"),
    ("Kore", "Female", "Firm"), ("Fenrir", "Male", "Excitable"), ("Leda", "Female", "Youthful"),
    ("Orus", "Male", "Firm"), ("Aoede", "Female", "Breezy"), ("Callirrhoe", "Female", "Easy-going"),
    ("Autonoe", "Female", "Bright"), ("Enceladus", "Male", "Breathy"), ("Iapetus", "Male", "Clear"),
    ("Umbriel", "Male", "Easy-going"), ("Algieba", "Male", "Smooth"), ("Despina", "Female", "Smooth"),
    ("Erinome", "Female", "Clear"), ("Algenib", "Male", "Gravelly"), ("Rasalgethi", "Male", "Informative"),
    ("Laomedeia", "Female", "Upbeat"), ("Achernar", "Female", "Soft"), ("Alnilam", "Male", "Firm"),
    ("Schedar", "Male", "Even"), ("Gacrux", "Female", "Mature"), ("Pulcherrima", "Female", "Forward"),
    ("Achird", "Male", "Friendly"), ("Zubenelgenubi", "Male", "Casual"), ("Vindemiatrix", "Female", "Gentle"),
    ("Sadachbia", "Male", "Lively"), ("Sadaltager", "Male", "Knowledgeable"), ("Sulafat", "Female", "Warm"),
]

# Voice Replication အတွက် လိုအပ်သော ခွင့်ပြုချက်စာသား (အသံပိုင်ရှင်က ကိုယ်တိုင်ဖတ်ပြီး အသံသွင်းရမည်)
# Google ၏ မူရင်းစာသားအတိုင်း - ပြင်ဆင်ခြင်း မပြုရ
CONSENT = {
    "en-US": ("English (US)", "I am the owner of this voice and I consent to Google using this voice to create a synthetic voice model."),
    "en-GB": ("English (UK)", "I am the owner of this voice and I consent to Google using this voice to create a synthetic voice model."),
    "en-IN": ("English (India)", "I am the owner of this voice and I consent to Google using this voice to create a synthetic voice model."),
    "en-AU": ("English (Australia)", "I am the owner of this voice and I consent to Google using this voice to create a synthetic voice model."),
    "ar-XA": ("Arabic", "أنا مالك هذا الصوت وأوافق على أن تستخدم Google هذا الصوت لإنشاء نموذج صوتي اصطناعي."),
    "bn-IN": ("Bengali", "আমি এই ভয়েসের মালিক এবং আমি একটি সিন্থেটিক ভয়েস মডেল তৈরি করতে এই ভয়েস ব্যবহার করে Google-এর সাথে সম্মতি দিচ্ছি।"),
    "zh-CN": ("Chinese (Simplified)", "我是此声音的拥有者并授权谷歌使用此声音创建语音合成模型"),
    "nl-NL": ("Dutch", "Ik ben de eigenaar van deze stem en ik geef Google toestemming om deze stem te gebruiken om een synthetisch stemmodel te maken."),
    "fr-FR": ("French (France)", "Je suis le propriétaire de cette voix et j'autorise Google à utiliser cette voix pour créer un modèle de voix synthétique."),
    "fr-CA": ("French (Canada)", "Je suis le propriétaire de cette voix et j'autorise Google à utiliser cette voix pour créer un modèle de voix synthétique."),
    "de-DE": ("German", "Ich bin der Eigentümer dieser Stimme und bin damit einverstanden, dass Google diese Stimme zur Erstellung eines synthetischen Stimmmodells verwendet."),
    "gu-IN": ("Gujarati", "હું આ વોઈસનો માલિક છું અને સિન્થેટિક વોઈસ મોડલ બનાવવા માટે આ વોઈસનો ઉપયોગ કરીને google ને હું સંમતિ આપું છું"),
    "hi-IN": ("Hindi", "मैं इस आवाज का मालिक हूं और मैं सिंथेटिक आवाज मॉडल बनाने के लिए Google को इस आवाज का उपयोग करने की सहमति देता हूं"),
    "id-ID": ("Indonesian", "Saya pemilik suara ini dan saya menyetujui Google menggunakan suara ini untuk membuat model suara sintetis."),
    "it-IT": ("Italian", "Sono il proprietario di questa voce e acconsento che Google la utilizzi per creare un modello di voce sintetica."),
    "ja-JP": ("Japanese", "私はこの音声の所有者であり、Googleがこの音声を使用して音声合成モデルを作成することを承認します。"),
    "kn-IN": ("Kannada", "ನಾನು ಈ ಧ್ವನಿಯ ಮಾಲಿಕ ಮತ್ತು ಸಂಶ್ಲೇಷಿತ ಧ್ವನಿ ಮಾದರಿಯನ್ನು ರಚಿಸಲು ಈ ಧ್ವನಿಯನ್ನು ಬಳಸಿಕೊಂಡುಗೂಗಲ್ ಗೆ ನಾನು ಸಮ್ಮತಿಸುತ್ತೇನೆ."),
    "ko-KR": ("Korean", "나는 이 음성의 소유자이며 구글이 이 음성을 사용하여 음성 합성 모델을 생성할 것을 허용합니다."),
    "ml-IN": ("Malayalam", "ഈ ശബ്ദത്തിന്റെ ഉടമ ഞാനാണ്, ഒരു സിന്തറ്റിക് വോയ്സ് മോഡൽ സൃഷ്ടിക്കാൻ ഈ ശബ്ദം ഉപയോഗിക്കുന്നതിന് ഞാൻ Google-ന് സമ്മതം നൽകുന്നു."),
    "mr-IN": ("Marathi", "मी या आवाजाचा मालक आहे आणि सिंथेटिक व्हॉइस मॉडेल तयार करण्यासाठी हा आवाज वापरण्यासाठी मी Google ला संमती देतो"),
    "pl-PL": ("Polish", "Jestem właścicielem tego głosu i wyrażam zgodę na wykorzystanie go przez Google w celu utworzenia syntetycznego modelu głosu."),
    "pt-BR": ("Portuguese (Brazil)", "Eu sou o proprietário desta voz e autorizo o Google a usá-la para criar um modelo de voz sintética."),
    "ru-RU": ("Russian", "Я являюсь владельцем этого голоса и даю согласие Google на использование этого голоса для создания модели синтетического голоса."),
    "es-ES": ("Spanish (Spain)", "Soy el propietario de esta voz y doy mi consentimiento para que Google la utilice para crear un modelo de voz sintética."),
    "es-US": ("Spanish (US)", "Soy el propietario de esta voz y doy mi consentimiento para que Google la utilice para crear un modelo de voz sintética."),
    "ta-IN": ("Tamil", "நான் இந்த குரலின் உரிமையாளர் மற்றும் செயற்கை குரல் மாதிரியை உருவாக்க இந்த குரலை பயன்படுத்த குகல்க்கு நான் ஒப்புக்கொள்கிறேன்."),
    "te-IN": ("Telugu", "నేను ఈ వాయిస్ యజమానిని మరియు సింతటిక్ వాయిస్ మోడల్ ని రూపొందించడానికి ఈ వాయిస్ ని ఉపయోగించడానికి googleకి నేను సమ్మతిస్తున్నాను."),
    "th-TH": ("Thai", "ฉันเป็นเจ้าของเสียงนี้ และฉันยินยอมให้ Google ใช้เสียงนี้เพื่อสร้างแบบจำลองเสียงสังเคราะห์"),
    "tr-TR": ("Turkish", "Bu sesin sahibi benim ve Google'ın bu sesi kullanarak sentetik bir ses modeli oluşturmasına izin veriyorum."),
    "vi-VN": ("Vietnamese", "Tôi là chủ sở hữu giọng nói này và tôi đồng ý cho Google sử dụng giọng nói này để tạo mô hình giọng nói tổng hợp."),
}

# အသံနမူနာ (▶ ခလုတ်) အတွက် စာသား
SAMPLE_TEXT = {
    "my": "မင်္ဂလာပါ၊ ဒါက ကျွန်တော့်အသံ နမူနာပါ။",
    "en": "Hello, this is a short preview of my voice.",
    "th": "สวัสดีครับ นี่คือเสียงตัวอย่างของฉัน",
    "zh": "你好，这是我的声音试听。",
    "ja": "こんにちは、これは私の声のサンプルです。",
    "ko": "안녕하세요, 제 목소리 미리듣기입니다.",
    "hi": "नमस्ते, यह मेरी आवाज़ का नमूना है।",
    "es": "Hola, esta es una breve muestra de mi voz.",
    "fr": "Bonjour, ceci est un court aperçu de ma voix.",
    "de": "Hallo, das ist eine kurze Hörprobe meiner Stimme.",
    "ru": "Здравствуйте, это короткий пример моего голоса.",
    "pt": "Olá, esta é uma breve amostra da minha voz.",
    "it": "Ciao, questa è una breve anteprima della mia voce.",
    "ar": "مرحبا، هذه عينة قصيرة من صوتي.",
    "tr": "Merhaba, bu sesimin kısa bir örneğidir.",
    "id": "Halo, ini adalah contoh singkat suara saya.",
    "vi": "Xin chào, đây là đoạn nghe thử giọng nói của tôi.",
}

# အင်တာနက်မရလျှင် သုံးမည့် Edge အရန်စာရင်း (ShortName, Gender, Locale, Label)
FALLBACK_EDGE = [
    ("my-MM-NilarNeural", "Female", "my-MM", "Burmese (Myanmar)"),
    ("my-MM-ThihaNeural", "Male", "my-MM", "Burmese (Myanmar)"),
    ("en-US-AriaNeural", "Female", "en-US", "English (United States)"),
    ("en-US-JennyNeural", "Female", "en-US", "English (United States)"),
    ("en-US-GuyNeural", "Male", "en-US", "English (United States)"),
    ("en-GB-SoniaNeural", "Female", "en-GB", "English (United Kingdom)"),
    ("en-GB-RyanNeural", "Male", "en-GB", "English (United Kingdom)"),
    ("th-TH-PremwadeeNeural", "Female", "th-TH", "Thai (Thailand)"),
    ("th-TH-NiwatNeural", "Male", "th-TH", "Thai (Thailand)"),
    ("zh-CN-XiaoxiaoNeural", "Female", "zh-CN", "Chinese (Mainland)"),
    ("zh-CN-YunxiNeural", "Male", "zh-CN", "Chinese (Mainland)"),
    ("ja-JP-NanamiNeural", "Female", "ja-JP", "Japanese (Japan)"),
    ("ja-JP-KeitaNeural", "Male", "ja-JP", "Japanese (Japan)"),
    ("ko-KR-SunHiNeural", "Female", "ko-KR", "Korean (Korea)"),
    ("ko-KR-InJoonNeural", "Male", "ko-KR", "Korean (Korea)"),
    ("hi-IN-SwaraNeural", "Female", "hi-IN", "Hindi (India)"),
    ("hi-IN-MadhurNeural", "Male", "hi-IN", "Hindi (India)"),
    ("es-ES-ElviraNeural", "Female", "es-ES", "Spanish (Spain)"),
    ("es-ES-AlvaroNeural", "Male", "es-ES", "Spanish (Spain)"),
    ("fr-FR-DeniseNeural", "Female", "fr-FR", "French (France)"),
    ("fr-FR-HenriNeural", "Male", "fr-FR", "French (France)"),
    ("id-ID-GadisNeural", "Female", "id-ID", "Indonesian (Indonesia)"),
    ("id-ID-ArdiNeural", "Male", "id-ID", "Indonesian (Indonesia)"),
    ("vi-VN-HoaiMyNeural", "Female", "vi-VN", "Vietnamese (Vietnam)"),
    ("vi-VN-NamMinhNeural", "Male", "vi-VN", "Vietnamese (Vietnam)"),
]

FALLBACK_GOOGLE = {
    "my": "Myanmar (Burmese)", "en": "English", "th": "Thai", "zh-CN": "Chinese (Mandarin/China)",
    "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "es": "Spanish", "fr": "French",
    "id": "Indonesian", "vi": "Vietnamese", "de": "German", "ru": "Russian", "pt": "Portuguese",
    "it": "Italian", "ar": "Arabic", "tr": "Turkish",
}

ENGINES = ("edge", "google", "gemini", "cloud")
_voice_cache: Dict[str, Tuple[float, dict]] = {}   # engine -> (အချိန်, ရလဒ်)


# --------------------------------------------------------------------------- #
# Error စီမံခန့်ခွဲမှု
# --------------------------------------------------------------------------- #
class TTSError(RuntimeError):
    """TTS ခေါ်ဆိုမှု မအောင်မြင်သည့်အခါ ပစ်မည့် error (ပြန်ကြိုးစားသင့်/မသင့် အချက်အလက်ပါ)။"""

    def __init__(self, message: str, status: Optional[int] = None, retry_after: Optional[float] = None,
                 retryable: bool = False):
        super().__init__(config.scrub(message))
        self.status = status
        self.retry_after = retry_after
        self.retryable = retryable


_RETRY_STATUS = {429, 500, 502, 503, 504}


def _friendly(msg: str, status: Optional[int]) -> str:
    """အဖြစ်များသော error များကို နားလည်လွယ်သော အကြံပြုချက်ထည့်ပေးသည်။"""
    low = msg.lower()
    if "location is not supported" in low:
        msg += "  → Google က သင့် IP/ဒေသကို ဤ API အတွက် မထောက်ပံ့ပါ (available-regions စာမျက်နှာကို ကြည့်ပါ)။"
    elif status == 403:
        msg += ("  → 403: key ကို ဤ API အတွက် ခွင့်မပြုထားခြင်း၊ API ကို project ထဲ enable မလုပ်ရသေးခြင်း၊ "
                "billing လိုအပ်ခြင်း၊ သို့မဟုတ် key restriction ကြောင့် ဖြစ်နိုင်သည်။")
    elif status == 429:
        msg += "  → 429: rate limit/quota ပြည့်နေသည်။ TTS_CONCURRENCY ကို လျှော့ပါ သို့မဟုတ် ခဏစောင့်ပါ။"
    return msg


def classify(e: Exception, retry_403: bool = False) -> TTSError:
    """မည်သည့် exception ကိုမဆို TTSError အဖြစ် ပြောင်းပြီး ပြန်ကြိုးစားသင့်/မသင့် ဆုံးဖြတ်သည်။"""
    if isinstance(e, TTSError):
        return e
    status = getattr(e, "code", None) or getattr(e, "status_code", None) or getattr(e, "status", None)
    if not isinstance(status, int):
        m = re.search(r"\b([45]\d\d)\b", str(e)[:200])
        status = int(m.group(1)) if m else None
    msg = config.scrub(str(e) or e.__class__.__name__)
    retry_after = None
    m = re.search(r"retry(?:Delay)?\D{0,12}(\d+(?:\.\d+)?)\s*s", msg, re.I)
    if m:
        retry_after = float(m.group(1))
    retryable = (status in _RETRY_STATUS) or (retry_403 and status == 403) \
        or isinstance(e, (TimeoutError, ConnectionError)) \
        or e.__class__.__name__ in ("NoAudioReceived", "ClientConnectionError", "ServerDisconnectedError")
    return TTSError(_friendly(msg, status), status, retry_after, retryable)


class Throttle:
    """request စတင်ချိန်များအကြား အနည်းဆုံး min_interval စက္ကန့် ချန်ပေးသည်။"""

    def __init__(self, min_interval: float):
        self.min = max(0.0, min_interval)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min
        if delay:
            await asyncio.sleep(delay)


async def _with_retries(factory: Callable[[], "asyncio.Future"], throttle: Throttle, attempts: int,
                        retry_403: bool = False):
    """factory() ကို ခေါ်သည်။ ယာယီ error ဖြစ်လျှင် exponential backoff + jitter ဖြင့် ပြန်ကြိုးစားသည်။"""
    for i in range(attempts):
        await throttle.wait()
        try:
            return await factory()
        except Exception as e:  # noqa: BLE001
            err = classify(e, retry_403)
            if not err.retryable or i == attempts - 1:
                raise err
            delay = err.retry_after if err.retry_after else min(60.0, 2.0 * (2 ** i))
            await asyncio.sleep(delay + random.uniform(0, 1.0))


async def _run_all(texts: List[str], make_factory: Callable[[int, str], Callable], concurrency: int,
                   min_interval: float, attempts: int, on_item: Optional[Callable[[], None]],
                   retry_403: bool = False) -> list:
    """စာသားအားလုံးကို concurrency အကန့်အသတ်ဖြင့် တပြိုင်နက်ထုတ်ပြီး ရလဒ်များကို အစဉ်လိုက် ပြန်ပေးသည်။"""
    sem = asyncio.Semaphore(max(1, concurrency))
    throttle = Throttle(min_interval)
    results: list = [None] * len(texts)

    async def one(i: int, text: str) -> None:
        async with sem:
            results[i] = await _with_retries(make_factory(i, text), throttle, attempts, retry_403)
            if on_item:
                on_item()

    await asyncio.gather(*[one(i, t) for i, t in enumerate(texts)])
    return results


# --------------------------------------------------------------------------- #
# အသံစာရင်း
# --------------------------------------------------------------------------- #
def person_name(short: str) -> str:
    """'my-MM-NilarNeural' -> 'Nilar' ကဲ့သို့ လူနာမည်သာ ထုတ်ယူသည်။"""
    base = short.split("-")[-1]
    base = re.sub(r"Neural$", "", base)
    base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    return base or short


def _label(code: str) -> str:
    primary = code.split("-")[0].lower()
    return f"{LANG_NAMES.get(primary, primary)} ({code})"


def _list_edge() -> dict:
    voices, langs, source = [], {}, "live"
    try:
        import edge_tts
        raw = asyncio.run(edge_tts.list_voices())
        for v in raw:
            short, locale = v["ShortName"], v["Locale"]
            friendly = v.get("FriendlyName", "")
            label = friendly.split(" - ", 1)[1] if " - " in friendly else locale
            voices.append({"id": short, "name": person_name(short), "gender": v.get("Gender", ""), "locale": locale})
            langs[locale] = f"{label} ({locale})"
    except Exception:  # noqa: BLE001  (အင်တာနက်မရလျှင် အရန်စာရင်းသုံးသည်)
        voices, langs, source = [], {}, "fallback (offline)"
        for short, gender, locale, label in FALLBACK_EDGE:
            voices.append({"id": short, "name": person_name(short), "gender": gender, "locale": locale})
            langs[locale] = f"{label} ({locale})"
    languages = [{"code": c, "label": l} for c, l in sorted(langs.items(), key=lambda kv: kv[1])]
    return {"engine": "edge", "source": source, "languages": languages, "voices": voices}


def _list_google() -> dict:
    try:
        from gtts.lang import tts_langs
        langs, source = dict(tts_langs()), "gTTS"
    except Exception:  # noqa: BLE001
        langs, source = dict(FALLBACK_GOOGLE), "fallback"
    languages = [{"code": c, "label": f"{n} ({c})"} for c, n in sorted(langs.items(), key=lambda kv: kv[1])]
    voices = [{"id": c, "name": "Google Voice", "gender": "Neutral", "locale": c} for c in langs]
    return {"engine": "google", "source": source, "languages": languages, "voices": voices}


def _list_gemini() -> dict:
    """Gemini TTS: အသံ ၃၀ သည် ဘာသာစကားအားလုံးအတွက် သုံးနိုင်သည် (locale="*")။"""
    languages = [{"code": c, "label": f"{n} ({c})"} for c, n in GEMINI_LANGS]
    voices = [{"id": n, "name": f"{n} - {d}", "gender": g, "locale": "*"} for n, g, d in GEMINI_VOICES]
    return {"engine": "gemini", "source": "gemini-tts", "languages": languages, "voices": voices}


def _cloud_person(name: str) -> Tuple[str, str]:
    parts = name.split("-")
    person = parts[-1]
    tier = " ".join(parts[2:-1]).replace("Chirp3 HD", "Chirp 3 HD") or "Standard"
    return person, tier


def _list_cloud() -> dict:
    data = _cloud_request("voices", method="GET")
    voices, langs = [], {}
    for v in data.get("voices", []):
        gender = {"MALE": "Male", "FEMALE": "Female"}.get(v.get("ssmlGender", ""), "Neutral")
        person, tier = _cloud_person(v["name"])
        for code in v.get("languageCodes", []):
            voices.append({"id": v["name"], "name": f"{person} ({tier})", "gender": gender, "locale": code,
                           "tier": tier})
            langs[code] = _label(code)
    # Chirp 3 HD ကို ရှေ့ဆုံးထားသည်
    voices.sort(key=lambda x: (0 if "Chirp 3 HD" in x["tier"] else 1, x["tier"], x["name"]))
    languages = [{"code": c, "label": l} for c, l in sorted(langs.items(), key=lambda kv: kv[1])]
    return {"engine": "cloud", "source": "cloud-tts", "languages": languages, "voices": voices}


def list_voices(engine: str) -> dict:
    """အသံစာရင်းကို ၁ နာရီ cache လုပ်ထားသည်။"""
    cached = _voice_cache.get(engine)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    data = {"edge": _list_edge, "google": _list_google, "gemini": _list_gemini, "cloud": _list_cloud}[engine]()
    if data["source"] != "fallback (offline)":
        _voice_cache[engine] = (time.time(), data)
    return data


def pick_edge_voice(lang_code: str, gender: Optional[str] = None) -> Tuple[str, str]:
    """ဘာသာစကားတစ်ခုအတွက် Edge-TTS အသံ (voice id, locale) ကို ရွေးပေးသည် (fallback အတွက်)။"""
    primary = (lang_code or "en").split("-")[0].lower()
    data = list_voices("edge")
    cands = [v for v in data["voices"] if v["locale"].split("-")[0].lower() == primary]
    if not cands:
        cands = [v for v in data["voices"] if v["locale"].startswith("en-")]
    pref = [v for v in cands if gender and v["gender"] == gender] or cands
    return pref[0]["id"], pref[0]["locale"]


# --------------------------------------------------------------------------- #
# Google Cloud Text-to-Speech (REST)
# --------------------------------------------------------------------------- #
def _cloud_request(path: str, payload: Optional[dict] = None, method: str = "POST") -> dict:
    """texttospeech.googleapis.com ကို ခေါ်သည်။ key ကို URL ထဲမထည့်ဘဲ header ဖြင့်သာ ပို့သည် (log မှာ မပေါက်စေရန်)။"""
    key = config.get_cloud_tts_key()
    if not key:
        raise TTSError("GOOGLE_TTS_API_KEY (သို့မဟုတ် GEMINI_API_KEY) မသတ်မှတ်ရသေးပါ။")
    req = urllib.request.Request(
        f"https://texttospeech.googleapis.com/v1/{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:  # noqa: BLE001
            msg = body[:300]
        retry_after = None
        if e.headers and e.headers.get("Retry-After", "").isdigit():
            retry_after = float(e.headers["Retry-After"])
        raise TTSError(_friendly(f"Cloud TTS {e.code}: {msg}", e.code), e.code, retry_after, e.code in _RETRY_STATUS)
    except urllib.error.URLError as e:
        raise TTSError(f"Cloud TTS ချိတ်ဆက်မရပါ: {e.reason}", retryable=True)


def _cloud_synth(text: str, voice: str, out: Path) -> Path:
    lang = "-".join(voice.split("-")[:2])
    data = _cloud_request("text:synthesize", {
        "input": {"text": text},
        "voice": {"languageCode": lang, "name": voice},
        "audioConfig": {"audioEncoding": "MP3"},
    })
    out.write_bytes(base64.b64decode(data["audioContent"]))
    return out


# --------------------------------------------------------------------------- #
# Google AI Studio - Gemini TTS
# --------------------------------------------------------------------------- #
def _genai_client():
    key = config.get_gemini_key()
    if not key:
        raise TTSError("GEMINI_API_KEY မသတ်မှတ်ရသေးပါ။ .env ဖိုင်ထဲ ထည့်ပြီး server ကို ပြန်စတင်ပါ။")
    from google import genai
    return genai.Client(api_key=key)


def _save_gemini_audio(data: bytes, out: Path) -> Path:
    """Gemini ပြန်ပေးသော အသံ (WAV header ပါ သို့မဟုတ် raw PCM 24kHz) ကို wav ဖိုင်အဖြစ် သိမ်းသည်။"""
    if data[:4] == b"RIFF":
        out.write_bytes(data)
    else:
        write_wav(out, np.frombuffer(data, dtype=np.int16), SAMPLE_RATE)
    return out


def _gemini_synth(client, text: str, voice: str, out: Path) -> Path:
    """Gemini TTS တစ်ကြိမ်ခေါ်သည်။ voice သည် ပါပြီးသားအသံနာမည် (Kore ...) သို့မဟုတ် replicate လုပ်ထားသော voicekey_..."""
    try:  # SDK အသစ် (Gemini 3.8 TTS) ပုံစံ
        resp = client.models.generate_content(
            model=config.GEMINI_TTS_MODEL,
            contents=[{"role": "user", "parts": [{"text": text}]}],
            config={"response_modalities": ["AUDIO"], "speech_config": {"voice_config": {"voice": voice}}},
        )
    except Exception as e:  # noqa: BLE001
        if e.__class__.__name__ not in ("ValidationError", "TypeError"):
            raise
        # SDK အဟောင်းတွင် 'voice' field မသိသောကြောင့် ပုံစံအဟောင်းဖြင့် ပြန်စမ်းသည်
        from google.genai import types
        resp = client.models.generate_content(
            model=config.GEMINI_TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
            ),
        )
    try:
        data = resp.candidates[0].content.parts[0].inline_data.data
    except (AttributeError, IndexError, TypeError):
        data = None
    if not data:
        raise TTSError("Gemini TTS က အသံ ပြန်မပေးပါ (စာသားကို ပယ်ချခြင်း ဖြစ်နိုင်)။", retryable=True)
    return _save_gemini_audio(bytes(data), out)


def prepare_wav24(src: Path, dst: Path, max_seconds: Optional[float] = None) -> Path:
    """Voice replication အတွက် 24kHz mono 16-bit WAV သို့ ပြောင်းသည်။"""
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn"]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(dst)]
    run(cmd)
    return dst


def replicate_voice(reference_wav: Path, consent_wav: Path) -> str:
    """
    Gemini Voice Replication: အသံနမူနာ (၁၀-၃၀ စက္ကန့်) + ခွင့်ပြုချက်အသံသွင်းမှု နှစ်ခုဖြင့်
    ကိုယ့်အသံပုံစံ (voicekey_...) ကို ဖန်တီးသည်။ store=False ဖြစ်သဖြင့် Google ဘက်တွင် အသံပရိုဖိုင် မသိမ်းထားပါ
    (key က ၇ ရက်သာ အသုံးပြုနိုင်သည်)။
    """
    client = _genai_client()
    src_b64 = base64.b64encode(reference_wav.read_bytes()).decode("ascii")
    consent_b64 = base64.b64encode(consent_wav.read_bytes()).decode("ascii")
    try:
        voice = client.voices.create(
            store=False,
            voice={
                "model": config.GEMINI_TTS_MODEL,
                "type": "replicated",
                "replicated": {
                    "source_audio": {"mime_type": "audio/wav", "data": src_b64},
                    "consent_audio": {"mime_type": "audio/wav", "data": consent_b64},
                },
            },
        )
    except AttributeError:
        raise TTSError("google-genai SDK အဟောင်းဖြစ်နေသည် (Voice Clone အတွက် 2.25.0 နှင့်အထက် လိုသည်)။ "
                       "pip install -U google-genai ကို run ပါ။")
    except Exception as e:  # noqa: BLE001
        err = classify(e)
        raise TTSError("Voice Clone ဖန်တီး၍မရပါ: " + str(err) +
                       "  (ခွင့်ပြုချက်စာသားကို အတိအကျ ဖတ်ထားခြင်း၊ နမူနာနှင့် ခွင့်ပြုချက်ကို အသံပိုင်ရှင်တစ်ဦးတည်းက "
                       "မိုက်ခရိုဖုန်းတစ်ခုတည်းဖြင့် သွင်းထားခြင်း ရှိမရှိ စစ်ပါ)")
    key = getattr(voice, "key", None) or getattr(voice, "id", None)
    if not key:
        raise TTSError("Voice Clone ၏ voice key ကို မရရှိပါ။")
    return str(key)


# --------------------------------------------------------------------------- #
# Edge-TTS / gTTS
# --------------------------------------------------------------------------- #
def _gtts_one(text: str, lang: str, out: Path) -> Path:
    from gtts import gTTS
    gTTS(text=text, lang=lang).save(str(out))
    return out


# --------------------------------------------------------------------------- #
# စာကြောင်းများကို အစုလိုက် (chunk) ခွဲခြင်း - request အရေအတွက် လျှော့ရန်
# --------------------------------------------------------------------------- #
CHUNK_LIMIT = {"gemini": 800, "cloud": 1200}   # တစ်ကြိမ်ပို့မည့် စာလုံးအများဆုံး


def group_lines(lines: List[str], engine: str, one_by_one: bool) -> List[List[int]]:
    """
    ဆက်တိုက်လာသော စာကြောင်းများကို request တစ်ခုအတွက် အစုတစ်ခုအဖြစ် စုသည် (Gemini / Cloud သာ)။
    one_by_one=True (ဇာတ်ကွက်တစ်ခုချင်းစီ ဗီဒီယိုနှင့် တိတိကျကျ ကိုက်ရန်လိုသည့်အခါ) သို့မဟုတ်
    Edge/gTTS ဆိုလျှင် တစ်ကြောင်းချင်း ခွဲထားသည်။
    """
    if one_by_one or engine not in CHUNK_LIMIT:
        return [[i] for i in range(len(lines))]
    limit = CHUNK_LIMIT[engine]
    groups: List[List[int]] = []
    cur: List[int] = []
    size = 0
    for i, line in enumerate(lines):
        if cur and size + len(line) + 1 > limit:
            groups.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(line) + 1
    if cur:
        groups.append(cur)
    return groups


# --------------------------------------------------------------------------- #
# အသံထုတ်ခြင်း (အဓိက function)
# --------------------------------------------------------------------------- #
def synth_texts(
    texts: List[str], engine: str, voice: str, language: str, work: Path,
    on_progress: Optional[Callable[[float], None]] = None,
) -> List[Path]:
    """စာသားတစ်ခုချင်းစီအတွက် အသံဖိုင်တစ်ခုစီ ထုတ်သည် (batching + throttle + backoff ပါ)။"""
    if engine not in ENGINES:
        raise ValueError(f"Unknown TTS engine: {engine}")
    done = {"n": 0}

    def tick() -> None:
        done["n"] += 1
        if on_progress:
            on_progress(done["n"] / max(1, len(texts)))

    if engine == "edge":
        def factory_edge(i: int, text: str):
            out = work / f"clip_{i:04d}.mp3"
            return lambda: edge_batch_one(text, voice, out)
        return asyncio.run(_run_all(texts, factory_edge, 3, 0.25, 5, tick, retry_403=True))

    if engine == "google":
        def factory_g(i: int, text: str):
            out = work / f"clip_{i:04d}.mp3"
            return lambda: asyncio.to_thread(_gtts_one, text, language, out)
        return asyncio.run(_run_all(texts, factory_g, 2, 1.0, 5, tick))

    if engine == "cloud":
        def factory_c(i: int, text: str):
            out = work / f"clip_{i:04d}.mp3"
            return lambda: asyncio.to_thread(_cloud_synth, text, voice, out)
        return asyncio.run(_run_all(texts, factory_c, 3, 0.3, 6, tick))

    # gemini
    client = _genai_client()

    def factory_gm(i: int, text: str):
        out = work / f"clip_{i:04d}.wav"
        return lambda: asyncio.to_thread(_gemini_synth, client, text, voice, out)
    return asyncio.run(_run_all(texts, factory_gm, config.TTS_CONCURRENCY,
                                max(config.TTS_MIN_INTERVAL, 0.6), 6, tick))


def synth_preview(engine: str, voice: str, language: str) -> Tuple[bytes, str]:
    """▶ ခလုတ်အတွက် အသံနမူနာ တိုတိုကို (အသံဖိုင် bytes, MIME) အဖြစ် ပြန်ပေးသည်။"""
    import shutil
    import tempfile
    lang_key = (language or voice).split("-")[0]
    if (language or "").startswith("zh"):
        lang_key = "zh"
    text = SAMPLE_TEXT.get(lang_key, SAMPLE_TEXT["en"])
    tmp = Path(tempfile.mkdtemp(prefix="tts_prev_"))
    try:
        clip = synth_texts([text], engine, voice, language, tmp)[0]
        mime = "audio/wav" if clip.suffix == ".wav" else "audio/mpeg"
        return clip.read_bytes(), mime
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
