"""
text_cleaner.py - AI Subtitle & Watermark ဖယ်ရှားသည့်စနစ် (OpenCV + EasyOCR)

လူကိုယ်တိုင် Bounding Box (X, Y, Width, Height) မရွေးရပါ။ အလိုအလျောက် ရှာဖွေသည်:

  ၁) Watermark (ရေစာ): ဗီဒီယိုတစ်လျှောက် နေရာတူတူတွင် ကြာရှည်ပေါ်နေသော စာသားအကွက်ကို
     frame ~၂၀ ကို နမူနာယူစစ်ပြီး ရှာသည် (ဥပမာ channel နာမည်၊ @handle)
  ၂) Hard-coded subtitle: ဗီဒီယိုအောက်ပိုင်းတွင် အချိန်နှင့်အမျှ ပြောင်းလဲသော စာကြောင်းများကို
     စက္ကန့်တိုင်း (OCR_INTERVAL) frame တစ်ခုစစ်ပြီး ရှာသည်

ရှာတွေ့သော နေရာကိုသာ frame တစ်ခုချင်းစီအလိုက် Dynamic Masking + Gaussian Blur ဖြင့် ဖျောက်သည်။
(ဘောင်အစွန်းကို feather ပြုလုပ်သဖြင့် ဘေးပတ်ဝန်းကျင်နှင့် ချောမွေ့စွာ ဆက်စပ်ပြီး နောက်ခံဗီဒီယို မပျက်ပါ)

ကန့်သတ်ချက်: စာသားအမျိုးအစား watermark/subtitle ကိုသာ ရှာနိုင်သည် (ပုံသက်သက် logo ကို မတွေ့နိုင်ပါ)။
EasyOCR က မြန်မာစာကို မထောက်ပံ့ပါ - မူရင်းဗီဒီယိုထဲက စာသားဘာသာစကား (English, Chinese ...) ကို ရွေးရမည်။
"""
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np

from app.core import config

Box = Tuple[float, float, float, float]   # (x1, y1, x2, y2) - 0..1 အချိုး (ဗီဒီယိုအရွယ်အစားနှင့် မသက်ဆိုင်)

# UI မှ ရွေးသော မူရင်းစာသားဘာသာစကား → EasyOCR language list
OCR_LANGS: Dict[str, List[str]] = {
    "en": ["en"],
    "zh": ["ch_sim", "en"],
    "zh_tra": ["ch_tra", "en"],
    "ja": ["ja", "en"],
    "ko": ["ko", "en"],
    "th": ["th", "en"],
    "vi": ["vi", "en"],
    "hi": ["hi", "en"],
    "ru": ["ru", "en"],
    "ar": ["ar", "en"],
}

DET_WIDTH = 640              # စစ်ဆေးရာတွင် frame ကို ဒီအကျယ်သို့ ချုံ့သည် (မြန်စေရန်)
SUB_ZONE = 0.55               # ဗီဒီယိုအမြင့်၏ ၅၅% အောက်ပိုင်းကို စာတန်းထိုးနေရာအဖြစ် သတ်မှတ်သည်
STATIC_PRESENCE = 0.6         # နမူနာ frame ၆၀% ကျော်တွင် နေရာတူပေါ်လျှင် watermark ဟု သတ်မှတ်သည်
_readers: Dict[tuple, object] = {}


# --------------------------------------------------------------------------- #
# ရရှိနိုင်မှု စစ်ဆေးခြင်း
# --------------------------------------------------------------------------- #
def availability() -> Tuple[bool, str]:
    """OpenCV နှင့် EasyOCR သွင်းထားခြင်း ရှိမရှိ စစ်သည် (import မလုပ်ဘဲ)။"""
    import importlib.util
    missing = [n for n in ("cv2", "easyocr") if importlib.util.find_spec(n) is None]
    if missing:
        return False, "မသွင်းရသေးပါ: " + ", ".join(missing) + "  (pip install opencv-python-headless easyocr)"
    return True, ""


