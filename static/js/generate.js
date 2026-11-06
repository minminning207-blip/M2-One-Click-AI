/* Progress၊ ရလဒ်ပြသခြင်း၊ Generate၊ စတင်ချိန် */

/* ======================================================================
   Processing ပြသခြင်း (Visualizer box ထဲရှိ #progressBox)
   ====================================================================== */
function renderProgress(j) {
  const steps = j.steps.map((s) =>
    `<li class="${s.status}"><span class="ic">${s.status === "done" ? "✔" : s.status === "active" ? '<i class="spin"></i>' : "○"}</span>${esc(s.label)}</li>`
  ).join("");
  const cancelling = j.message === "Cancelling...";
  const title = j.status === "queued" ? `တန်းစီစောင့်နေသည်${j.queue_position ? ` (${j.queue_position} ခုမြောက်)` : ""}` : (cancelling ? "Cancel လုပ်နေသည်" : "ပြုလုပ်နေသည်");
  $("progressBox").innerHTML = `
    <div class="progress">
      <div class="ptop"><b>${title}</b><span>${Math.round(j.progress)}%</span></div>
      <div class="bar"><div class="fill" style="width:${j.progress}%"></div></div>
      <div class="pmsg">${esc(j.message)}</div>
      <ul class="steps">${steps}</ul>
      <div class="pmeta">ကြာချိန် ${fmtTime(j.elapsed)} · ဒီစာမျက်နှာကို မပိတ်ပါနှင့်</div>
      <div style="margin-top:12px"><button type="button" class="btn-sm danger" data-cancel ${cancelling ? "disabled" : ""}>✖ ${cancelling ? "Cancel လုပ်နေသည်..." : "Cancel လုပ်မည်"}</button></div>
    </div>`;
}
let CURRENT_JOB = null;
document.addEventListener("click", async (e) => {       // Cancel ခလုတ် (progress box ကို ၀.၇ စက္ကန့်တစ်ခါ ပြန်ဆွဲသဖြင့် delegation သုံးသည်)
  const b = e.target.closest("[data-cancel]");
  if (!b || !CURRENT_JOB) return;
  b.disabled = true; b.textContent = "✖ Cancel လုပ်နေသည်...";
  try { await fetch(`/api/jobs/${CURRENT_JOB}/cancel`, { method: "POST" }); } catch (_) {}
});
async function runJob(jobId) {   // server ကို ၀.၇ စက္ကန့်တစ်ခါ မေးပြီး တိုးတက်မှု ပြသည်
  while (true) {
    const res = await fetch(`/api/jobs/${jobId}`);
    let j = null; try { j = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(errMsg(j, res));
    renderProgress(j);
    if (j.status === "done") return j.result;
    if (j.status === "cancelled") return null;
    if (j.status === "error") throw new Error(j.error || "Job failed");
    await sleep(700);
  }
}


/* ရလဒ် ပြသခြင်း: video + MP4 / MP3 download */
function showResult(r) {
  CAPS = r.captions || [];
  const v = $("resVideo");
  v.src = r.video_url + "?t=" + Date.now();
  v.onplay = () => attachAnalyser(v);   // ဖွင့်လိုက်မှ Live shape preview ကို အသံနှင့် ချိတ်သည်
  $("dlMp4").href = r.video_url; $("dlMp3").href = r.audio_url; $("dlSrt").href = r.srt_url;
  $("dlTranscript").classList.toggle("hidden", !r.transcript_srt_url);
  if (r.transcript_srt_url) $("dlTranscript").href = r.transcript_srt_url;
  $("resWarn").innerHTML = (r.warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("") +
    (r.music_credit ? `<div class="note">♪ Music credit (add it to your video description): ${esc(r.music_credit)}</div>` : "");
  $("capsList").innerHTML = CAPS.map((c) => `<p>[${c.start.toFixed(2)}s] ${esc(c.text)}</p>`).join("");
  $("posterBox").classList.toggle("hidden", !r.poster_url);
  if (r.poster_url) $("dlPoster").href = r.poster_url;
  const po = r.post;
  $("postBox").classList.toggle("hidden", !po);
  if (po) {
    $("postTitle").value = po.title || ""; $("postCaption").value = po.caption || ""; $("postTags").value = (po.hashtags || []).join(" ");
    $("dlPost").classList.toggle("hidden", !r.post_url); if (r.post_url) $("dlPost").href = r.post_url;
  }
  $("resultBox").classList.remove("hidden");
  $("resultBox").scrollIntoView({ behavior: "smooth", block: "nearest" });
}


requestAnimationFrame(frame);

/* ======================================================================
   ဗီဒီယို ထုတ်လုပ်ခြင်း
   ====================================================================== */
$("genBtn").addEventListener("click", async () => {
  const errBox = $("errBox"); errBox.innerHTML = "";
  const showErr = (m) => { errBox.innerHTML = `<div class="err">${esc(m)}</div>`; errBox.scrollIntoView({ behavior: "smooth", block: "nearest" }); };
  const scriptOn = $("scriptOn").checked, videoFile = $("video").files[0];
  const subOnly = !scriptOn && MODE.v === "sub", clone = $("voiceClone").checked && !subOnly;

  // client ဘက် အချက်အလက် စစ်ဆေးခြင်း
  if (scriptOn && !$("scriptText").value.trim()) return showErr("Voiceover Script ဖွင့်ထားသည် - စာသား ရိုက်ထည့်ပေးပါ။");
  if (!scriptOn && !videoFile) return showErr("ဗီဒီယိုဖိုင် ရွေးပေးပါ (Voiceover Script ပိတ်ထားလျှင် ဗီဒီယို မဖြစ်မနေလိုသည်)။");
  if (clone && (!$("refAudio").files[0] || !$("consentAudio").files[0])) return showErr("Voice Clone ဖွင့်ထားသည် - အသံနမူနာနှင့် ခွင့်ပြုချက်အသံသွင်းမှု နှစ်ဖိုင်လုံး တင်ပေးပါ။");
  if (!subOnly && !clone && $("engine").value !== "google" && !S.voice) return showErr("အသံ (Voice) ရွေးပေးပါ။");
  if ($("addLogo").checked && !$("logoFile").files[0]) return showErr("ပုံ Logo ဖွင့်ထားသည် - Logo ပုံဖိုင် ရွေးပေးပါ။");
  if ($("musicOn").checked && !MUSIC.id) return showErr("Background music is on - choose a track first (or turn it off).");
  if ($("textLogoOn").checked && !$("tlText").value.trim()) return showErr("စာသား Logo ဖွင့်ထားသည် - Logo စာသား ရိုက်ထည့်ပေးပါ။");

  const logoXY = posXY("logo"), tlXY = posXY("tlogo");
  const settings = {
    script_on: scriptOn, script_text: $("scriptText").value,
    recap_length: (MODE.v !== "recap") ? "original" : ($("recapOn").checked ? $("recapLength").value : "original"),   // OFF = မူလဗီဒီယိုအရှည်အတိုင်း
    engine: $("engine").value, voice: S.voice, language: S.lang,
    language_label: subOnly ? ($("subLang").value === "__orig" ? "" : $("subLang").value)
                            : ($("ttsLang").selectedOptions[0] ? $("ttsLang").selectedOptions[0].textContent : ""),
    sub_translate: $("subLang").value !== "__orig",
    voice_clone: clone, consent_lang: $("consentLang").value || "en-US",
    speed: S.speed, aspect_ratio: $("vizAspect").value,
    subtitle: getSub(),
    auto_edit: { enabled: $("autoEdit").checked, remove_text: false, ocr_lang: "en",
                 flip_speed: $("flipSpeed").checked, zoom: $("zoom").checked, rhythm: $("rhythm").value },
    post_text: $("postOn").checked,
    music: getMusic(),
    blur: { enabled: $("blurOn").checked, x: +BLUR.x.toFixed(4), y: +BLUR.y.toFixed(4), w: +BLUR.w.toFixed(4), h: +BLUR.h.toFixed(4), strength: +$("blurStrength").value },
    add_logo: $("addLogo").checked, logo_position: selVal("logoPos", "top-right"), logo_x: logoXY.x, logo_y: logoXY.y,
    viz: { enabled: $("vizOn").checked, position: $("vizPos").value, style: $("vizStyle").value,
           color: $("vizColor").value.toUpperCase(), background: $("vizBg").value },
    shade: { enabled: $("shadeOn").checked, mode: SH.mode, color: $("shadeColor").value.toUpperCase(), opacity: +$("shadeOpacity").value },
    mode: MODE.v, dub_keep_audio: $("dubKeep").checked, dub_bg_volume: +$("dubVol").value,
    text_logo: { enabled: $("textLogoOn").checked, text: $("tlText").value, font_family: $("logoFont").value,
                 font_size: +$("tlSize").value, color: $("tlColor").value.toUpperCase(),
                 opacity: +$("tlOpacity").value / 100, position: selVal("tlPos", "top-left"), x: tlXY.x, y: tlXY.y },
    cover: { enabled: $("coverOn").checked, template: $("coverTpl").value, title: $("coverTitle").value,
             tagline: $("coverTag").value, auto_title: $("coverAuto").checked, font_family: $("coverFont").value,
             font_size: +$("coverSize").value, seconds: +$("coverSec").value, poster: $("posterOn").checked },
    grade: { enabled: $("gradeOn").checked, tint_color: $("gradeTint").value.toUpperCase(),
             tint_opacity: +$("gradeTintOp").value, brightness: +$("gradeBr").value,
             contrast: +$("gradeCt").value, saturation: +$("gradeSa").value, vignette: +$("gradeVg").value },
  };
  // ဖိုင်များကို လိုအပ်မှသာ ထည့်ပို့သည်
  const fd = new FormData();
  fd.append("settings", JSON.stringify(settings));
  if (videoFile) fd.append("video", videoFile);
  if (clone) { fd.append("reference_audio", $("refAudio").files[0]); fd.append("consent_audio", $("consentAudio").files[0]); }
  if (settings.add_logo) fd.append("logo", $("logoFile").files[0]);

  $("genBtn").disabled = true; $("resultBox").classList.add("hidden"); $("resVideo").pause();
  $("progressBox").innerHTML = '<div class="progress"><i class="spin"></i> ဖိုင်တင်နေသည်...</div>';
  try {
    const res = await fetch("/api/create", { method: "POST", body: fd });
    let d = null; try { d = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(errMsg(d, res));
    CURRENT_JOB = d.job_id;
    const r = await runJob(d.job_id);
    $("progressBox").innerHTML = "";
    if (r === null) {       // အသုံးပြုသူ Cancel လုပ်ခဲ့သည်
      errBox.innerHTML = '<div class="note">✖ Cancel လုပ်ပြီးပါပြီ။ ထပ်လုပ်လိုလျှင် အပေါ်က ဆက်တင်များကို ပြင်ပြီး ပြန်နှိပ်ပါ။</div>';
    } else {
      showResult(r);
      if (window.refreshVideos) window.refreshVideos();      // sidebar ၏ ဗီဒီယိုစာရင်း အသစ်ပြန်ဆွဲ
    }
  } catch (err) {
    $("progressBox").innerHTML = ""; showErr(err.message);
  } finally { CURRENT_JOB = null; $("genBtn").disabled = false; }
});

/* ---------- ကူးယူခြင်း (copy) ---------- */
async function copyText(t, btn) {
  try { await navigator.clipboard.writeText(t); }
  catch (_) { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) {} ta.remove(); }
  const old = btn.textContent; btn.textContent = "✔ ကူးပြီး"; setTimeout(() => { btn.textContent = old; }, 1200);
}
document.addEventListener("click", (e) => { const b = e.target.closest("[data-copy]"); if (b) copyText($(b.dataset.copy).value, b); });
$("copyAll").addEventListener("click", () => copyText([$("postTitle").value, $("postCaption").value, $("postTags").value].join("\n\n"), $("copyAll")));

/* စတင်ချိန် */
updateScriptUi(); updateCloneUi();
fillFontSelects(); loadFonts();
loadHealth(); loadConsent(); loadVoices();
pvRefresh();

