"""
video_edit.py - FFmpeg ဖြင့် ဗီဒီယိုတပ်ဆင်ခြင်းနှင့် Rendering

အဆင့် A  render_clip   : ဇာတ်ကွက်တစ်ခုချင်းစီကို ဖြတ်ထုတ် + (AI မှ ရေစာ/စာတန်းထိုး ဖျောက်ထားသော frame များ)
                         + flip / zoom + အရွယ်အစားတပ် + Voiceover အချိန်နှင့် ကိုက်ညီအောင် Sync
အဆင့် B  concat_clips  : ကလစ်များကို တစ်ဆက်တည်းဖြစ်အောင် ဆက်သည်
အဆင့် C  compose       : Voiceover အသံ + 1.03x speed + အောက်ခြေအရိပ် + Logo + Live Shape + စာတန်းထိုး ထည့်၍ နောက်ဆုံး mp4 ထုတ်သည်
ဗီဒီယိုမတင်ထားလျှင် gradient နောက်ခံဖြင့် အသံ+စာတန်းထိုး+Shape ဗီဒီယို ထုတ်သည်။
"""
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

import numpy as np

from app.media import shapes
from app.core.services import atempo_chain, probe_video, run, run_ffmpeg

# ခဏရပ် + အနည်းငယ်မြန် (လုံးချင်းစီ: block စက္ကန့်, ရပ်မည့်စက္ကန့်)
# block တစ်ခုတွင် H စက္ကန့်ရပ်ပြီး ကျန်ကို (B-H)/B အဆမြန်စေသဖြင့် စုစုပေါင်းအချိန် မပြောင်း (အသံနှင့် မလွဲ)
RHYTHM = {"light": (6.0, 0.15), "medium": (4.0, 0.25), "strong": (3.0, 0.30)}
EDIT_SPEED = 1.03       # အမြန်နှုန်းပြောင်း (flip နှင့်အတူ)
ZOOM = 1.06             # အနည်းငယ် Zoom (၆%)
FPS = 30
MIN_SYNC, MAX_SYNC = 0.7, 1.5   # ဗီဒီယိုကလစ်ကို Voiceover နှင့် ကိုက်ရန် ပြောင်းလဲနိုင်သော အမြန်နှုန်းအပိုင်းအခြား

LOGO_POS = {
    "top-left": "20:20",
    "top-right": "W-w-20:20",
    "bottom-left": "20:H-h-20",
    "bottom-right": "W-w-20:H-h-20",
}


def edit_speed(has_video: bool, edit: dict) -> float:
    """Auto Edit ဖွင့်ပြီး flip/speed ရွေးထားမှ 1.03 ၊ မဟုတ်ရင် 1.0။"""
    return EDIT_SPEED if (has_video and edit.get("enabled") and edit.get("flip_speed")) else 1.0


def grade_filter(g: dict) -> str:
    """စိတ်ကြိုက် အလင်း/အမှောင်/အရောင်ညှိ filter chain (မလိုလျှင် '')။"""
    import math
    parts = []
    br = float(g.get("brightness", 0)) / 100.0
    ct = float(g.get("contrast", 100)) / 100.0
    sa = float(g.get("saturation", 100)) / 100.0
    if abs(br) > 0.001 or abs(ct - 1) > 0.001 or abs(sa - 1) > 0.001:
        parts.append(f"eq=brightness={br:.3f}:contrast={ct:.3f}:saturation={sa:.3f}")
    vg = float(g.get("vignette", 0)) / 100.0
    if vg > 0.01:   # ထောင့်လေးထောင့် မှောင်ညို့ခြင်း
        angle = math.pi / 2 - vg * (math.pi / 2 - math.pi / 6)
        parts.append(f"vignette=angle={angle:.4f}")
    op = float(g.get("tint_opacity", 0)) / 100.0
    if op > 0.005:  # အရောင်ခြုံခြင်း
        r, gg, b = shapes.hex_rgb(g.get("tint_color", "#FF8800"))
        parts.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=0x{r:02X}{gg:02X}{b:02X}@{op:.2f}:t=fill")
    return ",".join(parts)


