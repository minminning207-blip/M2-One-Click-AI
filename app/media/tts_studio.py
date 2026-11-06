"""
tts_studio.py - စာတန်းထိုးနှင့် အချိန်ဇယား ဆိုင်ရာ လုပ်ဆောင်ချက်များ

  * စာသားကို စာတန်းထိုးလိုင်းများအဖြစ် ခွဲခြင်း (မြန်မာ / CJK / ထိုင်း အတွက်ပါ)
  * အသံကလစ်များကို အချိန်ဇယားပေါ် စီခြင်း (assemble) - TTS ကလစ်၏ အစ/အဆုံး တိတ်ဆိတ်ချိန်ကို ဖြတ်ပြီးမှ စီသည်
  * SRT / ASS (စာတန်းထိုးပုံစံ) ဖိုင်ထုတ်ခြင်း
"""
import math
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

import numpy as np

from app.core.services import SAMPLE_RATE, read_wav, to_pcm, write_wav


# --------------------------------------------------------------------------- #
# တိတ်ဆိတ်ချိန် ဖြတ်ခြင်း
# --------------------------------------------------------------------------- #
def trim_silence(data: np.ndarray, thresh: int = 500, pad: float = 0.06) -> np.ndarray:
    """TTS အသံ၏ အစ/အဆုံးရှိ တိတ်ဆိတ်ချိန်ကို ဖြတ်သည် (စကားလုံးနားတွင် pad စက္ကန့် ချန်)။"""
    if len(data) == 0:
        return data
    idx = np.flatnonzero(np.abs(data.astype(np.int32)) > thresh)
    if len(idx) == 0:
        return data
    p = int(pad * SAMPLE_RATE)
    a = max(0, int(idx[0]) - p)
    b = min(len(data), int(idx[-1]) + p)
    return data[a:b].copy()


def voice_seconds(clip: Path, work: Path, tag: str, tempo: float = 1.0) -> float:
    """TTS ကလစ်၏ အစစ်အမှန် စကားပြောချိန် (တိတ်ဆိတ်ချိန်မပါ၊ tempo ကိုက်ပြီး)။"""
    pcm = work / f"len_{tag}.wav"
    to_pcm(clip, pcm, tempo)
    return len(trim_silence(read_wav(pcm))) / SAMPLE_RATE


# --------------------------------------------------------------------------- #
# စာသား → စာတန်းထိုးလိုင်းများ (မြန်မာ / CJK / Thai ကို ထောက်ပံ့)
# --------------------------------------------------------------------------- #
def visible_len(s: str) -> int:
    total = 0.0
    for ch in s:
        cat = unicodedata.category(ch)
        if cat == "Mn":
            continue
        total += 0.6 if cat == "Mc" else 1.0
    return int(math.ceil(total))


def _is_burmese(ch: str) -> bool:
    return 0x1000 <= ord(ch) <= 0x109F


def _burmese_syllables(word: str) -> List[str]:
    units, cur = [], ""
    for i, ch in enumerate(word):
        cp = ord(ch)
        nxt = word[i + 1] if i + 1 < len(word) else ""
        starter = 0x1000 <= cp <= 0x102A or cp == 0x103F
        if starter and cur and cur[-1] != "္" and nxt not in ("္", "်"):
            units.append(cur)
            cur = ""
        cur += ch
    if cur:
        units.append(cur)
    return units


def _clusters(word: str) -> List[str]:
    units: List[str] = []
    for ch in word:
        if units and unicodedata.category(ch).startswith("M"):
            units[-1] += ch
        else:
            units.append(ch)
    return units


def _split_units(word: str) -> List[str]:
    if any(_is_burmese(c) for c in word):
        return _burmese_syllables(word)
    if len(word) > 14 and not word.isascii():
        return _clusters(word)
    return [word]


def _tokens(text: str) -> List[Tuple[str, str]]:
    toks: List[Tuple[str, str]] = []
    for w in text.split():
        units = _split_units(w)
        for i, u in enumerate(units):
            toks.append((u, " " if i == len(units) - 1 else ""))
    return toks


def wrap_lines(text: str, width: int) -> List[str]:
    lines, cur, cur_len = [], "", 0
    for u, joiner in _tokens(text):
        ulen = visible_len(u)
        if cur and cur_len + ulen > width:
            lines.append(cur.rstrip())
            cur, cur_len = "", 0
        cur += u + joiner
        cur_len += ulen + len(joiner)
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [text]


