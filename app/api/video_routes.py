"""
video_routes.py - ထုတ်ပြီးဗီဒီယိုစာရင်း၊ job အခြေအနေ/Cancel၊ ကိုယ်ပိုင်ဖိုင်များ ဒေါင်းလုဒ်/ကြည့်ရန် (login နှင့် ပိုင်ရှင်စစ်ဆေးပြီးမှသာ)
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import csrf_guard, current_user
from app.core import config, jobs, videos

router = APIRouter(dependencies=[Depends(csrf_guard)])


def _own_job(job_id: str, user: dict):
    job = jobs.get_job(job_id)
    if not job or job.owner != user["id"]:        # ကိုယ့် job မဟုတ်လျှင်လည်း "မတွေ့" ဟုသာ ပြန်
        raise HTTPException(404, "Job မတွေ့ပါ (server ပြန်စတင်ထားနိုင်သည်)။")
    return job


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str, user: dict = Depends(current_user)):
    return _own_job(job_id, user).to_dict()


@router.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str, user: dict = Depends(current_user)):
    job = _own_job(job_id, user)
    return {"ok": job.cancel(), "status": job.status}


@router.get("/api/videos")
def list_videos(user: dict = Depends(current_user)):
    return {"videos": videos.list_for(user["id"]), "usage": videos.usage(user["id"]),
            "retention_hours": config.RETENTION_HOURS}


@router.delete("/api/videos/{video_id}")
def delete_video(video_id: str, user: dict = Depends(current_user)):
    if not videos.delete(user["id"], video_id):
        raise HTTPException(404, "ဗီဒီယို မတွေ့ပါ။")
    return {"ok": True}


@router.get("/outputs/{uid}/{name}")
def get_output(uid: int, name: str, user: dict = Depends(current_user)):
    if uid != user["id"] or name != Path(name).name or name.startswith("."):
        raise HTTPException(404, "ဖိုင် မတွေ့ပါ။")
    path = videos.user_dir(uid) / name
    if not path.is_file():
        raise HTTPException(404, "ဖိုင် မတွေ့ပါ (၄၈ နာရီပြည့်၍ အလိုအလျောက် ဖျက်ထားနိုင်သည်)။")
    return FileResponse(path)
