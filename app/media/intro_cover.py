"""
intro_cover.py - video ရှေ့ဆုံးမှာ ထည့်မည့် Cover/Intro card (template + title)
frame တစ်ပုံကို နောက်ခံယူ -> အရောင်ညှိ -> ASS ဖြင့် title ရေး -> ရှေ့ကပေါင်းထည့်
"""
import os
import re
from pathlib import Path
from typing import Optional

from app.media import shapes
from app.media import tts_studio
from app.core.services import probe_video, run, run_ffmpeg

FPS = 30
TEMPLATES = {
    "cinema":    {"text": "#FFFFFF", "edge": "#000000", "box": False, "accent": "#FFD400", "dark": 0.25},
    "yellowbox": {"text": "#111111", "edge": "#FFD400", "box": True,  "accent": "#FFD400", "dark": 0.35},
    "redbox":    {"text": "#FFFFFF", "edge": "#E50914", "box": True,  "accent": "#E50914", "dark": 0.35},
    "neon":      {"text": "#00F0FF", "edge": "#FF2BD6", "box": False, "accent": "#FF2BD6", "dark": 0.30},
}


def _write_ass(path: Path, title: str, tagline: str, tpl: dict, font: str, size: int, w: int, h: int, dur: float,
               fade: bool = True):
    def clean(x: str) -> str:
        return x.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()

    def wrap(text: str, n: int) -> str:
        if any(tts_studio._is_burmese(c) for c in text):
            n = int(n * 0.8)
        return "\\N".join(tts_studio.wrap_lines(text, n))

    title, tagline = clean(title), clean(tagline)
    font = re.sub(r"[,\r\n]", " ", font or "Liberation Sans")
    per_line = tts_studio.chars_per_line(size, w)
    tsize = max(24, int(size * 0.5))
    mh = int(w * 0.06)
    if tpl["box"]:
        bs, outline = 3, max(10, round(size * 0.28))
    else:
        bs, outline = 1, max(3, round(size * 0.08))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n")
        f.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        f.write(f"Style: Title,{font},{size},{tts_studio._ass_color(tpl['text'])},&H000000FF,"
                f"{tts_studio._ass_color(tpl['edge'])},&H64000000,-1,0,0,0,100,100,0,0,{bs},{outline},0,5,{mh},{mh},0,1\n")
        f.write(f"Style: Tag,{font},{tsize},{tts_studio._ass_color(tpl['accent'])},&H000000FF,&H00000000,&H64000000,"
                f"-1,0,0,0,100,100,0,0,1,{max(2, round(tsize * 0.08))},0,8,{mh},{mh},{int(h * 0.10)},1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        end = tts_studio._ass_time(dur)
        fd = "{\\fad(300,250)}" if fade else ""       # Poster (ပုံတစ်ပုံ) မှာ fade မထည့် (မဟုတ်လျှင် စာမမြင်ရ)
        if title:
            f.write(f"Dialogue: 0,0:00:00.00,{end},Title,,0,0,0,,{fd}{wrap(title, per_line)}\n")
        if tagline:
            f.write(f"Dialogue: 0,0:00:00.00,{end},Tag,,0,0,0,,{fd}{wrap(tagline, per_line)}\n")


def add_cover(mp4: Path, poster: Path, cv: dict, width: int, height: int, work: Path, base_dir: Path) -> float:
    """
    Poster ပုံ (ခေါင်းစဉ်စာတန်းပါပြီးသား) ကို mp4 ၏ ရှေ့မှာ cover အဖြစ် ထည့်ပြီး mp4 နေရာမှာ အစားထိုးသည်။
    cover စက္ကန့်ကို ပြန်ပေးသည်။
    """
    dur = float(cv.get("seconds", 2.5))
    inp = ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}", "-i", str(poster)]
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,"
          f"fade=t=in:st=0:d=0.25,fade=t=out:st={max(0.1, dur - 0.25):.2f}:d=0.25,format=yuv420p")
    intro = work / "intro.mp4"
    run_ffmpeg(["ffmpeg", "-y", *inp, "-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", vf, "-c:v", "libx264", "-preset", "superfast", "-crf", "22", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "192k", "-shortest", str(intro)], cwd=str(base_dir))

    joined = work / "with_cover.mp4"
    fc = ("[0:v]setsar=1,fps=30,format=yuv420p[v0];[1:v]setsar=1,fps=30,format=yuv420p[v1];"
          "[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];"
          "[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a1];"
          "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    run_ffmpeg(["ffmpeg", "-y", "-i", str(intro), "-i", str(mp4), "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "superfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(joined)], cwd=str(base_dir))
    os.replace(joined, mp4)
    return dur


# --------------------------------------------------------------------------- #
# Poster / Cover ပုံ (jpg တစ်ပုံ) - ဗီဒီယိုထဲက အကြည်ဆုံး frame + ခေါင်းစဉ်
# --------------------------------------------------------------------------- #
def _pick_frame_time(src_video: Path, vdur: float) -> float:
    """frame ၈ ခုကို နမူနာယူပြီး ထက်မြက်ပြီး အလင်းအမှောင်မှန်သော frame ၏ အချိန်ကို ရွေးသည်။"""
    import subprocess
    import numpy as np
    best_t, best = vdur * 0.35, -1.0
    for k in range(1, 9):
        t = vdur * (0.08 + 0.84 * k / 9)
        try:
            p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src_video), "-frames:v", "1",
                                "-vf", "scale=160:-2,format=gray", "-f", "rawvideo", "-"],
                               capture_output=True, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        a = np.frombuffer(p.stdout or b"", dtype=np.uint8).astype(np.float32)
        h = len(a) // 160
        if h < 4:
            continue
        img = a[: h * 160].reshape(h, 160)
        sharp = float(np.abs(np.diff(img, axis=1)).mean() + np.abs(np.diff(img, axis=0)).mean())
        mean = float(img.mean())
        score = sharp * min(1.0, mean / 50.0) * (1.0 if mean < 215 else 0.5)   # မှောင်လွန်း/ဖြူလွန်း frame ကို လျှော့
        if score > best:
            best, best_t = score, t
    return best_t