_SENT = re.compile(r"(?<=[!?…။။。！？])\s*|(?<=\.)\s+")
_CLAUSE = re.compile(r"(?<=[,;:၊，、；：])\s*")


def split_captions(text: str, max_chars: int = 70) -> List[str]:
    sentences: List[str] = []
    for block in re.split(r"\n+", text.strip()):
        for frag in _SENT.split(block):
            frag = frag.strip()
            if not frag:
                continue
            if sentences and not any(ch.isalnum() for ch in frag):
                sentences[-1] += frag
            else:
                sentences.append(frag)

    captions: List[str] = []
    for s in sentences:
        if visible_len(s) <= max_chars:
            captions.append(s)
            continue
        pieces = [p.strip() for p in _CLAUSE.split(s) if p.strip()]
        cur = ""
        for p in pieces:
            cand = (cur + " " + p).strip() if cur else p
            if visible_len(cand) <= max_chars:
                cur = cand
            else:
                if cur:
                    captions.append(cur)
                cur = p
                while visible_len(cur) > max_chars:
                    head, used, k = "", 0, 0
                    toks = _tokens(cur)
                    for k, (u, j) in enumerate(toks):
                        if used + visible_len(u) > max_chars and head:
                            break
                        head += u + j
                        used += visible_len(u) + len(j)
                    else:
                        k = len(toks)
                    if k < len(toks):   # စကားလုံးအလယ်မှာ မဖြတ်ဘဲ နောက်ဆုံး space (စကားလုံးအဆုံး) မှာ ဖြတ်ရန် ကြိုးစားသည်
                        b = k
                        while b > 0 and toks[b - 1][1] != " ":
                            b -= 1
                        if b > 0 and sum(visible_len(u) + len(j) for u, j in toks[:b]) >= max_chars * 0.4:
                            k = b
                    head = "".join(u + j for u, j in toks[:k])
                    rest = "".join(u + j for u, j in toks[k:]).strip()
                    captions.append(head.strip())
                    cur = rest
                    if not rest:
                        break
        if cur:
            captions.append(cur)
    return [c for c in captions if c.strip()]


def line_limit(text: str, per_line: int) -> int:
    """စာတန်းတစ်ကြောင်းတည်းဆန့်မည့် စာလုံးအရေအတွက် (မြန်မာစာဆိုလျှင် ၈၀%)။"""
    return max(8, int(per_line * 0.8) if any(_is_burmese(ch) for ch in text) else per_line)


def split_timed_caption(cap: dict, max_chars: int) -> List[dict]:
    parts = split_captions(cap["text"], max_chars)
    merged: List[str] = []
    for p in parts:   # အရမ်းတိုသော အပိုင်း (စက္ကန့်မပြည့် ပေါ်ပြီးပျောက်) ကို ရှေ့အပိုင်းနှင့် ပေါင်းသည်
        if merged and (visible_len(p) < max_chars * 0.3 or visible_len(merged[-1]) < max_chars * 0.3) \
                and visible_len(merged[-1] + " " + p) <= max_chars * 1.25:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    parts = merged
    if len(parts) <= 1:
        return [cap]
    weights = [max(1, visible_len(p)) for p in parts]
    total = float(sum(weights))
    out, t = [], cap["start"]
    for p, w in zip(parts, weights):
        d = (cap["end"] - cap["start"]) * w / total
        out.append({"start": round(t, 3), "end": round(t + d, 3), "text": p})
        t += d
    return out


