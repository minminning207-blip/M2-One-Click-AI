/* ဗီဒီယိုရွေးချယ်မှု၊ Mode၊ Font၊ အသံ (Voice) */

/* ======================================================================
   box 1: ဗီဒီယို + Voiceover Script + Mode (Recap / Dubbing)
   ====================================================================== */
const MODE = { v: "recap" };
function updateScriptUi() {
  const on = $("scriptOn").checked;
  $("scriptPanel").classList.toggle("hidden", !on);
  $("modePanel").classList.toggle("hidden", on);
  $("recapPanel").classList.toggle("hidden", on || MODE.v !== "recap");
  const subOnly = !on && MODE.v === "sub";
  $("subOpts").classList.toggle("hidden", !subOnly);
  $("voiceBox").classList.toggle("hidden", subOnly);   // Subtitle only မှာ အသံမထုတ်ပါ
  $("scriptNote").textContent = on
    ? "Voiceover Script ဖွင့်ထားသည် - အောက်က စာသားကို အသံနှင့် စာတန်းထိုးအဖြစ် သုံးမည်။"
    : MODE.v === "sub"
      ? "Subtitle only - မူရင်းအသံကို မပြောင်းပါ။ စကားကို Whisper ဖြင့် စာသားပြောင်းပြီး (ဘာသာပြန်လိုလျှင် Gemini ဖြင့် ပြန်ကာ) ဗီဒီယိုနှင့် ကိုက်အောင် စာတန်းတစ်ကြောင်းချင်း ထည့်ပေးမည်။"
    : MODE.v === "dub"
      ? "Dubbing - ရုပ်ရှင်ထဲက စကားကို အတိအကျ ဘာသာပြန်ပြီး ဇာတ်ကားတစ်ခုလုံးကို အသံသစ်ဖြင့် ပြောပေးမည် (ဇာတ်လမ်းကို မပြောင်း၊ မတိုပါ)။"
      : "Recap - ရုပ်ရှင်ထဲက စကားကို Whisper ဖြင့် စာသားပြောင်းပြီး Gemini ဖြင့် Recap ဇာတ်လမ်းအဖြစ် ပြန်ရေးကာ အသံထည့်ပေးမည်။";
  $("videoHint").textContent = on
    ? "မရွေးလည်းရသည် - မရွေးလျှင် အရောင်နောက်ခံဖြင့် ထုတ်ပေးမည်။"
    : "ဒီလုပ်ဆောင်ပုံအတွက် ဗီဒီယိုဖိုင် မဖြစ်မနေ လိုအပ်သည်။";
  $("whisperWarn").textContent = "⚠ " + (HEALTH.whisper_reason || "Speech-to-Text မသုံးနိုင်ပါ။");
  $("whisperWarn").classList.toggle("hidden", on || HEALTH.whisper_available);
  $("geminiWarn").classList.toggle("hidden", on || HEALTH.gemini_configured);
}
$("scriptOn").addEventListener("change", updateScriptUi);
bindToggle("recapOn", "recapChoose");   // Recap length ခလုတ် ON မှသာ ရွေးချယ်စရာပြ
$("scriptText").addEventListener("input", () => { $("counter").textContent = `${$("scriptText").value.length} / 5000`; });
$("modeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  MODE.v = b.dataset.m;
  document.querySelectorAll("#modeSeg button").forEach((x) => x.classList.toggle("active", x === b));
  $("dubOpts").classList.toggle("hidden", MODE.v !== "dub");
  updateScriptUi();
});

