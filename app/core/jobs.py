"""
jobs.py - နောက်ကွယ်မှ လုပ်ငန်း (job) စီမံခြင်း - တစ်ကြိမ်လျှင် တစ်ခုသာ run သည်

browser က job တစ်ခုစတင်ပြီး GET /api/jobs/{id} ကို ထပ်ခါထပ်ခါ မေးကာ တိုးတက်မှုကို ပြသသည်။
  * Job တစ်ခုချင်းကို user (owner) နှင့် ချိတ်ထားသည်
  * Cancel: တန်းစီနေသော job ကို ချက်ချင်းဖျက်၊ run နေသော job ကို နောက် progress အဆင့်ရောက်သည့်အခါ ရပ်သည်
    (JobCancelled သည် BaseException ဖြစ်၍ `except Exception` များက မမိစေ)
"""
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import config

# CPU/GPU မလေးစေရန် worker တစ်ခုတည်း သုံးသည် (နောက် request များက တန်းစီစောင့်ရသည်)
_executor = ThreadPoolExecutor(max_workers=1)
JOBS: Dict[str, "Job"] = {}
_lock = threading.Lock()
MAX_JOBS = 200
ACTIVE = ("queued", "running")


class JobCancelled(BaseException):
    """အသုံးပြုသူက Cancel လုပ်သည့်အခါ progress callback ထဲမှ ပစ်သည် (Exception မဟုတ်ပါ)။"""


class Job:
    def __init__(self, kind: str, steps: List[Tuple[str, str]], owner: Optional[int] = None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.owner = owner
        self.steps = [{"key": k, "label": label, "status": "pending"} for k, label in steps]
        self.status = "queued"          # queued | running | done | error | cancelled
        self.progress = 0.0
        self.message = "Waiting in queue..."
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.cancel_requested = False
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None

    def update(self, step: str, pct: float, msg: str = "") -> None:
        """worker function များက ခေါ်မည့် progress callback။"""
        if self.cancel_requested:
            raise JobCancelled()
        if any(s["key"] == step for s in self.steps):   # စာရင်းထဲမရှိသော step key ဆိုလျှင် အဆင့်အခြေအနေ မပြောင်းပါ
            found = False
            for s in self.steps:
                if s["key"] == step:
                    s["status"] = "active"
                    found = True
                elif not found:
                    s["status"] = "done"
        self.progress = max(self.progress, min(float(pct), 99.0))
        if msg:
            self.message = msg

    def cancel(self) -> bool:
        """Cancel တောင်းဆိုသည်။ မပြီးသေးသော job ဖြစ်မှ true ။"""
        with _lock:
            if self.status not in ACTIVE:
                return False
            self.cancel_requested = True
            if self.status == "queued":          # မစရသေးခင်ဆိုလျှင် ချက်ချင်းပြီး
                self.status = "cancelled"
                self.message = "Cancelled"
                self.finished = time.time()
            else:
                self.message = "Cancelling..."
        return True

    def queue_position(self) -> int:
        """တန်းစီနေသော job များအနက် ဘယ်နှစ်ခုမြောက် (၁ = နောက်တစ်ခုစမည်) ။ မတန်းစီနေလျှင် 0။"""
        if self.status != "queued":
            return 0
        with _lock:
            waiting = sorted((j for j in JOBS.values() if j.status == "queued"), key=lambda j: j.created)
        return next((i + 1 for i, j in enumerate(waiting) if j.id == self.id), 0)

    def to_dict(self) -> dict:
        end = self.finished or time.time()
        start = self.started or self.created
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 1),
            "message": self.message,
            "steps": self.steps,
            "elapsed": round(end - start, 1),
            "queue_position": self.queue_position(),
            "result": self.result,
            "error": self.error,
        }


def create_job(kind: str, steps: List[Tuple[str, str]], owner: Optional[int] = None) -> Job:
    job = Job(kind, steps, owner)
    with _lock:
        JOBS[job.id] = job
        if len(JOBS) > MAX_JOBS:  # ပြီးသွားသော အဟောင်းများကို ဖယ်သည်
            finished = sorted((j for j in JOBS.values() if j.status not in ACTIVE), key=lambda j: j.created)
            for old in finished[: len(JOBS) - MAX_JOBS]:
                JOBS.pop(old.id, None)
    return job


def get_job(job_id: str) -> Optional[Job]:
    return JOBS.get(job_id)


def active_count(owner: Optional[int]) -> int:
    """user တစ်ဦး၏ မပြီးသေးသော (တန်းစီ/run နေသော) job အရေအတွက်။"""
    with _lock:
        return sum(1 for j in JOBS.values() if j.owner == owner and j.status in ACTIVE)


def submit(job: Job, fn: Callable[[Callable[[str, float, str], None]], Any],
           cleanup: Optional[Callable[[], None]] = None,
           on_done: Optional[Callable[["Job"], None]] = None) -> None:
    """fn(progress_callback) ကို နောက်ကွယ်မှ run သည်။ fn က ရလဒ် dict ကို ပြန်ပေးရမည်။ on_done(job) သည် အောင်မြင်မှသာ ခေါ်သည်။"""

    def worker() -> None:
        try:
            if job.status == "cancelled":      # တန်းစီစဉ်က Cancel လုပ်ထားလျှင် မ run ပါ
                return
            job.status = "running"
            job.started = time.time()
            job.message = "Starting..."
            try:
                job.result = fn(job.update)
                for s in job.steps:
                    s["status"] = "done"
                job.progress = 100.0
                job.message = "Finished"
                job.status = "done"
                if on_done:
                    try:
                        on_done(job)
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
            except JobCancelled:
                job.status = "cancelled"
                job.message = "Cancelled"
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                # error စာသားထဲ API key ပါလာလျှင် ဖျက်ပြီးမှ browser သို့ ပို့သည်
                job.error = config.scrub(str(e) or e.__class__.__name__)
                job.status = "error"
                job.message = "Failed"
        finally:
            job.finished = job.finished or time.time()
            if cleanup:
                try:
                    cleanup()
                except Exception:  # noqa: BLE001
                    pass

    _executor.submit(worker)