def _use_gpu() -> bool:
    mode = config.OCR_GPU.lower()
    if mode in ("true", "1", "yes"):
        return True
    if mode in ("false", "0", "no"):
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def _get_reader(langs: List[str]):
    key = tuple(langs)
    if key not in _readers:
        import easyocr
        gpu = _use_gpu()
        try:
            _readers[key] = easyocr.Reader(langs, gpu=gpu, verbose=False)  # ပထမအကြိမ်တွင် model download လုပ်သည်
        except Exception as e:  # noqa: BLE001
            # GPU/CUDA driver ပြဿနာဖြစ်လျှင် CPU ဖြင့် ပြန်စမ်းသည် (ဒေါင်းလုဒ်အမှားဆိုလျှင် မဖုံးဘဲ ပစ်သည်)
            if gpu and "cuda" in str(e).lower():
                _readers[key] = easyocr.Reader(langs, gpu=False, verbose=False)
            else:
                raise
    return _readers[key]


def prepare(langs: List[str], tries: int = 3) -> None:
    """
    EasyOCR model များကို အရင်ဆုံး ဒေါင်းလုဒ်/load လုပ်ထားသည် (ကြာရှည်သော အလုပ်များ မလုပ်မီ)။
    ကွန်ရက်ပြတ်၍ ဒေါင်းလုဒ် မပြည့်စုံလျှင် ယာယီဖိုင်ကို ဖျက်ပြီး ၃ ကြိမ်ထိ ပြန်ကြိုးစားသည်။
    """
    import time
    from pathlib import Path
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            _get_reader(langs)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            _readers.pop(tuple(langs), None)
            # မပြည့်စုံသော ယာယီဖိုင်ကျန်နေလျှင် ပြန်ဒေါင်းရာတွင် အမှားဖြစ်တတ်၍ ဖျက်သည်
            for name in ("temp.zip",):
                try:
                    (Path.home() / ".EasyOCR" / "model" / name).unlink()
                except OSError:
                    pass
            if i < tries - 1:
                time.sleep(3 * (i + 1))
    raise RuntimeError(
        f"EasyOCR model ကို ဒေါင်းလုဒ်မရပါ ({last})။ ကွန်ရက်ကောင်းသည့်အချိန် ပြန်စမ်းပါ၊ သို့မဟုတ် "
        "model ဖိုင်များကို လက်ဖြင့်ဒေါင်းပြီး သင့် user folder အောက်က .EasyOCR/model ထဲ unzip လုပ်ပါ (ဥပမာ C:/Users/နာမည်/.EasyOCR/model)။")


# --------------------------------------------------------------------------- #
# အကွက် (box) တွက်ချက်ရေး helper များ
# --------------------------------------------------------------------------- #
def _iou(a: Box, b: Box) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def merge_line_boxes(boxes: List[Box]) -> List[Box]:
    """တစ်ကြောင်းတည်းရှိ စကားလုံးအကွက်များကို စာကြောင်းတစ်ကြောင်းအကွက်အဖြစ် ပေါင်းသည်။"""
    boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    merged: List[Box] = []
    for b in boxes:
        for i, m in enumerate(merged):
            v_overlap = min(b[3], m[3]) - max(b[1], m[1])
            h_min = min(b[3] - b[1], m[3] - m[1])
            h_gap = max(b[0] - m[2], m[0] - b[2])
            if h_min > 0 and v_overlap / h_min > 0.4 and h_gap < 0.08:
                merged[i] = _union(m, b)
                break
        else:
            merged.append(b)
    return merged


def is_subtitle_like(b: Box) -> bool:
    """အောက်ပိုင်းတွင်ရှိပြီး အလျားရှည်၍ အလယ်ဝန်းကျင်ကျသော အကွက်ကို စာတန်းထိုးဟု သတ်မှတ်သည်။"""
    w, h = b[2] - b[0], b[3] - b[1]
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return cy >= SUB_ZONE and h > 0 and (w / h) >= 1.5 and w >= 0.05 and 0.12 <= cx <= 0.88


def _pad(b: Box, w: int, h: int) -> Box:
    """ဘေးပတ်လည် နည်းနည်းချဲ့ပေးသည် (စာလုံးအစွန်း မကျန်စေရန်)။"""
    bh = b[3] - b[1]
    px, py = 0.012, max(0.008, bh * 0.35)
    return (max(0.0, b[0] - px), max(0.0, b[1] - py), min(1.0, b[2] + px), min(1.0, b[3] + py))


