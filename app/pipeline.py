"""
pipeline.py - Movie Recap + Text to Speech ပေါင်းစပ်ထားသော အဓိက လုပ်ငန်းစဉ်

Voiceover Script OFF (Auto Movie Recap):
  [ရုပ်ရှင်ဗီဒီယို] → Whisper (Exact Transcription + SRT)
      → Gemini (Recap Narrator Script ပြန်ရေး + Highlight scene timecode ရွေး)
      → TTS (Voiceover)
      → [AI ဖြင့် ရေစာ/စာတန်းထိုး ဖျောက်] → (highlight mode သာ) ဇာတ်ကွက်ဖြတ်ထုတ် + Sync → ပေါင်းစပ် Render
Voiceover Script ON:
  အသုံးပြုသူ၏ script ကို အသံထုတ် → (ဗီဒီယိုရှိလျှင်) မူရင်းဗီဒီယိုပေါ် တင်သည်
"""
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.core import config
from app.ai import gemini_service
from app.media import shapes
from app.ai import text_cleaner
from app.ai import transcribe
from app.ai import tts_engines
from app.ai import music_service
from app.media import tts_studio
from app.media import intro_cover
from app.media import video_edit
from app.core.services import Progress, atempo_chain, check_ffmpeg, noop_progress, probe_video, run


def _span(a: Tuple[float, float], f: float) -> float:
    return a[0] + (a[1] - a[0]) * f


def _synth(texts: List[str], engine: str, voice: str, language: str, work: Path, cb, warnings: List[str],
           allow_fallback: bool) -> List[Path]:
    """
    အသံထုတ်သည်။ Google engine (gTTS/Gemini/Cloud) မှာ ပိတ်ပင်ခံရလျှင် (403, location, quota)
    allow_fallback ဖြစ်ပါက Edge-TTS ဖြင့် အလိုအလျောက် အစားထိုးပြီး သတိပေးချက်ထည့်သည်။
    """
    t0 = time.time()
    try:
        out = tts_engines.synth_texts(texts, engine, voice, language, work, cb)
        print(f"[TTS] {engine}: {len(texts)} clip(s) in {time.time() - t0:.1f}s", flush=True)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[TTS] {engine} failed after {time.time() - t0:.1f}s: {config.scrub(e)}", flush=True)
        if engine == "edge" or not allow_fallback:
            raise
        vid, loc = tts_engines.pick_edge_voice(language)
        warnings.append(f"{engine} TTS မအောင်မြင်ပါ ({config.scrub(e)}) - Edge-TTS အသံ ({vid}) ဖြင့် အစားထိုးထားသည်။")
        return tts_engines.synth_texts(texts, "edge", vid, loc, work, cb)


def _resolve_voice(s: dict, ref_path: Optional[Path], consent_path: Optional[Path], work: Path,
                   progress: Progress, step: str, plan: Tuple[float, float]) -> Tuple[str, str, bool]:
    """(engine, voice, cloned) ကို ပြန်ပေးသည်။ Voice Clone ON ဆိုလျှင် Gemini Voice Replication ဖြင့် အသံပုံစံ ဖန်တီးသည်။"""
    if not s["voice_clone"]:
        return s["engine"], s["voice"], False
    progress(step, plan[0], "Replicating your voice (Gemini Voice Replication)...")
    ref = tts_engines.prepare_wav24(ref_path, work / "ref24.wav", max_seconds=30)
    consent = tts_engines.prepare_wav24(consent_path, work / "consent24.wav")
    key = tts_engines.replicate_voice(ref, consent)
    return "gemini", key, True


def _shade_settings(s: dict, video: Optional[Path], dur: float) -> dict:
    """အောက်ခြေအရိပ်: Auto = ဗီဒီယိုအလင်းအမှောင်ကြည့်၍ ရွေး၊ Custom = အသုံးပြုသူ ရာခိုင်နှုန်း။"""
    sh = s["shade"]
    if not sh["enabled"] or video is None:
        return {"enabled": False}
    opacity = video_edit.auto_shade_opacity(video, dur) if sh["mode"] == "auto" else sh["opacity"] / 100.0
    return {"enabled": True, "color": sh["color"], "opacity": opacity}


