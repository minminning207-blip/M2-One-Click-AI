"""
fix_tts_retry.py - tts_engines.py ကို အလိုအလျောက် ပြင်ပေးသော script
  * quota ကုန်လျှင် (retry delay 90 စက္ကန့်ထက်ကြီး) မစောင့်ဘဲ ချက်ချင်း error ပြ
  * delay ကို အများဆုံး 60 စက္ကန့်သာ
  * Edge-TTS တပြိုင်နက်ထုတ်မှု 3 -> 5 (မြန်စေရန်)
run: python tools/fix_tts_retry.py     (app/ai/tts_engines.py ကို ပြင်မည်၊ .bak ကူးသိမ်းပေးမည်)
"""
import shutil
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "app" / "ai" / "tts_engines.py"
s = p.read_text(encoding="utf-8")
if "Quota ကုန်နေသည်" in s:
    raise SystemExit("ပြင်ပြီးသားဖြစ်သည်။")

old1 = "            delay = err.retry_after if err.retry_after else min(60.0, 2.0 * (2 ** i))\n"
new1 = (old1 +
        "            if delay > 90:   # quota ကုန်နေ (နာရီချီစောင့်ရမည်) -> ချက်ချင်း error\n"
        "                raise TTSError(f\"Quota ကုန်နေသည် (retry delay {delay:.0f}s) - key ၏ quota/billing ကိုစစ်ပါ။ \" + str(err), err.status)\n"
        "            delay = min(delay, 60.0)\n")
old2 = "return asyncio.run(_run_all(texts, factory_edge, 3, 0.25, 5, tick, retry_403=True))"
new2 = "return asyncio.run(_run_all(texts, factory_edge, 5, 0.15, 5, tick, retry_403=True))"
for o in (old1, old2):
    if o not in s:
        raise SystemExit("tts_engines.py ၏ မျှော်လင့်ထားသော စာကြောင်း မတွေ့ပါ - ဖိုင်ကို ပြင်ထားပြီးသား သို့မဟုတ် ဗားရှင်းကွာနေနိုင်သည်။")
shutil.copy(p, p.with_name("tts_engines.py.bak"))
p.write_text(s.replace(old1, new1, 1).replace(old2, new2, 1), encoding="utf-8")
print("OK - tts_engines.py ကို ပြင်ပြီးပါပြီ (backup: tts_engines.py.bak)")
