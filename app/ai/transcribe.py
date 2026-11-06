"""
transcribe.py - faster-whisper ဖြင့် ဗီဒီယိုအသံကို စာသားပြောင်းခြင်း (Exact Transcription + SRT)

  * မူရင်းဘာသာစကားအတိုင်း အတိအကျ ပြောင်းသည် (ဘာသာမပြန်၊ ပြင်မရေး)
  * စာတန်းတစ်ခုချင်းစီ၏ စတင်/ပြီးဆုံးချိန် (timestamp) ပါသည်
  * .env: WHISPER_MODEL (small/medium အကြံပြု)၊ WHISPER_BEAM (GPU တွင် 5)၊ WHISPER_LANGUAGE (ဥပမာ en, zh, ja - မထည့်လျှင် auto)
"""
import hashlib
import json
import os
import time
import uuid
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

from app.core import config
from app.core.services import run

_model = None
_model_device: Optional[str] = None


def _pick_device() -> str:
    dev = config.WHISPER_DEVICE.lower()
    if dev in ("cpu", "cuda"):
        return dev
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _load(device: str):
    global _model, _model_device
    from faster_whisper import WhisperModel
    compute = (config.WHISPER_COMPUTE or "float16") if device == "cuda" else "int8"
    threads = config.WHISPER_THREADS or (os.cpu_count() or 4)
    _model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute, cpu_threads=threads)
    _model_device = device
    return _model


CACHE_DIR = config.BASE_DIR / "cache"


def _fingerprint(video: Path) -> str:
    h = hashlib.sha1()
    size = video.stat().st_size
    h.update(str(size).encode())
    with open(video, "rb") as f:
        h.update(f.read(4 << 20))
        if size > (8 << 20):
            f.seek(size - (4 << 20))
            h.update(f.read(4 << 20))
    return h.hexdigest()[:20]


def extract_audio(video: Path, out_wav: Path) -> None:
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out_wav)])


def _read_wav_f32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _clean_segments(segs: List[dict]) -> List[dict]:
    """အချိန်မမှန်သော စာတန်းများကို ဖယ်၊ Whisper hallucination loop (စာကြောင်းတူ ၃ ကြိမ်နှင့်အထက်ဆက်တိုက်) ကို ဖြတ်သည်။"""
    out: List[dict] = []
    repeat = 0
    for s in segs:
        if s["end"] - s["start"] < 0.05:
            continue
        if out and s["text"].strip().lower() == out[-1]["text"].strip().lower():
            repeat += 1
            if repeat >= 2:
                continue
        else:
            repeat = 0
        out.append(s)
    return out


