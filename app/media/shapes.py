"""
shapes.py - Video Live Shape Visualizer ပုံစံများ

အသံနှင့်အတူ လှုပ်ရှားသော shape ပုံစံ ၉ မျိုး (FFmpeg filter များ + numpy ဖြင့်ဆွဲသော Pulse Ring):
  ring · wave · wave_line · bars · spectrum_line · dots · scope · scope_polar · volume
"""
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from app.core.services import SAMPLE_RATE, read_wav

STYLES = ("ring", "wave", "wave_line", "bars", "spectrum_line", "dots", "scope", "scope_polar", "volume")

# နောက်ခံ (background) အတွက် အရောင်များ  (gradient: အစ, အဆုံး)
GRADIENTS: Dict[str, Tuple[str, str]] = {
    "purple": ("1b1540", "4a0a52"),
    "blue": ("0b1d3a", "0a5f7a"),
    "sunset": ("2b1055", "d1457a"),
    "dark": ("0d0f14", "20242f"),
    "light": ("e8ebf2", "aab4c8"),
}
BACKGROUNDS = ("none", "dark", "light", "purple", "blue", "sunset")


def hex_rgb(value: str, default: str = "#00D4FF") -> Tuple[int, int, int]:
    import re
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
        value = default
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def shape_size(style: str, width: int, height: int) -> Tuple[int, int]:
    """ပုံစံတစ်ခုချင်းစီ၏ ပုံမှန်အရွယ်အစား (အကျယ်, အမြင့်)။"""
    portrait = height >= width
    wide_w = int(width * 0.86) // 2 * 2
    if style in ("scope", "scope_polar", "ring"):
        side = int(min(width, height) * (0.40 if portrait else 0.42)) // 2 * 2
        return side, side
    if style == "volume":
        return wide_w, (int(height * 0.07) if portrait else int(height * 0.10)) // 2 * 2
    return wide_w, (int(height * 0.16) if portrait else int(height * 0.28)) // 2 * 2


def audio_filter(style: str, color: str, w: int, h: int) -> str:
    """[aviz] အသံမှ [viz] ဗီဒီယိုအဖြစ် ပြောင်းမည့် filter chain ကို ပြန်ပေးသည် (ring မှလွဲ၍)။"""
    r, g, b = hex_rgb(color)
    col = "0x%02X%02X%02X" % (r, g, b)
    key = "format=rgba,colorkey=black:0.12:0.1"   # အနက်ရောင်နောက်ခံကို ဖောက်ထွင်းစေသည်
    mono = "aformat=channel_layouts=mono"
    if style == "wave":
        return f"[aviz]{mono},showwaves=s={w}x{h}:mode=cline:rate=30:colors={col}:scale=sqrt:draw=full,{key}[viz]"
    if style == "wave_line":
        return f"[aviz]{mono},showwaves=s={w}x{h}:mode=p2p:rate=30:colors={col}:scale=sqrt:draw=full,{key}[viz]"
    if style == "bars":
        return (f"[aviz]{mono},showfreqs=s={w // 2}x{h // 2}:mode=bar:fscale=log:ascale=sqrt:win_size=2048:"
                f"cmode=combined:colors={col}:rate=30,{key},scale={w}:{h}:flags=neighbor[viz]")
    if style == "spectrum_line":
        return (f"[aviz]{mono},showfreqs=s={w}x{h}:mode=line:fscale=log:ascale=sqrt:win_size=2048:"
                f"cmode=combined:colors={col}:rate=30,{key}[viz]")
    if style == "dots":
        return (f"[aviz]{mono},showfreqs=s={w // 2}x{h // 2}:mode=dot:fscale=log:ascale=sqrt:win_size=2048:"
                f"cmode=combined:colors={col}:rate=30,{key},scale={w}:{h}:flags=neighbor[viz]")
    if style in ("scope", "scope_polar"):
        mode = "lissajous" if style == "scope" else "polar"
        # mono အသံမှာ ဒေါင်လိုက်မျဉ်းတစ်ကြောင်းသာ ထွက်မည်ဖြစ်၍ ဘယ်/ညာကို အနည်းငယ်နောက်ကျစေသည်
        return (f"[aviz]pan=stereo|c0=c0|c1=c0,adelay=0|4,"
                f"avectorscope=s={w}x{h}:rate=30:mode={mode}:zoom=1.3:draw=line:scale=sqrt:"
                f"rc={r}:gc={g}:bc={b}:ac=255:rf=40:gf=40:bf=40:af=0,{key}[viz]")
    if style == "volume":
        return (f"[aviz]pan=stereo|c0=c0|c1=c0,showvolume=r=30:w={w}:h={max(8, h // 2)}:b=2:t=0:v=0:f=0.85:"
                f"c=0xff{b:02x}{g:02x}{r:02x},scale={w}:{h}:flags=neighbor,{key}[viz]")
    raise ValueError(f"Unknown shape style: {style}")