# --------------------------------------------------------------------------- #
# အချိန်ဇယား: အသံကလစ် (unit) များကို အစဉ်လိုက်ဆက်သည်
# --------------------------------------------------------------------------- #
def assemble(
    clips: List[Path], unit_lines: List[List[str]], speed: float, work: Path, out_wav: Path
) -> Tuple[List[dict], float, List[float]]:
    """
    ကလစ်တစ်ခုစီ၏ အစ/အဆုံး တိတ်ဆိတ်ချိန်ကို ဖြတ်ပြီး စာကြောင်းကြား ကွာဟချက် အနည်းငယ်ပဲ ချန်သည်။
    ပြန်ပေးသည်: (captions, စုစုပေါင်းစက္ကန့်, unit တစ်ခုချင်းစီ၏ စတင်ချိန်များ)
    """
    lead, gap, tail = 0.12, 0.12 / max(speed, 0.5), 0.30
    pieces = [np.zeros(int(lead * SAMPLE_RATE), dtype=np.int16)]
    t = lead
    caps: List[dict] = []
    starts: List[float] = []
    for i, (clip, lines) in enumerate(zip(clips, unit_lines)):
        pcm = work / f"line_{i:04d}.wav"
        to_pcm(clip, pcm, speed)
        data = trim_silence(read_wav(pcm))
        dur = len(data) / SAMPLE_RATE
        starts.append(t)
        weights = [max(1, visible_len(x)) for x in lines]
        total_w = float(sum(weights))
        cursor = t
        for x, w in zip(lines, weights):
            d = dur * w / total_w
            caps.append({"start": round(cursor, 3), "end": round(cursor + d, 3), "text": x})
            cursor += d
        pieces.append(data)
        g = gap if i < len(clips) - 1 else tail
        pieces.append(np.zeros(int(g * SAMPLE_RATE), dtype=np.int16))
        t += dur + g
    write_wav(out_wav, np.concatenate(pieces))
    return caps, t, starts


def assemble_scenes(
    clips: List[Path], texts: List[str], scene_lens: List[float], speed: float, work: Path, out_wav: Path
) -> Tuple[List[dict], float, List[float]]:
    """ဇာတ်ကွက်အရှည်ကို မပြောင်း။ Voiceover ရှည်လျှင် အမြန်နှုန်းတင် (အများဆုံး 1.35x) ၊ ကျန်လျှင် fade ဖြင့် ဖြတ်။"""
    lead, tail, MAX_EXTRA = 0.15, 0.10, 1.35
    caps: List[dict] = []
    targets: List[float] = []
    pieces: List[np.ndarray] = []
    t = 0.0
    for i, (clip, text, scene_len) in enumerate(zip(clips, texts, scene_lens)):
        pcm = work / f"scene_voice_{i:04d}.wav"
        to_pcm(clip, pcm, speed)
        data = trim_silence(read_wav(pcm))
        avail = max(0.3, scene_len - lead - tail)
        dur = len(data) / SAMPLE_RATE
        if dur > avail:
            extra = min(MAX_EXTRA, dur / avail)
            to_pcm(clip, pcm, speed * extra)
            data = trim_silence(read_wav(pcm))
            n = int(avail * SAMPLE_RATE)
            if len(data) > n:
                data = data[:n].copy()
                f = min(len(data), int(0.12 * SAMPLE_RATE))
                data[-f:] = (data[-f:] * np.linspace(1.0, 0.0, f)).astype(np.int16)
            dur = len(data) / SAMPLE_RATE
        buf = np.zeros(int(round(scene_len * SAMPLE_RATE)), dtype=np.int16)
        off = int(lead * SAMPLE_RATE)
        buf[off:off + len(data)] = data[: max(0, len(buf) - off)]
        pieces.append(buf)
        caps.append({"start": round(t + lead, 3), "end": round(t + lead + dur, 3), "text": text})
        targets.append(scene_len)
        t += scene_len
    write_wav(out_wav, np.concatenate(pieces))
    return caps, t, targets