# --------------------------------------------------------------------------- #
# အရိပ် (Bottom shade)
# --------------------------------------------------------------------------- #
def auto_shade_opacity(video: Optional[Path], duration: float) -> float:
    """
    Auto: ဗီဒီယိုအောက်ပိုင်း ကြည်လင်မှုကို frame ၅ ခု နမူနာယူတိုင်းပြီး အလင်းလွန်လျှင် အရိပ်ပိုမှောင်စေသည်။
    """
    if video is None:
        return 0.5
    vals: List[float] = []
    for k in range(5):
        t = duration * (k + 0.5) / 5
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=64:-2,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        if not p.stdout:
            continue
        # ပုံအမြင့်ကို ဖိုင်အရှည်ကနေ ပြန်တွက်သည် (အကျယ် ၆၄)
        arr = np.frombuffer(p.stdout, dtype=np.uint8)
        h = len(arr) // 64
        if h < 4:
            continue
        img = arr[: h * 64].reshape(h, 64)
        vals.append(float(img[int(h * 0.72):].mean()))
    if not vals:
        return 0.5
    luma = sum(vals) / len(vals)
    return round(min(0.8, max(0.35, 0.35 + 0.45 * luma / 255.0)), 2)


def make_shade_png(path: Path, width: int, band_h: int, color: str, opacity: float) -> None:
    """အောက်ခြေအရိပ် png (အပေါ်မှာ ဖောက်ထွင်း၊ အောက်မှာ ရွေးထားသောအရောင်) ကို ဖန်တီးသည်။"""
    r, g, b = shapes.hex_rgb(color, "#000000")
    alpha = int(round(max(0.0, min(1.0, opacity)) * 255))
    expr = f"min({alpha},{alpha}*pow(Y/{band_h},1.4))"
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=black:s={width}x{band_h},format=rgba,geq=r={r}:g={g}:b={b}:a='{expr}'",
         "-frames:v", "1", str(path)])


