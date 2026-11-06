"""
captcha.py - Google reCAPTCHA v2 (checkbox) ကို server ဘက်မှာ စစ်ဆေးခြင်း
RECAPTCHA_SITE_KEY / RECAPTCHA_SECRET မထည့်ထားလျှင် ပိတ်ထားသည် (verify() က အမြဲ True)။
"""
import json
import urllib.parse
import urllib.request

from app.core import config


def enabled() -> bool:
    return bool(config.RECAPTCHA_SITE_KEY and config.RECAPTCHA_SECRET)


def verify(token: str, ip: str = "") -> bool:
    if not enabled():
        return True
    if not token:
        return False
    data = urllib.parse.urlencode({"secret": config.RECAPTCHA_SECRET, "response": token, "remoteip": ip or ""}).encode()
    try:
        req = urllib.request.Request(config.RECAPTCHA_VERIFY_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("success"))
    except Exception as e:  # noqa: BLE001
        print(f"[CAPTCHA] စစ်ဆေး၍မရပါ: {config.scrub(e)}", flush=True)
        return False      # စစ်မရလျှင် ခွင့်မပြု (လုံခြုံရေးအတွက်)