def render_ring(wav: Path, duration: float, out_mov: Path, color: str, size: int = 480, fps: int = 30,
                on_progress: Optional[Callable[[float], None]] = None) -> None:
    """
    အသံနှင့် တုံ့ပြန်သော စက်ဝိုင်းပုံ spectrum ("Pulse Ring") ကို numpy ဖြင့်ဆွဲပြီး ffmpeg ဆီ pipe ပို့သည်
    (ဖောက်ထွင်းနောက်ခံပါသော mov ထွက်သည်)။
    """
    r8, g8, b8 = hex_rgb(color)
    data = read_wav(wav).astype(np.float32)
    peak = float(np.max(np.abs(data))) if len(data) else 1.0
    data /= (peak or 1.0)
    win, bands = 2048, 64
    hann = np.hanning(win).astype(np.float32)

    yy, xx = np.mgrid[0:size, 0:size]
    cx = cy = (size - 1) / 2.0
    dx, dy = xx - cx, yy - cy
    rad = np.sqrt(dx * dx + dy * dy)
    ang = (np.arctan2(dy, dx) + np.pi) / (2 * np.pi)
    sym = np.abs(ang * 2 - 1) * bands             # ဘယ်/ညာ ပြန်လည်ထပ်တူ
    bidx = np.minimum(sym.astype(int), bands - 1)
    gap_mask = (sym - np.floor(sym)) < 0.68       # bar များအကြား အကွက်လပ်
    base, ext, ring_w = size * 0.20, size * 0.27, max(2.0, size * 0.012)
    inner = np.abs(rad - (base - 8)) < ring_w

    edges = np.geomspace(5, 700, bands + 1).astype(int)
    edges = np.maximum.accumulate(np.maximum(edges, np.arange(bands + 1) + 5))

    frames = int(math.ceil(duration * fps))
    err_file = tempfile.TemporaryFile()
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{size}x{size}", "-r", str(fps),
           "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", str(out_mov)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err_file)
    smooth = np.zeros(bands, dtype=np.float32)
    color_px = np.array([r8, g8, b8, 255], dtype=np.uint8)
    try:
        for i in range(frames):
            c = int(i / fps * SAMPLE_RATE)
            seg = data[max(0, c - win // 2): c + win // 2]
            if len(seg) < win:
                seg = np.pad(seg, (0, win - len(seg)))
            spec = np.abs(np.fft.rfft(seg * hann))
            amps = np.array([spec[edges[k]:max(edges[k] + 1, edges[k + 1])].mean() for k in range(bands)],
                            dtype=np.float32)
            amps = np.clip((amps / (win * 0.04)) ** 0.6, 0.0, 1.0)
            smooth = np.maximum(amps, smooth * 0.80)
            rms = float(np.sqrt(np.mean(seg * seg)))
            b0 = base * (1.0 + 0.10 * min(rms * 3, 1.0))
            outer = b0 + smooth[bidx] * ext
            mask = (rad >= b0) & (rad <= outer) & gap_mask
            mask |= inner
            frame = np.zeros((size, size, 4), dtype=np.uint8)
            frame[mask] = color_px
            proc.stdin.write(frame.tobytes())
            if on_progress and i % 15 == 0:
                on_progress(i / frames)
        proc.stdin.close()
        proc.wait()
    except BrokenPipeError:
        proc.wait()
    if proc.returncode != 0:
        err_file.seek(0)
        raise RuntimeError("Ring render failed:\n" + err_file.read().decode("utf-8", "replace")[-1200:])
    err_file.close()