def make_poster(out_jpg: Path, src_video: Optional[Path], title: str, cv: dict, width: int, height: int,
                work: Path, base_dir: Path) -> None:
    """Cover Intro နှင့် template တူသော Poster ပုံ (jpg) တစ်ပုံကို out_jpg သို့ ထုတ်သည်။"""
    tpl = TEMPLATES.get(cv.get("template"), TEMPLATES["yellowbox"])
    ass = work / "poster.ass"
    _write_ass(ass, title, cv.get("tagline", ""), tpl, cv.get("font_family", ""),
               int(cv.get("font_size", 90)), width, height, 5.0, fade=False)
    rel = ass.relative_to(base_dir).as_posix()
    if src_video is not None:
        _, _, vdur = probe_video(src_video)
        png = work / "poster_frame.png"
        run(["ffmpeg", "-y", "-ss", f"{_pick_frame_time(src_video, vdur):.2f}", "-i", str(src_video),
             "-frames:v", "1", str(png)])
        inp = ["-i", str(png)]
    else:
        inp = ["-f", "lavfi", "-i", f"color=c=0x{shapes.GRADIENTS['purple'][1]}:s={width}x{height}:r=1"]
    ac = "0x%02X%02X%02X" % shapes.hex_rgb(tpl["accent"])
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,"
          f"eq=brightness=-0.12:saturation=1.3,vignette=PI/4,"
          f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{tpl['dark']}:t=fill,"
          f"drawbox=x=0:y=ih*0.985:w=iw:h=ih*0.015:color={ac}:t=fill,"
          f"ass={rel}:fontsdir=fonts,format=yuvj420p")
    run_ffmpeg(["ffmpeg", "-y", *inp, "-vf", vf, "-frames:v", "1", "-q:v", "2", str(out_jpg)], cwd=str(base_dir))