# --------------------------------------------------------------------------- #
# အဆင့် A: ဇာတ်ကွက်တစ်ခုချင်းစီ (clip)
# --------------------------------------------------------------------------- #
def _fit_graph(width: int, height: int, flip: bool, zoom: bool, from_file: bool) -> List[str]:
    """flip/zoom ပြုလုပ်ပြီး အရွယ်အစားတပ်သည့် filter graph ([0:v] မှ [fit] သို့)။ အလွတ်နေရာကို မှုန်ဝါးသော နောက်ခံဖြင့် ဖြည့်သည်။"""
    pre = []
    if from_file:
        pre.append(f"fps={FPS}")
    if flip:
        pre.append("hflip")
    if zoom:
        pre.append(f"crop=trunc(iw/{ZOOM}/2)*2:trunc(ih/{ZOOM}/2)*2")
    head = ",".join(pre) or "null"
    return [
        f"[0:v]{head},split=2[fa][fb]",
        f"[fa]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"scale={width // 8}:{height // 8},gblur=sigma=3,scale={width}:{height},eq=brightness=-0.12[bg]",
        f"[fb]scale={width}:{height}:force_original_aspect_ratio=decrease[fg]",
        "[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[fit]",
    ]


def _sync_filter(src_dur: float, target: float) -> str:
    """ကလစ်ကို Voiceover (target စက္ကန့်) နှင့် အတိအကျကိုက်အောင်: အမြန်နှုန်းပြောင်း → ကျန်လျှင် freeze → ပိုလျှင် ဖြတ်။"""
    speed = max(MIN_SYNC, min(MAX_SYNC, src_dur / target))
    new_len = src_dur / speed
    pad = max(0.0, target - new_len)
    return (f"setpts=PTS/{speed:.5f},tpad=stop_mode=clone:stop_duration={pad:.3f},"
            f"trim=duration={target:.3f},setpts=PTS-STARTPTS")


def _clip_cmd(from_file: bool, src: Optional[Path], start: float, src_dur: float, target: float,
              out: Path, width: int, height: int, flip: bool, zoom: bool,
              raw_size: Optional[Tuple[int, int]]) -> List[str]:
    cmd = ["ffmpeg", "-y"]
    if from_file:
        cmd += ["-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src)]
    else:
        pw, ph = raw_size  # type: ignore[misc]
        cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{pw}x{ph}", "-r", str(FPS), "-i", "-"]
    fc = _fit_graph(width, height, flip, zoom, from_file)
    fc[-1] = fc[-1].replace("[fit]", ",") .rstrip(",")  # [fit] label မလိုတော့ဘဲ sync filter ဆက်သည်
    fc[-1] += "," + _sync_filter(src_dur, target) + "[v]"
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-t", f"{target:.3f}", str(out)]
    return cmd


def render_clip_from_file(src: Path, start: float, src_dur: float, target: float, out: Path,
                          width: int, height: int, flip: bool, zoom: bool) -> None:
    """မူရင်းဗီဒီယိုမှ [start, start+src_dur] ကို ဖြတ်ပြီး target စက္ကန့်ဖြစ်အောင် Sync လုပ်ကာ out သို့ ထုတ်သည်။"""
    run_ffmpeg(_clip_cmd(True, src, start, src_dur, target, out, width, height, flip, zoom, None))


def render_clip_from_frames(frames: Iterable[np.ndarray], raw_size: Tuple[int, int], src_dur: float, target: float,
                            out: Path, width: int, height: int, flip: bool, zoom: bool) -> None:
    """AI ဖြင့် ရေစာ/စာတန်းထိုး ဖျောက်ပြီးသော frame များ (BGR) ကို pipe ဖြင့် ffmpeg သို့ ပို့၍ Sync လုပ်ကာ ထုတ်သည်။"""
    cmd = _clip_cmd(False, None, 0, src_dur, target, out, width, height, flip, zoom, raw_size)
    full = [cmd[0], "-hide_banner", "-loglevel", "error", *cmd[1:]]
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(full, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
    try:
        assert proc.stdin is not None
        for fr in frames:
            proc.stdin.write(fr.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    proc.wait()
    if proc.returncode != 0:
        err.seek(0)
        raise RuntimeError("ffmpeg failed:\n" + err.read().decode("utf-8", "replace")[-1500:])
    err.close()


# --------------------------------------------------------------------------- #
# အဆင့် B: ကလစ်များ ဆက်ခြင်း
# --------------------------------------------------------------------------- #
def concat_clips(clips: List[Path], out: Path, work: Path) -> None:
    """encode ပုံစံတူ ကလစ်များကို ပြန်မ encode ဘဲ (copy) ဆက်သည်။"""
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", str(out)], cwd=str(work))


# --------------------------------------------------------------------------- #
# အဆင့် C: နောက်ဆုံး ပေါင်းစပ်ခြင်း
# --------------------------------------------------------------------------- #
def compose(
    *,
    base: Optional[Path],
    prepared: bool,
    narration: Path,
    narration_dur: float,
    out: Path,
    width: int,
    height: int,
    edit: dict,
    logo: Optional[Path],
    logo_pos: str,
    subs: Optional[Path],
    viz: dict,
    shade: dict,
    ring_mov: Optional[Path],
    base_dir: Path,
    work: Path,
    grade: Optional[dict] = None,
    bg_audio: Optional[Path] = None,
    bg_volume: float = 0.15,
    music: Optional[Path] = None,
    music_volume: float = 0.2,
    music_duck: bool = True,
    logo_xy: Optional[Tuple[float, float]] = None,
    blur: Optional[dict] = None,
    on_progress: Optional[Callable[[float], None]] = None,
) -> float:
    """
    base       : မူရင်း/ပြင်ဆင်ပြီး ဗီဒီယို (None = ဗီဒီယိုမပါ၊ gradient နောက်ခံသုံး)
    prepared   : True ဆိုလျှင် base သည် အရွယ်အစားတပ်ပြီး (flip/zoom ပါပြီးသား) ဖြစ်သည်
    ပြန်ပေးသည်: နောက်ဆုံးဗီဒီယိုစက္ကန့်
    """
    has_video = base is not None
    speed = edit_speed(has_video, edit)
    on = bool(edit.get("enabled")) and has_video

    final_audio = narration_dur / speed
    if has_video:
        bw, bh, vdur = probe_video(base)
        final_video = vdur / speed
    else:
        final_video = final_audio
    final_dur = max(final_video, final_audio)
    pad = max(0.0, final_audio - final_video)   # narration ပိုရှည်လျှင် နောက်ဆုံး frame ကို ရပ်ထားသည်

    style = viz.get("style", "scope")
    viz_on = bool(viz.get("enabled"))
    bgname = viz.get("background", "none")

    # ---- အရိပ်ပုံ ကြိုဖန်တီးခြင်း ----
    shade_png: Optional[Path] = None
    band_h = int(height * 0.28) // 2 * 2
    if has_video and shade.get("enabled"):
        shade_png = work / "shade.png"
        make_shade_png(shade_png, width, band_h, shade.get("color", "#000000"), float(shade.get("opacity", 0.5)))

    def build(gradient: bool) -> List[str]:
        cmd: List[str] = ["ffmpeg", "-y"]
        fc: List[str] = []
        n = {"i": 0}

        def lab() -> str:
            n["i"] += 1
            return f"v{n['i']}"

        # ---------- input များ ----------
        if has_video:
            cmd += ["-i", str(base)]
        else:  # ဗီဒီယိုမပါ: ရွေးထားသော background (none = purple) ကို gradient အဖြစ်သုံး
            c0, c1 = shapes.GRADIENTS.get(bgname, shapes.GRADIENTS["purple"]) if bgname != "none" else shapes.GRADIENTS["purple"]
            if gradient:
                src = (f"gradients=s={width}x{height}:r={FPS}:c0=0x{c0}:c1=0x{c1}:x0=0:y0=0:x1={width}:y1={height}:"
                       f"nb_colors=2:speed=0.004")
            else:
                src = f"color=c=0x{c0}:s={width}x{height}:r={FPS}"
            cmd += ["-f", "lavfi", "-t", f"{final_dur:.3f}", "-i", src]
        cmd += ["-i", str(narration)]
        nxt = {"i": 2}

        def add_input(args: List[str]) -> int:
            cmd.extend(args)
            idx = nxt["i"]
            nxt["i"] += 1
            return idx

        shade_idx = add_input(["-i", str(shade_png)]) if shade_png is not None else None
        logo_idx = add_input(["-i", str(logo)]) if logo is not None else None
        bg_idx = add_input(["-i", str(bg_audio)]) if bg_audio is not None else None
        music_idx = add_input(["-stream_loop", "-1", "-i", str(music)]) if music is not None else None

        # shape အရွယ်အစားနှင့် panel
        sw, sh_ = shapes.shape_size(style, width, height)
        pad_px = max(16, int(0.15 * min(sw, sh_)))
        pw, ph = sw + 2 * pad_px, sh_ + 2 * pad_px
        panel_idx = None
        if viz_on and has_video and bgname != "none":
            if bgname == "dark":
                src = f"color=c=black@0.45:s={pw}x{ph},format=rgba"
            elif bgname == "light":
                src = f"color=c=white@0.30:s={pw}x{ph},format=rgba"
            else:
                c0, c1 = shapes.GRADIENTS[bgname]
                if gradient:
                    src = (f"gradients=s={pw}x{ph}:c0=0x{c0}:c1=0x{c1}:nb_colors=2:speed=0.004,format=rgba,"
                           f"colorchannelmixer=aa=0.62")
                else:
                    src = f"color=c=0x{c1}@0.62:s={pw}x{ph},format=rgba"
            panel_idx = add_input(["-f", "lavfi", "-t", f"{final_dur:.3f}", "-i", src])
        ring_idx = add_input(["-i", str(ring_mov)]) if (viz_on and style == "ring" and ring_mov is not None) else None

        cur = "0:v"

        def chain(filt: str) -> None:
            nonlocal cur
            o = lab()
            fc.append(f"[{cur}]{filt}[{o}]")
            cur = o

        # ---------- ဗီဒီယိုအပိုင်း ----------
        if has_video:
            if not prepared:
                # မူရင်းဗီဒီယိုကို တိုက်ရိုက်သုံးသည့်အခါ flip / zoom / အရွယ်အစားတပ်ခြင်းကို ဤနေရာတွင်လုပ်သည်
                g = _fit_graph(width, height, bool(on and edit.get("flip_speed")), bool(on and edit.get("zoom")), True)
                fc.extend(g)
                cur = "fit"
            if speed != 1.0:
                chain(f"setpts=PTS/{speed}")
            if on and edit.get("rhythm") in RHYTHM:   # ခဏရပ်ပြီး အနည်းငယ်မြန်သော လှုပ်ရှားမှု
                rb, rh = RHYTHM[edit["rhythm"]]
                k = (rb - rh) / rb
                chain(f"setpts='(floor(T/{rb})*{rb}+{rh}+(T-floor(T/{rb})*{rb})*{k:.5f})/TB',fps={FPS}")
            if pad > 0.05:
                chain(f"tpad=stop_mode=clone:stop_duration={pad:.3f}")

        if grade and has_video:
            gf = grade_filter(grade)
            if gf:
                chain(gf)

        # ---------- မူရင်းစာတန်း/ရေစာ မှုန်ဝါးခြင်း (အကွက်တစ်ခု) ----------
        if blur and blur.get("enabled") and has_video:
            bx = min(max(float(blur["x"]), 0.0), 0.98)
            by = min(max(float(blur["y"]), 0.0), 0.98)
            bw = min(max(float(blur["w"]), 0.02), 1.0 - bx)
            bh = min(max(float(blur["h"]), 0.02), 1.0 - by)
            sigma = 4 + int(blur.get("strength", 60)) * 0.36
            a, b2, c2, o = lab(), lab(), lab(), lab()
            fc.append(f"[{cur}]split[{a}][{b2}]")
            fc.append(f"[{b2}]crop=w=iw*{bw:.4f}:h=ih*{bh:.4f}:x=iw*{bx:.4f}:y=ih*{by:.4f},gblur=sigma={sigma:.1f}[{c2}]")
            fc.append(f"[{a}][{c2}]overlay=x=W*{bx:.4f}:y=H*{by:.4f}[{o}]")
            cur = o

        # ---------- အောက်ခြေအရိပ် ----------
        if shade_idx is not None:
            o = lab()
            fc.append(f"[{cur}][{shade_idx}:v]overlay=0:{height - band_h}:format=auto[{o}]")
            cur = o

        # ---------- Logo ----------
        if logo_idx is not None:
            lg = lab()
            fc.append(f"[{logo_idx}:v]scale={int(width * 0.15)}:-1,format=rgba[{lg}]")
            o = lab()
            if logo_xy is not None:   # drag ဖြင့် စိတ်ကြိုက်နေရာ (အလယ်မှတ်)
                pos_expr = f"x=W*{logo_xy[0]:.4f}-w/2:y=H*{logo_xy[1]:.4f}-h/2"
            else:
                pos_expr = LOGO_POS.get(logo_pos, LOGO_POS['top-right'])
            fc.append(f"[{cur}][{lg}]overlay={pos_expr}[{o}]")
            cur = o

        # ---------- အသံ + Live Shape ----------
        atempo = atempo_chain(speed) if speed != 1.0 else "anull"
        pos = viz.get("position", "middle")
        py = {"top": int(height * 0.08), "middle": (height - ph) // 2,
              "bottom": height - ph - int(height * 0.14)}.get(pos, (height - ph) // 2)
        py = max(0, py)
        px = (width - pw) // 2
        if viz_on and style == "ring" and ring_idx is not None:
            fc.append(f"[1:a]{atempo}[anar]")
            side = sw
            sp = f",setpts=PTS/{speed}" if speed != 1.0 else ""
            fc.append(f"[{ring_idx}:v]scale={side}:{side},format=rgba{sp}[viz]")
        elif viz_on:
            fc.append(f"[1:a]{atempo},asplit=2[anar][aviz]")
            fc.append(shapes.audio_filter(style, viz.get("color", "#00d4ff"), sw, sh_))
        else:
            fc.append(f"[1:a]{atempo}[anar]")

        vmix = "avo" if music_idx is not None else "aout"      # ဂီတပါလျှင် narration mix ကို [avo] သို့ အရင်ထုတ်
        if bg_idx is not None:   # Dubbing: မူရင်းအသံကို တိုးတိုးရောထည့်
            fc.append(f"[{bg_idx}:a]volume={bg_volume:.2f},{atempo}[abg]")
            fc.append(f"[anar][abg]amix=inputs=2:duration=first:dropout_transition=0,volume=2[{vmix}]")
        else:
            fc.append(f"[anar]anull[{vmix}]")
        if music_idx is not None:      # Background music: loop၊ fade၊ ဗီဒီယိုအရှည်အတိုင်းဖြတ်၊ (ရွေးလျှင်) စကားပြောစဉ် ducking
            fo = max(0.0, final_dur - 2.0)
            fc.append(f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,volume={music_volume:.3f},"
                      f"atrim=0:{final_dur:.3f},afade=t=in:st=0:d=1.5,afade=t=out:st={fo:.3f}:d=2[mus]")
            if music_duck:
                fc.append("[avo]asplit=2[vo1][vo2]")
                fc.append("[mus][vo2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[musd]")
                fc.append("[vo1][musd]amix=inputs=2:duration=first:dropout_transition=0,volume=2[aout]")
            else:
                fc.append("[avo][mus]amix=inputs=2:duration=first:dropout_transition=0,volume=2[aout]")
            
        if viz_on:
            if panel_idx is not None:
                o = lab()
                fc.append(f"[{cur}][{panel_idx}:v]overlay=x={px}:y={py}:format=auto[{o}]")
                cur = o
            o = lab()
            fc.append(f"[{cur}][viz]overlay=x={px + pad_px}:y={py + pad_px}:format=auto[{o}]")
            cur = o

        # ---------- စာတန်းထိုး (ASS) ----------
        if subs is not None:
            rel = Path(subs).relative_to(base_dir).as_posix()   # cwd = base_dir ဖြစ်၍ path တိုတိုသုံးနိုင်သည်
            o = lab()
            fc.append(f"[{cur}]ass={rel}:fontsdir=fonts[{o}]")
            cur = o

        fc.append(f"[{cur}]format=yuv420p[vout]")
        cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "superfast", "-crf", "23", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "192k", "-t", f"{final_dur:.3f}", "-movflags", "+faststart", str(out)]
        return cmd

    (base_dir / "fonts").mkdir(exist_ok=True)
    try:
        run_ffmpeg(build(True), total=final_dur, on_progress=on_progress, cwd=str(base_dir))
    except RuntimeError as first:
        try:  # ffmpeg အဟောင်းတွင် 'gradients' မရှိနိုင်၍ အရောင်ရိုးရိုးဖြင့် ပြန်စမ်းသည်
            run_ffmpeg(build(False), total=final_dur, on_progress=on_progress, cwd=str(base_dir))
        except RuntimeError:
            raise first
    return final_dur
