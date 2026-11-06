"""
services.py - အများသုံး helper function များ

  * FFmpeg ကို progress ပြသပြီး run ခြင်း
  * wav ဖတ်/ရေး၊ အသံအမြန်နှုန်းပြောင်း (atempo)
  * Edge-TTS တစ်ကြောင်းထုတ်ခြင်း
"""
import json
import shutil
import subprocess
import wave
from collections import deque
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

SAMPLE_RATE = 24000

# progress(အဆင့်နာမည်, ရာခိုင်နှုန်း 0-100, စာသား)
Progress = Callable[[str, float, str], None]


def noop_progress(step: str, pct: float, msg: str = "") -> None:
    """progress မလိုသောအခါ သုံးမည့် အလွတ် function။"""
    return None


# --------------------------------------------------------------------------- #
# FFmpeg / ffprobe
# --------------------------------------------------------------------------- #
def check_ffmpeg() -> None:
    """FFmpeg သွင်းထားခြင်း ရှိမရှိ စစ်သည်။"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe မတွေ့ပါ။ FFmpeg ကို install လုပ်ပြီး PATH ထဲ ထည့်ပါ။")


def run(cmd: List[str], cwd: Optional[str] = None) -> str:
    """command တစ်ခုကို run ပြီး မအောင်မြင်ရင် error ပစ်သည်။"""
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{p.stderr[-1500:]}")
    return p.stdout


# ffmpeg progress အတွက်သာဖြစ်သော စာကြောင်းများကို error ထဲ မထည့်ရန်
_PROGRESS_KEYS = ("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time", "dup_frames",
                  "drop_frames", "speed=", "progress=")


def run_ffmpeg(
    cmd: List[str],
    total: Optional[float] = None,
    on_progress: Optional[Callable[[float], None]] = None,
    cwd: Optional[str] = None,
) -> None:
    """ffmpeg ကို run ပြီး `-progress pipe:1` ဖြင့် တိုးတက်မှု (0..1) ကို callback ပေးသည်။"""
    full = [cmd[0], "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", *cmd[1:]]
    proc = subprocess.Popen(
        full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", cwd=cwd,
    )
    tail: deque = deque(maxlen=60)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            tail.append(line)
            if on_progress and total and (line.startswith("out_time_us=") or line.startswith("out_time_ms=")):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000.0
                    on_progress(max(0.0, min(seconds / total, 1.0)))
                except ValueError:
                    pass  # "N/A" ကဲ့သို့ တန်ဖိုးများကို ကျော်သည်
    except BaseException:
        proc.kill()          # Cancel (JobCancelled) လုပ်လျှင် ffmpeg ကို ဆက်မပြေးစေဘဲ ချက်ချင်းသတ်သည်
        proc.wait()
        raise
    proc.wait()
    if proc.returncode != 0:
        msg = "\n".join(l for l in tail if not l.startswith(_PROGRESS_KEYS))
        raise RuntimeError(f"ffmpeg failed:\n{msg[-1800:]}")


def probe_video(path: Path) -> Tuple[int, int, float]:
    """ဗီဒီယိုရဲ့ (အကျယ်, အမြင့်, စက္ကန့်) ကို ပြန်ပေးသည်။"""
    out = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    if not data.get("streams"):
        raise RuntimeError("ဤဖိုင်ထဲတွင် video stream မတွေ့ပါ။")
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])


# --------------------------------------------------------------------------- #
# အသံဖိုင် လုပ်ဆောင်ချက်
# --------------------------------------------------------------------------- #
def write_wav(path: Path, audio: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """numpy array ကို mono 16-bit wav အဖြစ် သိမ်းသည်။"""
    if audio.dtype != np.int16:
        audio = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio.tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def atempo_chain(speed: float) -> str:
    """ffmpeg 'atempo' က 0.5-2.0 အတွင်းသာ လက်ခံနိုင်သဖြင့် ကွင်းဆက်ပြီး သုံးသည်။"""
    parts = []
    s = float(speed)
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def to_pcm(src: Path, dst: Path, tempo: float = 1.0) -> None:
    """မည်သည့်အသံဖိုင်ကိုမဆို mono 24kHz wav သို့ ပြောင်းပြီး လိုအပ်ရင် အမြန်နှုန်းပြောင်းသည်။"""
    cmd = ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE)]
    if abs(tempo - 1.0) > 0.001:
        cmd += ["-filter:a", atempo_chain(tempo)]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    run(cmd)


# --------------------------------------------------------------------------- #
# Edge-TTS (တစ်ကြောင်းချင်း - concurrency/retry ကို tts_engines က စီမံသည်)
# --------------------------------------------------------------------------- #
async def edge_batch_one(text: str, voice: str, out: Path) -> Path:
    """စာသားတစ်ခုကို Edge-TTS ဖြင့် mp3 ထုတ်သည်။"""
    import edge_tts
    await edge_tts.Communicate(text, voice).save(str(out))
    return out