def _clean_enabled(s: dict, warnings: List[str]) -> bool:
    """AI ရေစာ/စာတန်းထိုး ဖျောက်ခြင်း ဖွင့်ထားပြီး OpenCV + EasyOCR ရှိမှ true။"""
    ae = s["auto_edit"]
    if not (ae["enabled"] and ae["remove_text"]):
        return False
    ok, why = text_cleaner.availability()
    if not ok:
        warnings.append("AI ရေစာ/စာတန်းထိုး ဖယ်ရှားခြင်းကို ကျော်သွားသည် - " + why)
    return ok


def _make_plan_runner(video: Path, sw: int, sh: int, langs: List[str], ranges: List[Tuple[float, float]],
                      progress: Progress, step: str, span: Tuple[float, float]):
    """Watermark ကို ဗီဒီယိုတစ်လျှောက် ကြိုရှာပြီး ကလစ်တစ်ခုချင်းစီအတွက် plan ပြုလုပ်ပေးမည့် function ကို ပြန်ပေးသည်။"""
    progress(step, span[0], "AI is looking for watermarks...")
    static = text_cleaner.find_static_boxes(
        video, ranges, sw, sh, langs,
        on_progress=lambda f: progress(step, _span(span, f * 0.1), "AI is looking for watermarks..."))
    return static


def _refit_scenes(scenes, texts, clips, engine, voice, language, work, warnings, allow_fallback,
                  speed, lang_label, src_lang, progress, span) -> None:
    """TTS အသံအရှည်ကို တိုင်းပြီး ဇာတ်ကွက်အရှည်နှင့် မကိုက်သော narration များကို Gemini ဖြင့် ပြန်ညှိ + ထပ်ထုတ်သည်။"""
    for rnd in range(2 if engine == "edge" else 1):
        bad = []
        for i, sc in enumerate(scenes):
            target = max(1.0, sc["end"] - sc["start"] - 0.45)
            cur = tts_studio.voice_seconds(clips[i], work, f"r{rnd}_{i}", speed)
            if cur <= 0:
                continue
            ratio = cur / target
            if ratio < 0.72 or ratio > 1.08:
                bad.append({"id": i, "narration": texts[i], "seconds": round(target, 1),
                            "current_seconds": round(cur, 1)})
        if not bad:
            return
        progress("voice", _span(span, 0.5 + 0.25 * rnd), f"Fitting narration to scene length ({len(bad)} scenes)...")
        print(f"[Refit] round {rnd + 1}: {len(bad)} scene(s) need fitting", flush=True)
        try:
            new = gemini_service.refit(bad, lang_label, src_lang)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Narration အရှည်ပြန်ညှိခြင်း မအောင်မြင်ပါ ({config.scrub(e)}) - မူလ narration ဖြင့် ဆက်လုပ်သည်။")
            print(f"[Refit] Gemini failed: {config.scrub(e)}", flush=True)
            return
        idx = [b["id"] for b in bad if new.get(b["id"])]
        if not idx:
            return
        sub = work / f"refit{rnd}"
        sub.mkdir(exist_ok=True)
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_synth, [new[i] for i in idx], engine, voice, language, sub, None, warnings, allow_fallback)
        try:
            new_clips = fut.result(timeout=150)      # TTS မဖြေလျှင် ၁၅၀ စက္ကန့်အထိသာ စောင့်
        except Exception as e:  # noqa: BLE001
            ex.shutdown(wait=False)
            warnings.append(f"ပြန်ညှိထားသော narration ကို အသံမထုတ်နိုင်ပါ ({config.scrub(e) or 'timeout'}) - မူလ narration ဖြင့် ဆက်လုပ်သည်။")
            print(f"[Refit] TTS failed/timeout: {config.scrub(e) or 'timeout'}", flush=True)
            return
        ex.shutdown(wait=False)
        for i, c in zip(idx, new_clips):
            clips[i] = c
            texts[i] = new[i]
            scenes[i]["text"] = new[i]


