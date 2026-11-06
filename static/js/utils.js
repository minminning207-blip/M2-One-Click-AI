/* helper function များ + server အခြေအနေစစ်ခြင်း */
/* ======================================================================
   အခြေခံ helper များ
   ====================================================================== */
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// HTML ထဲ ထည့်မည့် စာသားများကို လုံခြုံအောင် escape လုပ်သည်
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (s) => { s = Math.round(s); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };

// FastAPI error (စာသား သို့မဟုတ် စာရင်း) ကို ဖတ်လို့ရသော စာသားအဖြစ် ပြောင်းသည်
function errMsg(data, res) {
  if (data && data.detail) {
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d) => (d.loc ? d.loc.slice(1).join(".") + ": " : "") + d.msg).join("\n");
  }
  return `Server error (${res.status})`;
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}
// အရောင်အမည်ရွေးချယ်မှု (select) နှင့် swatch (color input) ကို ချိတ်ဆက်သည် - အရောင်ကုဒ်ကို မပြပါ
function bindColor(swatchId, selectId, onChange) {
  const sw = $(swatchId), sel = $(selectId);
  const set = (hex) => {
    hex = hex.toUpperCase(); sw.value = hex.toLowerCase();
    const opt = [...sel.options].find((o) => o.value === hex);
    sel.value = opt ? hex : "custom";
    if (onChange) onChange();
  };
  sel.addEventListener("change", () => { if (sel.value !== "custom") set(sel.value); });
  sw.addEventListener("input", () => set(sw.value));
  return set;
}
function bindToggle(cbId, panelId) {
  const cb = $(cbId), panel = $(panelId);
  const sync = () => panel.classList.toggle("hidden", !cb.checked);
  cb.addEventListener("change", sync); sync();
}

/* ======================================================================
   server အခြေအနေ စစ်ခြင်း (API key ကို ဘယ်တော့မှ မလာပါ - ရှိ/မရှိ သာ)
   ====================================================================== */
const HEALTH = { gemini_configured: true, cloud_tts_configured: true, whisper_available: true, ocr_available: true, ocr_reason: "" };
async function loadHealth() {
  try { Object.assign(HEALTH, await (await fetch("/api/health")).json()); } catch (_) {}
  updateScriptUi(); updateCloneUi(); updateEngineHint();
}

/* Session ကုန်သွားလျှင် (401) Login စာမျက်နှာသို့ ပြန်ပို့ */
(() => {
  const _fetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await _fetch(...args);
    if (res.status === 401 && !location.pathname.startsWith("/auth")) location.href = "/auth.html";
    return res;
  };
})();