def transcribe(
    video: Path, work: Path, on_progress: Optional[Callable[[float, str], None]] = None
) -> Tuple[List[dict], str, float]:
    """
    ပြန်ပေးသည်: (segments [{start,end,text}], detected_language, audio_duration)
    """
    def report(f: float, m: str) -> None:
        if on_progress:
            on_progress(f, m)

    beam = max(1, config.WHISPER_BEAM)
    lang_env = os.getenv("WHISPER_LANGUAGE", "").strip().lower()
    lang = None if lang_env in ("", "auto") else lang_env

    # ဗီဒီယိုတူ၊ model/beam/language တူလျှင် ယခင် ရလဒ်ကို ပြန်သုံးသည်
    CACHE_DIR.mkdir(exist_ok=True)
    tag = f"{config.TRANSCRIBE_BACKEND}-{config.transcribe_endpoint()[1]}" if config.transcribe_api() else config.WHISPER_MODEL
    tag = tag.replace("/", "_")
    cache_file = CACHE_DIR / f"whisper_{_fingerprint(video)}_{tag}_b{beam}_{lang or 'auto'}.json"
    if cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            report(1.0, "Using the saved transcript of this video (cache)...")
            return data["segments"], data["language"], float(data["duration"])
        except (ValueError, KeyError, OSError):
            pass

    if config.transcribe_api():     # API (Groq / OpenAI) ဖြင့် - GPU မလို
        result = _transcribe_api(video, work, lang, report)
        try:
            cache_file.write_text(json.dumps({"segments": result[0], "language": result[1], "duration": result[2]},
                                             ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return result

    t0 = time.time()
    report(0.0, "Extracting audio...")
    audio = work / "source.wav"
    extract_audio(video, audio)

    report(0.02, "Loading Whisper model (first run downloads it)...")
    model = _model
    if model is None:
        device = _pick_device()
        try:
            model = _load(device)
        except Exception as e:  # noqa: BLE001
            if device != "cuda":
                raise
            report(0.02, f"GPU မသုံးနိုင်ပါ ({e}) - CPU ဖြင့် ဆက်လုပ်နေသည်...")
            model = _load("cpu")

    def run_once(m):
        opts = dict(task="transcribe", vad_filter=True, beam_size=beam, language=lang,
                    condition_on_previous_text=False,   # စာကြောင်းတူ ထပ်ခါထပ်ခါထွက်ခြင်း (hallucination) လျှော့ရန်
                    vad_parameters=dict(min_silence_duration_ms=500))
        try:
            segments, info = m.transcribe(str(audio), **opts)
        except TypeError as e:
            if "metadata_errors" not in str(e):
                raise
            report(0.02, "PyAV အဟောင်းကို ကျော်ပြီး အသံကို တိုက်ရိုက်ဖတ်နေသည်...")
            segments, info = m.transcribe(_read_wav_f32(audio), **opts)
        total = float(getattr(info, "duration", 0) or 0)
        out: List[dict] = []
        for s in segments:
            text = s.text.strip()
            if text:
                out.append({"start": float(s.start), "end": float(s.end), "text": text})
            if total > 0:
                report(min(float(s.end) / total, 1.0), f"Transcribing... {int(min(float(s.end) / total, 1.0) * 100)}%")
        return _clean_segments(out), info.language, total

    try:
        result = run_once(model)
    except Exception as e:  # noqa: BLE001
        if _model_device == "cuda":
            report(0.02, f"GPU မရပါ ({e.__class__.__name__}) - CPU ဖြင့် ပြန်လုပ်နေသည်...")
            result = run_once(_load("cpu"))
        else:
            raise
    print(f"[Whisper] model={config.WHISPER_MODEL} device={_model_device} beam={beam} lang={result[1]} "
          f"-> {len(result[0])} segments in {time.time() - t0:.1f}s", flush=True)
    try:
        cache_file.write_text(json.dumps({"segments": result[0], "language": result[1], "duration": result[2]},
                                         ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return result


# --------------------------------------------------------------------------- #
# Speech-to-Text API (Groq / OpenAI - OpenAI-compatible /audio/transcriptions)
#   * အသံကို 16kHz mono mp3 အပိုင်းများ (မူလ ၁၀ မိနစ်စီ) ဖြတ်ပြီး ပို့သည် -> ဖိုင်အရွယ်ကန့်သတ်ချက်အောက်မှာ ကျန်မည်
#   * အပိုင်းတစ်ခုချင်း၏ အချိန်ကို offset ပေါင်း၍ တစ်ဆက်တည်းဖြစ်အောင် ပြန်စီသည်
# --------------------------------------------------------------------------- #
_LANG_CODES = {
    "english": "en", "burmese": "my", "myanmar": "my", "chinese": "zh", "cantonese": "yue", "japanese": "ja",
    "korean": "ko", "thai": "th", "vietnamese": "vi", "hindi": "hi", "indonesian": "id", "malay": "ms",
    "spanish": "es", "french": "fr", "german": "de", "russian": "ru", "portuguese": "pt", "italian": "it",
    "arabic": "ar", "turkish": "tr", "dutch": "nl", "polish": "pl", "ukrainian": "uk", "bengali": "bn",
    "tamil": "ta", "telugu": "te", "urdu": "ur", "persian": "fa", "hebrew": "he", "greek": "el", "czech": "cs",
    "swedish": "sv", "romanian": "ro", "hungarian": "hu", "tagalog": "fil", "filipino": "fil", "nepali": "ne",
    "khmer": "km", "lao": "lo", "sinhala": "si", "marathi": "mr", "gujarati": "gu", "kannada": "kn",
    "malayalam": "ml", "punjabi": "pa", "swahili": "sw", "danish": "da", "finnish": "fi", "norwegian": "no",
}


def available() -> Tuple[bool, str]:
    """Speech-to-Text သုံးနိုင်မနိုင် (ok, မသုံးနိုင်ကြောင်း အကြောင်းရင်း)။"""
    if config.transcribe_api():
        if not config.get_transcribe_key():
            return False, "Speech-to-Text API key မထည့်ရသေးပါ (.env ထဲ GROQ_API_KEY သို့မဟုတ် OPENAI_API_KEY ထည့်ပါ)။"
        return True, ""
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        return False, "faster-whisper မသွင်းရသေးပါ (pip install -r requirements-local.txt) - သို့မဟုတ် .env ထဲ TRANSCRIBE_BACKEND=groq ထားပါ။"
    return True, ""


def _lang_code(name: str) -> str:
    n = (name or "").strip().lower()
    if 2 <= len(n) <= 3 and n.isalpha():
        return n
    return _LANG_CODES.get(n, "en")


def _multipart(fields: dict, filename: str, data: bytes) -> Tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
             for k, v in fields.items()]
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f"Content-Type: audio/mpeg\r\n\r\n").encode("utf-8") + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _api_chunk(path: Path, lang: Optional[str]) -> dict:
    """အပိုင်းတစ်ခုကို API သို့ ပို့သည်။ 429/5xx ဆိုလျှင် စောင့်ပြီး ပြန်ကြိုးစားသည်။"""
    base, model = config.transcribe_endpoint()
    key = config.get_transcribe_key()
    fields = {"model": model, "response_format": "verbose_json", "temperature": "0"}
    if lang:
        fields["language"] = lang
    body, ctype = _multipart(fields, path.name, path.read_bytes())
    url = base.rstrip("/") + "/audio/transcriptions"
    last = ""
    for attempt in range(6):
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": ctype,
                                              "Accept": "application/json", "User-Agent": "MovieRecapStudio/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            last = f"{e.code}: {text}"
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                ra = e.headers.get("Retry-After") if e.headers else None
                time.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(60.0, 3.0 * 2 ** attempt))
                continue
            hint = "  → API key မှားနေနိုင်သည်။" if e.code in (401, 403) else ""
            raise RuntimeError(config.scrub(f"Speech-to-Text API {last}{hint}"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = str(getattr(e, "reason", e))
            if attempt < 5:
                time.sleep(min(30.0, 2.0 * 2 ** attempt))
                continue
    raise RuntimeError(config.scrub(f"Speech-to-Text API ချိတ်ဆက်မရပါ: {last}"))


def _transcribe_api(video: Path, work: Path, lang: Optional[str], report) -> Tuple[List[dict], str, float]:
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    t0 = time.time()
    report(0.0, "Extracting audio...")
    for old in work.glob("tchunk_*.mp3"):
        old.unlink()
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
         "-f", "segment", "-segment_time", str(config.TRANSCRIBE_CHUNK_SECONDS), "-reset_timestamps", "1",
         str(work / "tchunk_%03d.mp3")])
    files = sorted(work.glob("tchunk_*.mp3"))
    if not files:
        raise RuntimeError("ဗီဒီယိုထဲတွင် အသံ မတွေ့ပါ။")
    durs = [float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                       str(f)]).strip() or 0) for f in files]
    offs = [sum(durs[:i]) for i in range(len(files))]
    base, model = config.transcribe_endpoint()
    done = {"n": 0}

    def tick() -> None:
        done["n"] += 1
        report(done["n"] / len(files), f"Transcribing (API)... {done['n']}/{len(files)}")

    # ပထမအပိုင်းကို အရင်ပို့၍ ဘာသာစကားကို သိပြီး ကျန်အပိုင်းများကို ထိုဘာသာစကားဖြင့် တပြိုင်နက်ပို့သည် (တသမတ်တည်းဖြစ်စေရန်)
    first = _api_chunk(files[0], lang)
    detected = lang or _lang_code(first.get("language", ""))
    tick()
    results = [first]
    if len(files) > 1:
        with ThreadPoolExecutor(max_workers=config.TRANSCRIBE_CONCURRENCY) as ex:
            futs = [ex.submit(_api_chunk, f, detected) for f in files[1:]]
            for fu in futs:
                results.append(fu.result())
                tick()

    out: List[dict] = []
    for res, off in zip(results, offs):
        for s in res.get("segments") or []:
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            if float(s.get("no_speech_prob", 0) or 0) > 0.8 and float(s.get("avg_logprob", 0) or 0) < -1.0:
                continue      # စကားမပါသော အပိုင်းမှ Whisper ၏ ထင်ယောင်ထင်မှား စာသား
            if float(s.get("compression_ratio", 0) or 0) > 2.4:
                continue      # စာကြောင်းတူ ထပ်ခါထပ်ခါ (hallucination loop)
            out.append({"start": float(s["start"]) + off, "end": float(s["end"]) + off, "text": text})
    total = sum(durs)
    print(f"[Transcribe API] {config.TRANSCRIBE_BACKEND}/{model} lang={detected} -> {len(out)} segments "
          f"({len(files)} chunk(s)) in {time.time() - t0:.1f}s", flush=True)
    return _clean_segments(out), detected, total