def run_pipeline(
    s: dict,
    video_path: Optional[Path],
    ref_path: Optional[Path],
    consent_path: Optional[Path],
    logo_path: Optional[Path],
    work: Path,
    out_dir: Path,
    base_dir: Path,
    job_id: str,
    progress: Progress = noop_progress,
    url_base: str = "/outputs",
) -> dict:
    check_ffmpeg()
    warnings: List[str] = []
    post_lang = s.get("language_label", "")
    width, height = tts_studio.dimensions(s["aspect_ratio"])
    sub, edit, viz = s["subtitle"], s["auto_edit"], s["viz"]
    if viz["style"] == "none":                 # Shape style = None ဆိုလျှင် shape၊ panel၊ ring မဆွဲ
        viz = dict(viz, enabled=False)
    auto_mode = not s["script_on"]
    has_video = video_path is not None
    speed_tts = float(s["speed"])
    per_line = tts_studio.chars_per_line(int(sub["font_size"]), width)
    flip = bool(edit["enabled"] and edit["flip_speed"] and has_video)
    zoom = bool(edit["enabled"] and edit["zoom"] and has_video)
    clean = has_video and _clean_enabled(s, warnings)
    langs = text_cleaner.OCR_LANGS.get(edit.get("ocr_lang", "en"), ["en"])
    if clean:
        # Whisper ကဲ့သို့ ကြာသောအလုပ်မလုပ်မီ OCR model ကို အရင်ဒေါင်းထားသည်။
        # မရလျှင် Job တစ်ခုလုံး မပျက်စေဘဲ AI ဖယ်ရှားခြင်းကိုသာ ကျော်ပြီး သတိပေးသည်
        progress("whisper" if not s["script_on"] else "voice", 0, "Preparing the AI text-removal model...")
        try:
            text_cleaner.prepare(langs)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"AI ရေစာ/စာတန်းထိုး ဖယ်ရှားခြင်းကို ကျော်သွားသည် - {config.scrub(e)}")
            clean = False

    narration = work / "narration.wav"
    transcript_srt_url = None
    base_video: Optional[Path] = None
    prepared = False
    
    bg_audio: Optional[Path] = None
    dub = (s.get("mode") == "dub") and not s["script_on"]
    sub_only = (s.get("mode") == "sub") and not s["script_on"]     # Subtitle only: စာတန်းသာထည့်၊ မူရင်းအသံအတိုင်း

    # ======================================================================= #
    # Subtitle only : Whisper -> (ဘာသာပြန်) -> မူရင်းအသံ + စာတန်းထိုး
    # ======================================================================= #
    if sub_only:
        if not has_video:
            raise ValueError("Subtitle only အတွက် ဗီဒီယိုဖိုင် လိုအပ်သည်။")
        p_wh, p_gm, p_cl = (0, 45), (45, 55), (55, 85)
        p_render = (85, 100)

        progress("whisper", 0, "Whisper: speech to text...")
        segments, detected, _ = transcribe.transcribe(
            video_path, work, lambda f, m: progress("whisper", _span(p_wh, f), m))
        if not segments:
            raise ValueError("ဗီဒီယိုထဲတွင် စကားပြောသံ မတွေ့ပါ။")
        t_srt = out_dir / f"transcript_{job_id}.srt"
        tts_studio.write_srt(segments, t_srt)       # မူရင်းစကားအတိုင်း (Exact) SRT
        transcript_srt_url = f"{url_base}/{t_srt.name}"
        sw, sh, vdur = probe_video(video_path)

        progress("gemini", p_gm[0], "Gemini: translating subtitles...")
        cap_src = segments
        if s.get("sub_translate", True) and (s.get("language_label") or "").strip():
            try:
                cap_src = gemini_service.translate_segments(
                    segments, s["language_label"], detected,
                    lambda f, m: progress("gemini", _span(p_gm, f), m), merge=False)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"ဘာသာပြန်၍မရပါ ({config.scrub(e)}) - မူရင်းစကားအတိုင်း စာတန်းထိုးထားသည်။")
                cap_src = segments
        unit_caps = [{"start": x["start"], "end": x["end"], "text": x["text"]} for x in cap_src]
        if not (s.get("sub_translate", True) and (s.get("language_label") or "").strip()):
            post_lang = tts_engines.LANG_NAMES.get((detected or "en").split("-")[0], "English")   # ဘာသာမပြန်လျှင် မူရင်းဘာသာဖြင့် ရေး
        # မူရင်းအသံကို အသံလမ်းကြောင်းအဖြစ် သုံးသည် (အသံမရှိလျှင် တိတ်ဆိတ်အသံ)
        ring = viz["enabled"] and viz["style"] == "ring"
        ac = ["-ac", "1", "-ar", "24000"] if ring else ["-ac", "2", "-ar", "48000"]
        try:
            run(["ffmpeg", "-y", "-i", str(video_path), "-vn", *ac, "-c:a", "pcm_s16le", str(narration)])
        except RuntimeError:
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{vdur:.2f}",
                 "-c:a", "pcm_s16le", str(narration)])
        narration_dur = vdur

        if clean:
            progress("clips", p_cl[0], "AI is looking for watermarks...")
            static = _make_plan_runner(video_path, sw, sh, langs, [(0.0, vdur)], progress, "clips", p_cl)
            plan = text_cleaner.plan_clip(video_path, 0.0, vdur, sw, sh, langs, static)
            pw = min(sw, 960) // 2 * 2
            ph = int(round(sh * pw / sw / 2)) * 2
            total_frames = max(1, int(vdur * video_edit.FPS))
            frames = text_cleaner.cleaned_frames(
                video_path, 0.0, vdur, pw, ph, video_edit.FPS, plan,
                on_frame=lambda i: progress("clips", _span(p_cl, 0.1 + 0.9 * min(1.0, i / total_frames)),
                                            f"Cleaning frames... {int(min(1.0, i / total_frames) * 100)}%")
                if i % 30 == 0 else None)
            base_video = work / "cleaned_base.mp4"
            video_edit.render_clip_from_frames(frames, (pw, ph), vdur, vdur, base_video, width, height, flip, zoom)
            prepared = True
        else:
            base_video, prepared = video_path, False
        shade_src, shade_dur = video_path, vdur

    # ======================================================================= #
    # Voiceover Script OFF : Auto Movie Recap
    # ======================================================================= #
    elif auto_mode:
        if not has_video:
            raise ValueError("Auto Movie Recap အတွက် ဗီဒီယိုဖိုင် လိုအပ်သည်။")
        p_wh, p_gm, p_vo, p_cl = (0, 30), (30, 40), (40, 60), (60, 85)
        p_render = (85, 100)

        # ---- ၁) Whisper: Exact Transcription + SRT ----
        progress("whisper", 0, "Whisper: speech to text...")
        segments, detected, _ = transcribe.transcribe(
            video_path, work, lambda f, m: progress("whisper", _span(p_wh, f), m))
        if not segments:
            raise ValueError("ဗီဒီယိုထဲတွင် စကားပြောသံ မတွေ့ပါ။ Voiceover Script ကို ON လုပ်ပြီး စာသားထည့်ပါ။")
        t_srt = out_dir / f"transcript_{job_id}.srt"
        tts_studio.write_srt(segments, t_srt)       # မူရင်းစကားအတိုင်း (Exact) SRT
        transcript_srt_url = f"{url_base}/{t_srt.name}"
        sw, sh, vdur = probe_video(video_path)

        # ---- ၂) Gemini: Recap Narrator Script + Highlight timecodes ----
        progress("gemini", p_gm[0], "Gemini: writing the recap script...")
        engine, voice, cloned = s["engine"], s["voice"], False
        language = s["language"]
        gemini_ok = True
        try:
            if dub:
                scenes = gemini_service.translate_segments(
                    segments, s["language_label"], detected,
                    lambda f, m: progress("gemini", _span(p_gm, f), m))
            else:
                scenes = gemini_service.write_recap(
                    segments, vdur, s["language_label"], s["recap_length"],
                    lambda f, m: progress("gemini", _span(p_gm, f), m),
                    video=video_path, source_lang=detected)
                
            if not scenes:
                raise RuntimeError("Gemini က scene တစ်ခုမျှ မရွေးပေးပါ။")
        except Exception as e:  # noqa: BLE001
            gemini_ok = False
            # Gemini မရလျှင် (ဥပမာ ဒေသပိတ်ပင်မှု) မူရင်းစကားအတိုင်း ပြန်ပြောသော ဗီဒီယို ဆက်ထုတ်ပေးသည်
            warnings.append(f"Gemini ကို မသုံးနိုင်ပါ ({config.scrub(e)}) - မူရင်းစာသားအတိုင်း (ဘာသာမပြန်၊ ပြန်မရေးဘဲ) ထုတ်ထားသည်။")
            scenes = [dict(x) for x in segments]
            engine = "edge"
            voice, language = tts_engines.pick_edge_voice(detected)
            s = dict(s, voice_clone=False)

        # ---- ၃) TTS: Voiceover ----
        progress("voice", p_vo[0], "Generating voiceover...")
        engine, voice, cloned = _resolve_voice(s, ref_path, consent_path, work, progress, "voice", p_vo) \
            if s["voice_clone"] else (engine, voice, False)
        original = s["recap_length"] == "original" or dub     # Recap length ခလုတ် OFF = မူလဗီဒီယိုအရှည်အတိုင်း
        if original:   # Gemini အောင်မြင်/မအောင်မြင် (fallback) နှစ်မျိုးလုံးတွင် ဗီဒီယိုတစ်ခုလုံး မကျော်ဘဲ ဖုံးစေသည်
            scenes = gemini_service.make_contiguous(scenes, vdur)
        texts = [x["text"] for x in scenes]
        clips = _synth(texts, engine, voice, language, work,
                       lambda f: progress("voice", _span(p_vo, f), f"Generating voiceover... {int(f * 100)}%"),
                       warnings, allow_fallback=not cloned)
        if original and gemini_ok and not dub:
            _refit_scenes(scenes, texts, clips, engine, voice, language, work, warnings, not cloned,
                          speed_tts, s["language_label"], detected, progress, p_vo)
        if original:
            # ဇာတ်ကွက်အရှည် မပြောင်း၊ Voiceover ရှည်လျှင် အမြန်နှုန်းတင်/ဖြတ်
            caps, narration_dur, targets = tts_studio.assemble_scenes(
                clips, texts, [x["end"] - x["start"] for x in scenes], speed_tts, work, narration)
        else:
            caps, narration_dur, starts = tts_studio.assemble(clips, [[t] for t in texts], speed_tts, work, narration)
            # ဇာတ်ကွက်တစ်ခုချင်းစီ၏ ပစ်မှတ်စက္ကန့် = Voiceover ကလစ်ရှည်သလောက် (ပထမတစ်ခုမှာ lead ပါ)
            targets = []
            for i in range(len(scenes)):
                end = starts[i + 1] if i + 1 < len(scenes) else narration_dur
                targets.append(end - (0.0 if i == 0 else starts[i]))

        # ---- ၄) ဗီဒီယိုအပိုင်း ပြင်ဆင်ခြင်း ----
        progress("clips", p_cl[0], "Preparing scenes...")
        if original:
            if dub and s.get("dub_keep_audio", True):
                bg_audio = work / "bg.wav"
                run(["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "24000", str(bg_audio)])
            # ဗီဒီယိုတစ်ခုလုံး မူလအရှည်အတိုင်း -> ဇာတ်ကွက်ဖြတ်/ဆက်စရာမလို၊ compose တစ်ကြိမ်တည်းနဲ့ ပြီး
            if clean:
                static = _make_plan_runner(video_path, sw, sh, langs, [(0.0, vdur)], progress, "clips", p_cl)
                plan = text_cleaner.plan_clip(video_path, 0.0, vdur, sw, sh, langs, static)
                pw = min(sw, 960) // 2 * 2
                ph = int(round(sh * pw / sw / 2)) * 2
                total_frames = max(1, int(vdur * video_edit.FPS))
                frames = text_cleaner.cleaned_frames(
                    video_path, 0.0, vdur, pw, ph, video_edit.FPS, plan,
                    on_frame=lambda i: progress("clips", _span(p_cl, 0.1 + 0.9 * min(1.0, i / total_frames)),
                                                f"Cleaning frames... {int(min(1.0, i / total_frames) * 100)}%")
                    if i % 30 == 0 else None)
                base_video = work / "cleaned_base.mp4"
                video_edit.render_clip_from_frames(frames, (pw, ph), vdur, vdur, base_video, width, height, flip, zoom)
                prepared = True
            else:
                base_video, prepared = video_path, False     # encode လုံးဝမလုပ်
        else:
            # highlight recap (short/medium/...) - ဇာတ်ကွက်များကို အပြိုင် render
            static: List = []
            if clean:
                static = _make_plan_runner(video_path, sw, sh, langs, [(x["start"], x["end"]) for x in scenes],
                                           progress, "clips", p_cl)
            pw = min(sw, 960) // 2 * 2
            ph = int(round(sh * pw / sw / 2)) * 2
            clip_files: List[Path] = [work / f"scene_{i:04d}.mp4" for i in range(len(scenes))]

            def render_one(i: int) -> None:
                sc, target = scenes[i], targets[i]
                src_dur = sc["end"] - sc["start"]
                if clean:
                    plan_i = text_cleaner.plan_clip(video_path, sc["start"], src_dur, sw, sh, langs, static)
                    frames_i = text_cleaner.cleaned_frames(video_path, sc["start"], src_dur, pw, ph,
                                                           video_edit.FPS, plan_i)
                    video_edit.render_clip_from_frames(frames_i, (pw, ph), src_dur, target, clip_files[i],
                                                       width, height, flip, zoom)
                else:
                    video_edit.render_clip_from_file(video_path, sc["start"], src_dur, target, clip_files[i],
                                                     width, height, flip, zoom)

            workers = 1 if clean else 3          # OCR ကို အပြိုင်မလုပ်
            finished = 0
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for _ in ex.map(render_one, range(len(scenes))):
                    finished += 1
                    progress("clips", _span(p_cl, 0.1 + 0.9 * finished / len(scenes)),
                             f"Scene {finished}/{len(scenes)} done")
            base_video = work / "recap_base.mp4"
            video_edit.concat_clips(clip_files, base_video, work)
            prepared = True
        unit_caps = [dict(c) for c in caps]
        shade_src, shade_dur = video_path, vdur

    # ======================================================================= #
    # Voiceover Script ON
    # ======================================================================= #
    else:
        if clean:
            p_vo, p_cl, p_render = (0, 45), (45, 75), (75, 100)
        else:
            p_vo, p_cl, p_render = (0, 55), (55, 55), (55, 100)
        max_chars = per_line * 2 if sub["enabled"] else 140
        lines = tts_studio.split_captions(s["script_text"], max_chars)
        if not lines:
            raise ValueError("Voiceover Script ဗလာဖြစ်နေသည်။")
        engine, voice, cloned = _resolve_voice(s, ref_path, consent_path, work, progress, "voice", p_vo)
        groups = tts_engines.group_lines(lines, engine, one_by_one=False)   # Gemini/Cloud: request အရေအတွက်လျှော့ရန် စုပေါင်း
        unit_lines = [[lines[i] for i in g] for g in groups]
        unit_texts = [" ".join(u) for u in unit_lines]
        progress("voice", p_vo[0], f"Generating voiceover ({len(unit_texts)} request(s))...")
        clips = _synth(unit_texts, engine, voice, s["language"], work,
                       lambda f: progress("voice", _span(p_vo, f), f"Generating voiceover... {int(f * 100)}%"),
                       warnings, allow_fallback=not cloned)
        caps, narration_dur, _ = tts_studio.assemble(clips, unit_lines, speed_tts, work, narration)
        unit_caps = caps
        shade_src, shade_dur = video_path, 0.0

        if has_video:
            sw, sh, vdur = probe_video(video_path)
            shade_dur = vdur
            if clean:
                # မူရင်းဗီဒီယိုတစ်ခုလုံးကို AI ဖြင့် သန့်စင် (ဗီဒီယိုရှည်လျှင် အချိန်ကြာနိုင်သည်)
                progress("clean", p_cl[0], "AI is cleaning watermark / subtitles...")
                static = _make_plan_runner(video_path, sw, sh, langs, [(0.0, vdur)], progress, "clean", p_cl)
                plan = text_cleaner.plan_clip(video_path, 0.0, vdur, sw, sh, langs, static)
                pw = min(sw, 960) // 2 * 2
                ph = int(round(sh * pw / sw / 2)) * 2
                total_frames = max(1, int(vdur * video_edit.FPS))
                frames = text_cleaner.cleaned_frames(
                    video_path, 0.0, vdur, pw, ph, video_edit.FPS, plan,
                    on_frame=lambda i: progress("clean", _span(p_cl, 0.1 + 0.9 * min(1.0, i / total_frames)),
                                                f"Cleaning frames... {int(min(1.0, i / total_frames) * 100)}%")
                    if i % 30 == 0 else None)
                base_video = work / "cleaned_base.mp4"
                video_edit.render_clip_from_frames(frames, (pw, ph), vdur, vdur, base_video, width, height, flip, zoom)
                prepared = True
            else:
                base_video = video_path
                prepared = False

    # ======================================================================= #
    # စာတန်းထိုး + MP3 + ဗီဒီယိုပေါင်းစပ်ခြင်း (နှစ်မျိုးလုံး တူညီ)
    # ======================================================================= #
    speed = video_edit.edit_speed(has_video, edit)
    # ရှည်လွန်းသောစာတန်းကို အချိန်ခွဲပြီး အပိုင်းများအဖြစ် ပြသ၊ Auto Edit 1.03x ဆိုလျှင် အချိန်ကို လျှော့
    final_caps: List[dict] = []
    for c in unit_caps:
        for p in tts_studio.split_timed_caption(c, tts_studio.line_limit(c["text"], per_line)):   # တစ်ကြိမ်လျှင် စာတန်းတစ်ကြောင်းတည်း
            final_caps.append({"start": round(p["start"] / speed, 3), "end": round(p["end"] / speed, 3),
                               "text": p["text"]})

    srt = out_dir / f"video_{job_id}.srt"
    tts_studio.write_srt(final_caps, srt)
    subs_path = None
    tl = s["text_logo"]
    logo_on = bool(tl["enabled"] and tl["text"].strip())
    if sub["enabled"] or logo_on:
        subs_path = work / "subs.ass"
        tts_studio.build_ass(final_caps if sub["enabled"] else [], sub, width, height, subs_path, tl)

    mp3 = out_dir / f"video_{job_id}.mp3"           # MP3 သည် ဗီဒီယိုနှင့် အမြန်နှုန်းတူစေသည်
    mp3_cmd = ["ffmpeg", "-y", "-i", str(narration)]
    if speed != 1.0:
        mp3_cmd += ["-af", atempo_chain(speed)]
    run(mp3_cmd + ["-c:a", "libmp3lame", "-b:a", "192k", str(mp3)])

    shade = _shade_settings(s, shade_src if has_video else None, shade_dur)   # ဗီဒီယိုမပါလျှင် အရိပ်မထည့်

    music_path, music_credit = None, ""
    mu = s.get("music") or {}
    if mu.get("enabled") and mu.get("id"):
        progress("render", p_render[0], "Fetching background music...")
        try:
            music_path, music_credit = music_service.fetch_track(mu["id"])
        except Exception as e:      # ဂီတမရလျှင် ဂီတမပါဘဲ ဆက်လုပ်သည်
            warnings.append(f"Background music was skipped: {config.scrub(e)}")
    ring_mov = None
    p_r = p_render
    if viz["enabled"] and viz["style"] == "ring":
        progress("render", p_r[0], "Rendering live ring shape...")
        ring_mov = work / "ring.mov"
        shapes.render_ring(narration, narration_dur, ring_mov, viz["color"],
                           on_progress=lambda f: progress("render", _span(p_r, f * 0.15), "Rendering live ring shape..."))
    progress("render", _span(p_r, 0.15), "Rendering final video...")
    mp4 = out_dir / f"video_{job_id}.mp4"
    duration = video_edit.compose(
        base=base_video, prepared=prepared, narration=narration, narration_dur=narration_dur, out=mp4,
        width=width, height=height, edit=edit, logo=logo_path if s["add_logo"] else None,
        logo_pos=s["logo_position"], subs=subs_path, viz=viz, shade=shade, ring_mov=ring_mov,
        base_dir=base_dir, work=work,
        grade=s["grade"] if (has_video and s["grade"]["enabled"]) else None,
        bg_audio=bg_audio, bg_volume=s["dub_bg_volume"] / 100.0,
        music=music_path, music_volume=float(mu.get("volume", 20)) / 100.0, music_duck=bool(mu.get("duck", True)),
        blur=s["blur"] if (has_video and s["blur"]["enabled"]) else None,
        logo_xy=(s["logo_x"], s["logo_y"]) if (s.get("logo_x") is not None and s.get("logo_y") is not None) else None,
        on_progress=lambda f: progress("render", _span(p_r, 0.15 + 0.85 * f), f"Rendering video... {int(f * 100)}%"))
    intro_dur = 0.0
    cv = s["cover"]
    title = cv["title"].strip()
    if (cv["enabled"] or cv.get("poster")) and not title and cv["auto_title"]:
        try:
            title = gemini_service.make_title([c["text"] for c in final_caps][:12], post_lang or s["language_label"])
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Cover title ကို Gemini မရေးနိုင်ပါ ({config.scrub(e)})")
    # ---- Poster (ခေါင်းစဉ်စာတန်းပါသော ပုံ) - Cover Intro နှင့် Poster နှစ်မျိုးလုံး ဒီပုံကိုသုံးသည် ----
    poster_url = None
    poster_path = None
    if cv["enabled"] or cv.get("poster"):
        try:
            progress("render", 98, "Making poster image...")
            poster_path = out_dir / f"poster_{job_id}.jpg"
            intro_cover.make_poster(poster_path, video_path if has_video else None, title, cv, width, height,
                                    work, base_dir)
        except Exception as e:  # noqa: BLE001
            poster_path = None
            warnings.append(f"Poster ပုံ ထုတ်၍မရပါ ({config.scrub(e)})")
    if cv["enabled"] and poster_path is not None:
        try:
            progress("render", 99, "Adding cover intro...")
            intro_dur = intro_cover.add_cover(mp4, poster_path, cv, width, height, work, base_dir)
            final_caps = [dict(c, start=round(c["start"] + intro_dur, 3), end=round(c["end"] + intro_dur, 3))
                          for c in final_caps]
            tts_studio.write_srt(final_caps, srt)      # SRT ကို cover စက္ကန့်ပမာဏ နောက်ဆုတ်
        except Exception as e:  # noqa: BLE001
            intro_dur = 0.0
            warnings.append(f"Cover intro ထည့်၍မရပါ ({config.scrub(e)})")
    if poster_path is not None:
        if cv.get("poster"):
            poster_url = f"{url_base}/{poster_path.name}"
        else:
            try:
                poster_path.unlink()      # Poster ကို တောင်းမထားဘဲ cover အတွက်သာ ထုတ်ခဲ့လျှင် ဖိုင်မကျန်ရစ်စေရန်
            except OSError:
                pass
    # ---- Title / Caption / Hashtag ----
    post = None
    post_url = None
    if s.get("post_text", True):
        try:
            progress("render", 99, "Writing title / caption / hashtags...")
            post = gemini_service.make_post([c["text"] for c in final_caps], post_lang, "")
            if post:
                txt = out_dir / f"post_{job_id}.txt"
                txt.write_text(f"{post['title']}\n\n{post['caption']}\n\n{' '.join(post['hashtags'])}\n", encoding="utf-8")
                post_url = f"{url_base}/{txt.name}"
        except Exception as e:  # noqa: BLE001
            post = None
            warnings.append(f"Title / Caption / Hashtag ရေး၍မရပါ ({config.scrub(e)})")
    progress("render", 100, "Done")

    return {
        "mode": "subtitle" if sub_only else ("auto-recap" if auto_mode else "script"),
        "duration": round(duration + intro_dur, 2),
        "captions": final_caps,
        "warnings": warnings,
        "video_url": f"{url_base}/{mp4.name}",
        "audio_url": f"{url_base}/{mp3.name}",
        "srt_url": f"{url_base}/{srt.name}",
        "transcript_srt_url": transcript_srt_url,
        "post": post,
        "post_url": post_url,
        "poster_url": poster_url,
        "music_credit": music_credit,
    }