# --------------------------------------------------------------------------- #
# စာသားအကွက် ရှာဖွေခြင်း (EasyOCR detector)
# --------------------------------------------------------------------------- #
def detect_boxes(reader, bgr: np.ndarray) -> List[Box]:
    """frame တစ်ခုတွင် စာသားရှိသော အကွက်များကို (0..1 အချိုးဖြင့်) ပြန်ပေးသည်။"""
    h, w = bgr.shape[:2]
    try:
        horizontal, free = reader.detect(bgr, min_size=12, text_threshold=0.6, low_text=0.35,
                                         link_threshold=0.4, canvas_size=DET_WIDTH, mag_ratio=1.0)
    except Exception:  # noqa: BLE001
        return []
    boxes: List[Box] = []
    for x1, x2, y1, y2 in (horizontal[0] if horizontal else []):
        boxes.append((x1 / w, y1 / h, x2 / w, y2 / h))
    for poly in (free[0] if free else []):
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        boxes.append((min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h))
    out = []
    for x1, y1, x2, y2 in boxes:
        x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(1.0, x2), min(1.0, y2)
        if x2 > x1 and y2 > y1 and (x2 - x1) * (y2 - y1) < 0.35:   # ဖရိန်တစ်ခုလုံးနီးပါး အကွက်ကို ပယ်သည်
            out.append((x1, y1, x2, y2))
    return out


def _det_size(src_w: int, src_h: int) -> Tuple[int, int]:
    h = int(round(src_h * DET_WIDTH / src_w / 2)) * 2
    return DET_WIDTH, max(2, h)


def _decode_frames(src, start: float, dur: float, fps: float, out_w: int, out_h: int) -> Iterator[np.ndarray]:
    """ffmpeg ဖြင့် frame များကို BGR raw အဖြစ် ဖတ်သည် (အချိန်တိကျစေရန်)။"""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
           "-vf", f"fps={fps:.4f},scale={out_w}:{out_h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = out_w * out_h * 3
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


# --------------------------------------------------------------------------- #
# Watermark (အမြဲတမ်း နေရာတူ) ရှာခြင်း
# --------------------------------------------------------------------------- #
def find_static_boxes(src, ranges: List[Tuple[float, float]], src_w: int, src_h: int, langs: List[str],
                      samples: int = 10, on_progress: Optional[Callable[[float], None]] = None) -> List[Box]:
    """
    ဗီဒီယိုထဲက အပိုင်းများ (ranges) တစ်လျှောက် frame ~samples ခုကို စစ်ပြီး နေရာတူတူ ထပ်ခါထပ်ခါပေါ်သော
    စာသားအကွက် (watermark) များကို ပြန်ပေးသည်။
    """
    reader = _get_reader(langs)
    total = sum(e - s for s, e in ranges)
    if total <= 0:
        return []
    times = []
    for k in range(samples):
        t = (k + 0.5) / samples * total
        for s, e in ranges:      # စုစုပေါင်းအချိန်ပေါ်က နေရာကို မူရင်းဗီဒီယိုအချိန်သို့ ပြန်ပြောင်းသည်
            if t <= e - s:
                times.append(s + t)
                break
            t -= e - s
    dw, dh = _det_size(src_w, src_h)
    per_sample: List[List[Box]] = []
    for i, t in enumerate(times):
        frame = next(_decode_frames(src, t, 0.5, 2.0, dw, dh), None)
        per_sample.append(detect_boxes(reader, frame) if frame is not None else [])
        if on_progress:
            on_progress((i + 1) / len(times))

    clusters: List[dict] = []   # {"box": Box, "hits": int}
    for boxes in per_sample:
        for b in boxes:
            for c in clusters:
                if _iou(c["box"], b) > 0.4:
                    c["box"] = _union(c["box"], b)
                    c["hits"] += 1
                    break
            else:
                clusters.append({"box": b, "hits": 1})
    need = max(2, int(len(per_sample) * STATIC_PRESENCE))
    return [_pad(c["box"], src_w, src_h) for c in clusters if c["hits"] >= need]


# --------------------------------------------------------------------------- #
# ကလစ်တစ်ခုအတွက် အစီအစဉ် (plan)
# --------------------------------------------------------------------------- #
@dataclass
class ClipPlan:
    interval: float
    static: List[Box] = field(default_factory=list)
    samples: List[List[Box]] = field(default_factory=list)   # နမူနာတစ်ခုချင်းစီ၏ စာတန်းထိုးအကွက်များ

    def boxes_at(self, t: float) -> List[Box]:
        """အချိန် t တွင် ဖျောက်ရမည့် အကွက်များ (အနီးဆုံးနမူနာကို ဆက်ထားသည်)။"""
        out = list(self.static)
        if self.samples:
            k = min(len(self.samples) - 1, max(0, int(round(t / self.interval))))
            out.extend(self.samples[k])
        return out