/* ---------- font များ (fonts/ folder ထဲက .ttf/.otf ကို အလိုအလျောက်ဖတ်) ---------- */
const FONT_BASE = ["Liberation Sans", "Noto Sans Myanmar", "Padauk", "Myanmar Text", "Arial", "DejaVu Sans"];
let SERVER_FONTS = [];
function fillFontSelects() {
  const names = [...new Set([...SERVER_FONTS.map((f) => f.family), ...FONT_BASE])];
  ["logoFont", "coverFont"].forEach((id) => {
    const el = $(id), cur = el.value;
    el.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}${SERVER_FONTS.some((f) => f.family === n) ? " ★" : ""}</option>`).join("");
    if (cur) el.value = cur;
  });
  const sf = $("subFont");
  [...sf.querySelectorAll("[data-srv]")].forEach((o) => o.remove());
  SERVER_FONTS.slice().reverse().forEach((f) => {
    if (![...sf.options].some((o) => o.value === f.family)) {
      const o = new Option(f.family + " ★", f.family); o.dataset.srv = "1"; sf.insertBefore(o, sf.firstChild);
    }
  });
}
async function loadFonts() {
  try {
    SERVER_FONTS = await (await fetch("/api/fonts")).json();
    for (const f of SERVER_FONTS) {
      try { const ff = new FontFace(f.family, `url(/fonts/${encodeURIComponent(f.file)})`); await ff.load(); document.fonts.add(ff); } catch (_) {}
    }
  } catch (_) {}
  fillFontSelects();
}


/* ======================================================================
   box 2: Audio Style
   ====================================================================== */
const S = { voices: [], languages: [], lang: "", gender: "All", voice: "", speed: 1, prev: null };
let NAMES = {};   // အသံကို ကိုယ်ပိုင်နာမည်ပေးထားသည်များ (ဒီ browser ထဲတွင်သာ သိမ်းသည်)
try { NAMES = JSON.parse(localStorage.getItem("voiceNames") || "{}"); } catch (_) {}
const saveNames = () => { try { localStorage.setItem("voiceNames", JSON.stringify(NAMES)); } catch (_) {} };
const voiceName = (v) => NAMES[v.id] || v.name;
let CONSENT = [];

// Voice Clone: ဖွင့်ထားလျှင် Model/Voice ရွေးချယ်မှုများကို ပိတ်ထားသည်
function updateCloneUi() {
  const on = $("voiceClone").checked;
  if (on && $("engine").value !== "gemini") { $("engine").value = "gemini"; loadVoices(); }  // language စာရင်း (my ပါ) ပြန်ဆွဲ
  $("clonePanel").classList.toggle("hidden", !on);
  $("cloneWarn").classList.toggle("hidden", HEALTH.gemini_configured);
  ["engine", "ttsLang", "voiceSel", "voicePlay", "voiceRename"].forEach((id) => { $(id).disabled = on && id !== "ttsLang"; });
  document.querySelectorAll("#genderSeg button").forEach((b) => { b.disabled = on; });
}
$("voiceClone").addEventListener("change", updateCloneUi);
async function loadConsent() {
  try {
    CONSENT = await (await fetch("/api/consent")).json();
    $("consentLang").innerHTML = CONSENT.map((c) => `<option value="${esc(c.locale)}">${esc(c.label)}</option>`).join("");
    showConsent();
  } catch (_) {}
}
function showConsent() {
  const c = CONSENT.find((x) => x.locale === $("consentLang").value);
  $("consentText").textContent = c ? c.statement : "";
}
$("consentLang").addEventListener("change", showConsent);

function updateEngineHint() {
  const e = $("engine").value;
  const hints = {
    edge: "Microsoft ၏ အသံများ - အမည်ပေးထားသော အသံများစွာ ရှိသည်။ အခမဲ့။ အင်တာနက်လိုသည်။",
    google: "Google Translate အသံ (gTTS)။ အခမဲ့။ ဘာသာစကားတစ်ခုလျှင် အသံတစ်မျိုးသာ ရှိပြီး ကျား/မ မရွေးရပါ။",
    gemini: "Google AI Studio ၏ Gemini TTS (GEMINI_API_KEY သုံးသည်)။ အသံ ၃၀ ရှိပြီး စာသားကိုကြည့်ကာ ဘာသာစကားကို အလိုအလျောက် သိသည်။",
    cloud: "Google Cloud Text-to-Speech (Chirp 3 HD စသည်) - GOOGLE_TTS_API_KEY သုံးသည်။ Text-to-Speech API နှင့် billing ဖွင့်ထားရမည်။",
  };
  let t = hints[e];
  if (e === "gemini" && !HEALTH.gemini_configured) t += "  ⚠ GEMINI_API_KEY မထည့်ရသေးပါ။";
  if (e === "cloud" && !HEALTH.cloud_tts_configured) t += "  ⚠ GOOGLE_TTS_API_KEY မထည့်ရသေးပါ။";
  $("engineHint").textContent = t;
}

async function loadVoices() {
  updateEngineHint();
  $("voiceSel").innerHTML = "<option>အသံစာရင်း ရယူနေသည်...</option>";
  const engine = $("engine").value;
  try {
    const res = await fetch("/api/tts/voices?engine=" + engine);
    const d = await res.json();
    if (!res.ok) throw new Error(errMsg(d, res));
    S.voices = d.voices; S.languages = d.languages;
    $("voiceSource").textContent = d.source && d.source.startsWith("fallback") ? "(အော့ဖ်လိုင်းစာရင်း)" : "";
    $("ttsLang").innerHTML = d.languages.map((l) => `<option value="${esc(l.code)}">${esc(l.label)}</option>`).join("");
    $("langLabel").textContent = `ဘာသာစကား (${d.languages.length} မျိုး)`;
    const has = (c) => d.languages.some((l) => l.code === c);
    const pref = { edge: ["my-MM", "en-US"], google: ["my", "en"], gemini: ["my", "en"], cloud: ["my-MM", "en-US"] }[engine];
    S.lang = pref.find(has) || (d.languages[0] ? d.languages[0].code : "");
    $("ttsLang").value = S.lang;
    $("genderRow").classList.toggle("hidden", engine === "google");   // Google Translate TTS တွင် gender မရွေးရ
    S.gender = "All"; S.voice = "";
    document.querySelectorAll("#genderSeg button").forEach((x) => x.classList.toggle("active", x.dataset.g === "All"));
    renderVoices();
  } catch (err) {
    S.voices = []; S.languages = [];
    $("ttsLang").innerHTML = ""; $("voiceSel").innerHTML = "<option value=''>အသံမရှိပါ</option>";
    $("engineHint").textContent = err.message; toast(err.message);
  }
}
function renderVoices() {
  const engine = $("engine").value;
  let list = S.voices.filter((v) => v.locale === S.lang || v.locale === "*");
  if (engine !== "google" && S.gender !== "All") list = list.filter((v) => v.gender === S.gender);
  if (!list.some((v) => v.id === S.voice)) S.voice = list.length ? list[0].id : "";
  $("voiceSel").innerHTML = list.length
    ? list.map((v) => `<option value="${esc(v.id)}">${esc(voiceName(v))}${v.gender && engine !== "google" ? " (" + esc(v.gender) + ")" : ""}</option>`).join("")
    : "<option value=''>ဤစစ်ထုတ်မှုအတွက် အသံမရှိပါ</option>";
  $("voiceSel").value = S.voice;
}
$("engine").addEventListener("change", loadVoices);
$("ttsLang").addEventListener("change", () => { S.lang = $("ttsLang").value; renderVoices(); });
$("voiceSel").addEventListener("change", () => { S.voice = $("voiceSel").value; });
$("genderSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || b.disabled) return;
  S.gender = b.dataset.g;
  document.querySelectorAll("#genderSeg button").forEach((x) => x.classList.toggle("active", x === b));
  renderVoices();
});
// ✎ ခလုတ်: အသံကို ကိုယ်ပိုင်နာမည်ပေးသည် (ဥပမာ "Kyaw Kyaw")
$("voiceRename").addEventListener("click", () => {
  const v = S.voices.find((x) => x.id === S.voice); if (!v) return;
  const n = prompt("အသံအမည် ပေးပါ (ဗလာထားလျှင် မူလအမည်ပြန်ဖြစ်မည်):", voiceName(v));
  if (n === null) return;
  if (n.trim()) NAMES[v.id] = n.trim(); else delete NAMES[v.id];
  saveNames(); renderVoices();
});
// ▶ ခလုတ်: အသံနမူနာ နားထောင်သည်
$("voicePlay").addEventListener("click", async () => {
  const v = S.voices.find((x) => x.id === S.voice); if (!v) return;
  const btn = $("voicePlay");
  if (S.prev) { S.prev.pause(); S.prev = null; }
  btn.classList.add("loading");
  try {
    const res = await fetch("/api/tts/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine: $("engine").value, voice: v.id, language: v.locale === "*" ? S.lang : v.locale }),
    });
    if (!res.ok) { let d = null; try { d = await res.json(); } catch (_) {} throw new Error(errMsg(d, res)); }
    const url = URL.createObjectURL(await res.blob());
    const a = new Audio(url); S.prev = a; a.onended = () => URL.revokeObjectURL(url);
    await a.play();
  } catch (err) { toast(err.message); }
  finally { btn.classList.remove("loading"); }
});

// အမြန်နှုန်း (0.5x - 4x)
function setSpeed(v, fromCustom) {
  S.speed = Math.max(0.5, Math.min(4, v));
  document.querySelectorAll("#speedChips button").forEach((b) => b.classList.toggle("active", !fromCustom && +b.dataset.v === S.speed));
  if (!fromCustom) $("speedCustom").value = "";
}
$("speedChips").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setSpeed(+b.dataset.v, false); });
$("speedCustom").addEventListener("input", () => { const v = parseFloat($("speedCustom").value); if (!isNaN(v)) setSpeed(v, true); });