# --------------------------------------------------------------------------- #
# SRT / ASS
# --------------------------------------------------------------------------- #
def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(caps: List[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(caps, 1):
            f.write(f"{i}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n{c['text']}\n\n")


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _hex_rgb(value: str, default: str = "#FFFFFF") -> Tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
        value = default
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _ass_color(value: str, opacity: float = 1.0) -> str:
    r, g, b = _hex_rgb(value)
    alpha = int(round((1.0 - max(0.0, min(1.0, opacity))) * 255))
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def dimensions(aspect: str) -> Tuple[int, int]:
    return {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}.get(aspect, (1080, 1920))


def chars_per_line(font_size: int, width: int) -> int:
    return max(10, int((width * 0.86) / (font_size * 0.56)))


def build_ass(caps: List[dict], style: dict, width: int, height: int, path: Path, text_logo: dict = None) -> None:
    size = int(style.get("font_size", 56))
    mode = style.get("outline_mode", "outline")
    pos = style.get("position", "bottom")
    pos = pos if pos in ("bottom", "middle", "top") else "bottom"
    align = {"bottom": 2, "middle": 5, "top": 8}[pos]
    margin_v = {"bottom": int(height * 0.12), "middle": 0, "top": int(height * 0.10)}[pos]
    margin_h = int(width * 0.06)
    primary = _ass_color(style.get("color", "#FFFFFF"))
    ocolor = style.get("outline_color", "#000000")
    opacity = float(style.get("box_opacity", 1.0))

    border_style, outline, shadow = 1, 0, 0
    outline_col, back_col = _ass_color(ocolor), "&H64000000"
    if mode == "outline":
        outline = max(2, round(size * 0.07))
    elif mode == "box":
        border_style, outline = 3, max(8, round(size * 0.22))
        outline_col = _ass_color(ocolor, opacity)
    elif mode == "shadow":
        shadow, back_col = max(2, round(size * 0.06)), _ass_color(ocolor, 0.8)

    font = re.sub(r"[,\r\n]", " ", style.get("font_family") or "Liberation Sans")
    bold = -1 if style.get("bold", True) else 0
    per_line = chars_per_line(size, width)

    with open(path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\n")
        f.write(f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n")
        f.write("[V4+ Styles]\n")
        f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        f.write(f"Style: Default,{font},{size},{primary},&H000000FF,{outline_col},{back_col},{bold},0,0,0,100,100,0,0,"
                f"{border_style},{outline},{shadow},{align},{margin_h},{margin_h},{margin_v},1\n")

        # ---- Text Logo (စာသား logo) ----
        logo_line = ""
        if text_logo and text_logo.get("enabled") and (text_logo.get("text") or "").strip():
            lsize = int(text_logo.get("font_size", 48))
            lfont = re.sub(r"[,\r\n]", " ", text_logo.get("font_family") or font)
            lalign = {"top-left": 7, "top-center": 8, "top-right": 9,
                      "bottom-left": 1, "bottom-center": 2, "bottom-right": 3}.get(text_logo.get("position"), 7)
            lalpha = int(round((1 - max(0.0, min(1.0, float(text_logo.get("opacity", 1.0))))) * 255))
            lr, lg, lb = _hex_rgb(text_logo.get("color", "#FFFFFF"))
            lcol = f"&H{lalpha:02X}{lb:02X}{lg:02X}{lr:02X}"
            f.write(f"Style: Logo,{lfont},{lsize},{lcol},&H000000FF,&H{lalpha:02X}000000,&H00000000,-1,0,0,0,100,100,0,0,"
                    f"1,{max(2, round(lsize * 0.06))},0,{lalign},{int(width * 0.04)},{int(width * 0.04)},{int(height * 0.05)},1\n")
            ltxt = text_logo["text"].replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", "\\N")
            if text_logo.get("x") is not None and text_logo.get("y") is not None:
                ltxt = f"{{\\an5\\pos({int(float(text_logo['x']) * width)},{int(float(text_logo['y']) * height)})}}" + ltxt
            logo_line = f"Dialogue: 1,0:00:00.00,9:59:59.00,Logo,,0,0,0,,{ltxt}\n"

        f.write("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        for c in caps:
            clean = c["text"].replace("\\", "").replace("{", "(").replace("}", ")")
            limit = line_limit(clean, per_line)
            text = " ".join(clean.split())          # စာတန်းကို တစ်ကြောင်းတည်းသာ ပြသည် (\\N ဖြင့် မခွဲ)
            tags = ""
            if style.get("pos_x") is not None and style.get("pos_y") is not None:   # drag ဖြင့် စိတ်ကြိုက်နေရာ
                tags += f"\\an5\\pos({int(float(style['pos_x']) * width)},{int(float(style['pos_y']) * height)})"
            vl = visible_len(text)
            if vl > limit * 1.1:                    # မခွဲနိုင်သော စာလုံးရှည်ဆိုလျှင် စာလုံးကို အနည်းငယ်ချုံ့၍ တစ်ကြောင်းတည်းဖြစ်စေသည်
                pct = max(55, int(100 * limit * 1.1 / vl))
                tags += f"\\fscx{pct}\\fscy{pct}"
            if tags:
                text = "{" + tags + "}" + text
            f.write(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Default,,0,0,0,,{text}\n")
        f.write(logo_line)