def plan_clip(src, start: float, dur: float, src_w: int, src_h: int, langs: List[str],
              static: List[Box], interval: Optional[float] = None) -> ClipPlan:
    """ကလစ်ထဲက စာတန်းထိုး (အချိန်နှင့်အမျှ ပြောင်းသော စာသား) နေရာများကို စက္ကန့်တိုင်း စစ်ဆေးသည်။"""
    interval = interval or config.OCR_INTERVAL
    reader = _get_reader(langs)
    dw, dh = _det_size(src_w, src_h)
    plan = ClipPlan(interval=interval, static=static)
    for frame in _decode_frames(src, start, dur, 1.0 / interval, dw, dh):
        boxes = [b for b in detect_boxes(reader, frame) if is_subtitle_like(b)]
        # watermark အဖြစ် ရှာပြီးသားနေရာနှင့် ထပ်နေလျှင် ထပ်မဖျောက်ရန် ဖယ်သည်
        boxes = [b for b in boxes if all(_iou(b, s) < 0.5 for s in static)]
        plan.samples.append([_pad(b, src_w, src_h) for b in merge_line_boxes(boxes)])
    return plan


# --------------------------------------------------------------------------- #
# Dynamic Masking + Gaussian Blur (OpenCV)
# --------------------------------------------------------------------------- #
_feather_cache: Dict[Tuple[int, int], np.ndarray] = {}


def _feather(h: int, w: int) -> np.ndarray:
    """အစွန်းများကို ချောမွေ့စွာ ပျောက်သွားစေမည့် mask (အလယ်=1၊ အစွန်း=0)။"""
    key = (h, w)
    if key not in _feather_cache:
        ramp = max(2, int(min(h, w) * 0.22))
        ys = np.minimum(np.arange(h), np.arange(h)[::-1]).astype(np.float32)
        xs = np.minimum(np.arange(w), np.arange(w)[::-1]).astype(np.float32)
        m = np.minimum(ys[:, None] / ramp, xs[None, :] / ramp)
        _feather_cache[key] = np.clip(m, 0.0, 1.0)[..., None]
    return _feather_cache[key]


def blur_boxes(frame: np.ndarray, boxes: List[Box]) -> np.ndarray:
    """frame ပေါ်ရှိ အကွက်များကိုသာ Gaussian Blur + feather mask ဖြင့် ဖျောက်သည် (ကျန်နောက်ခံ မထိပါ)။"""
    import cv2
    fh, fw = frame.shape[:2]
    for x1, y1, x2, y2 in boxes:
        px1, py1, px2, py2 = int(x1 * fw), int(y1 * fh), int(x2 * fw), int(y2 * fh)
        px1, py1, px2, py2 = max(0, px1), max(0, py1), min(fw, px2), min(fh, py2)
        w, h = px2 - px1, py2 - py1
        if w < 4 or h < 4:
            continue
        roi = frame[py1:py2, px1:px2]
        small = cv2.resize(roi, (max(1, w // 5), max(1, h // 5)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), sigmaX=max(2.0, small.shape[0] / 2.5))
        blurred = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        m = _feather(h, w)
        frame[py1:py2, px1:px2] = (roi * (1.0 - m) + blurred * m).astype(np.uint8)
    return frame


def cleaned_frames(src, start: float, dur: float, proc_w: int, proc_h: int, fps: int, plan: ClipPlan,
                   on_frame: Optional[Callable[[int], None]] = None) -> Iterator[np.ndarray]:
    """ကလစ်၏ frame များကို တစ်ခုချင်း ဖတ်၊ plan အရ အကွက်များကို ဖျောက်ပြီး ပြန်ထုတ်ပေးသည်။"""
    for i, frame in enumerate(_decode_frames(src, start, dur, float(fps), proc_w, proc_h)):
        frame = np.array(frame)               # ဖတ်ရန်သာ buffer ကို ကူးပြီး ပြင်လို့ရအောင် လုပ်သည်
        boxes = plan.boxes_at(i / fps)
        if boxes:
            frame = blur_boxes(frame, boxes)
        if on_frame:
            on_frame(i)
        yield frame
