"""
llm_fallback.py - Gemini မရတဲ့အခါ (503/504/429/quota ...) အလိုအလျောက် ပြောင်းသုံးမည့် အရန် provider များ

  anthropic : Claude (Anthropic Messages API)      .env: ANTHROPIC_API_KEY ၊ ANTHROPIC_MODEL (မူလ claude-sonnet-5-5)
  openai    : OpenAI-compatible chat completions   .env: LLM_OPENAI_API_KEY (သို့ OPENAI_API_KEY) ၊ LLM_OPENAI_MODEL (မဖြစ်မနေ)
              LLM_OPENAI_BASE_URL (မူလ https://api.openai.com/v1 - OpenRouter / Groq / DeepSeek စသည်ဖြင့်လည်း ပြောင်းသုံးနိုင်)

  LLM_FALLBACK=anthropic,openai   (စမ်းမည့်အစဉ်၊ မထည့်လျှင် key ရှိသည့် provider များကို anthropic -> openai အစဉ်)

စာသား (prompt) သက်သက်သာ ပို့သည် - ဗီဒီယိုကို မမြင်နိုင်ပါ (Gemini သာ မြင်နိုင်သည်)။
SDK မလို၊ stdlib (urllib) သာသုံးသည်။
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from app.core import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_ORDER = ("anthropic", "openai")
# Python ၏ မူလ User-Agent (Python-urllib) ကို Groq စသည့် Cloudflare နောက်က API များက 403 (error 1010) ဖြင့် ပိတ်တတ်သဖြင့် ကိုယ်ပိုင်အမည်ပေးသည်
USER_AGENT = "MovieRecapStudio/1.0"


def _anthropic_key() -> Optional[str]:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None


def _anthropic_model() -> str:
    return (os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5-5").strip()


def _openai_key() -> Optional[str]:
    return (os.getenv("LLM_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip() or None


def _openai_model() -> str:
    return (os.getenv("LLM_OPENAI_MODEL") or "").strip()


def _openai_base() -> str:
    return (os.getenv("LLM_OPENAI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")


def _configured(name: str) -> bool:
    if name == "anthropic":
        return bool(_anthropic_key())
    if name == "openai":
        return bool(_openai_key() and _openai_model())
    return False


def providers() -> List[str]:
    """key ထည့်ထားပြီး သုံးနိုင်သော အရန် provider များကို စမ်းမည့်အစဉ်အတိုင်း ပြန်ပေးသည်။"""
    order = [p.strip().lower() for p in (os.getenv("LLM_FALLBACK") or "").split(",") if p.strip()] or list(_DEFAULT_ORDER)
    seen: List[str] = []
    for p in order:
        if p in _DEFAULT_ORDER and p not in seen and _configured(p):
            seen.append(p)
    return seen


def available() -> bool:
    return bool(providers())


def secrets() -> List[str]:
    return [k for k in (_anthropic_key(), _openai_key()) if k]


# --------------------------------------------------------------------------- #
def _post(url: str, headers: dict, payload: dict, timeout: float = 180.0) -> dict:
    """JSON POST။ 429/5xx ဆိုလျှင် တစ်ကြိမ်သာ အတိုချုံး စောင့်ပြီး ပြန်စမ်းသည် (နောက်ထပ်မှာ နောက် provider သို့ ပြောင်းမည်)။"""
    body = json.dumps(payload).encode("utf-8")
    last = ""
    for attempt in range(2):
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            last = f"{e.code}: {text}"
            if e.code in (429, 500, 502, 503, 504, 529) and attempt == 0:
                ra = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(15.0, float(ra)) if ra and ra.replace(".", "", 1).isdigit() else 3.0)
                continue
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = str(getattr(e, "reason", e))
            if attempt == 0:
                time.sleep(3.0)
                continue
    raise RuntimeError(config.scrub(last))


def _call_anthropic(prompt: str, temperature: float) -> str:
    data = _post(ANTHROPIC_URL,
                 {"x-api-key": _anthropic_key() or "", "anthropic-version": ANTHROPIC_VERSION},
                 {"model": _anthropic_model(), "max_tokens": 16000,
                  "temperature": max(0.0, min(1.0, temperature)),
                  "messages": [{"role": "user", "content": prompt}]})
    text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
    if not text.strip():
        raise RuntimeError("Anthropic က အဖြေအလွတ် ပြန်ပေးသည်")
    return text


def _call_openai(prompt: str, temperature: float) -> str:
    data = _post(_openai_base() + "/chat/completions",
                 {"Authorization": f"Bearer {_openai_key()}"},
                 {"model": _openai_model(), "temperature": temperature,
                  "messages": [{"role": "user", "content": prompt}]})
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = ""
    if not text.strip():
        raise RuntimeError("OpenAI-compatible provider က အဖြေအလွတ် ပြန်ပေးသည်")
    return text


_CALLERS = {"anthropic": _call_anthropic, "openai": _call_openai}


def extract_json(text: str) -> str:
    """model က JSON ၏ ရှေ့နောက်မှာ စာထည့်လာလျှင်လည်း JSON အပိုင်းသာ ထုတ်ယူသည်။"""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    starts = [i for i in (t.find("["), t.find("{")) if i != -1]
    if not starts:
        return t
    a = min(starts)
    b = max(t.rfind("]"), t.rfind("}"))
    return t[a:b + 1] if b > a else t


def generate(prompt: str, temperature: float = 0.5, json_mode: bool = False) -> Tuple[str, str]:
    """အရန် provider များကို စဉ်လိုက်စမ်းပြီး (စာသား, provider အမည်) ကို ပြန်ပေးသည်။ အားလုံးမရလျှင် RuntimeError။"""
    errors: List[str] = []
    for name in providers():
        try:
            text = _CALLERS[name](prompt, temperature)
            return (extract_json(text) if json_mode else text), name
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {config.scrub(e)[:160]}")
            print(f"[LLM] fallback provider {name} failed: {config.scrub(e)[:160]}", flush=True)
    raise RuntimeError("အရန် provider များ မအောင်မြင်ပါ - " + (" | ".join(errors) or "key မထည့်ရသေးပါ"))
