/* စာတန်းထိုး၊ Auto Edit၊ Logo၊ Cover၊ အရောင်၊ အရိပ်၊ ဗီဒီယိုအရွယ်အစား */

/* ======================================================================
   နေရာစိတ်ကြိုက် (drag) - စာတန်း / ပုံ logo / စာသား logo
   POS.x / POS.y = အလယ်မှတ်၏ နေရာ (canvas ၏ ၀ - ၁ အချိုး)  ၊ null = Preset နေရာအတိုင်း
   ====================================================================== */
const POS = { sub: null, logo: null, tlogo: null };
const POS_SELECT = { sub: "subPos", logo: "logoPos", tlogo: "tlPos" };
const POS_DEFAULT = { sub: "bottom", logo: "top-right", tlogo: "top-left" };
function clearPos(key) {
  POS[key] = null;
  const sel = $(POS_SELECT[key]);
  if (sel.value === "custom") sel.value = POS_DEFAULT[key];
}
Object.keys(POS_SELECT).forEach((k) => $(POS_SELECT[k]).addEventListener("change", () => { if ($(POS_SELECT[k]).value !== "custom") POS[k] = null; }));
const selVal = (id, fallback) => { const v = $(id).value; return v === "custom" ? fallback : v; };
const posXY = (k) => (POS[k] ? { x: +POS[k].x.toFixed(4), y: +POS[k].y.toFixed(4) } : { x: null, y: null });

/* ======================================================================
   box 3: စာတန်းထိုး
   ====================================================================== */
const OUTLINES = {
  "black-outline": { mode: "outline", color: "#000000", opacity: 1 },
  "white-outline": { mode: "outline", color: "#FFFFFF", opacity: 1 },
  "black-box": { mode: "box", color: "#000000", opacity: 1 },
  "semi-box": { mode: "box", color: "#000000", opacity: 0.6 },
  "shadow": { mode: "shadow", color: "#000000", opacity: 1 },
  "none": { mode: "none", color: "#000000", opacity: 1 },
};
const PRESETS = {
  classic: { color: "#FFFFFF", outline: "black-outline", size: 56, pos: "bottom" },
  yellow: { color: "#FFE600", outline: "black-outline", size: 72, pos: "bottom" },
  boxed: { color: "#FFFFFF", outline: "semi-box", size: 56, pos: "bottom" },
  neon: { color: "#39FF14", outline: "black-outline", size: 72, pos: "middle" },
  minimal: { color: "#FFFFFF", outline: "shadow", size: 56, pos: "bottom" },
};
const setSubColor = bindColor("subColor", "subColorName");
$("subPreset").addEventListener("change", () => {
  const p = PRESETS[$("subPreset").value];
  setSubColor(p.color); $("subOutline").value = p.outline; $("subSize").value = p.size; $("subPos").value = p.pos; POS.sub = null;
});
$("subFont").addEventListener("change", () => $("subFontCustom").classList.toggle("hidden", $("subFont").value !== "__custom"));
function getSub() {   // server သို့ ပို့မည့် စာတန်းထိုးဆက်တင်
  const o = OUTLINES[$("subOutline").value];
  const fam = $("subFont").value === "__custom" ? ($("subFontCustom").value.trim() || "Liberation Sans") : $("subFont").value;
  const xy = posXY("sub");
  return {
    enabled: $("subEnabled").checked, font_family: fam, font_size: +$("subSize").value,
    color: $("subColor").value.toUpperCase(), outline_mode: o.mode, outline_color: o.color,
    box_opacity: o.opacity, position: selVal("subPos", "bottom"), bold: true, pos_x: xy.x, pos_y: xy.y,
  };
}

/* ======================================================================
   box 4-6: Auto Edit / Logo / Cover / Color / Shade ၏ ခလုတ်များ
   ====================================================================== */
bindToggle("subEnabled", "subPanel");
bindToggle("autoEdit", "editPanel");
bindToggle("addLogo", "logoPanel");
bindToggle("textLogoOn", "textLogoPanel");
function syncCoverPanel() { $("coverPanel").classList.toggle("hidden", !($("coverOn").checked || $("posterOn").checked)); }
$("coverOn").addEventListener("change", syncCoverPanel); $("posterOn").addEventListener("change", syncCoverPanel); syncCoverPanel();
bindToggle("blurOn", "blurPanel");
bindToggle("gradeOn", "gradePanel");
bindToggle("shadeOn", "shadePanel");
bindColor("tlColor", "tlColorName");
bindColor("gradeTint", "gradeTintName");
bindColor("vizColor", "vizColorName");
bindColor("shadeColor", "shadeColorName");
function bindRange(id) { const el = $(id), out = $(id + "Val"); const f = () => { out.textContent = el.value; }; el.addEventListener("input", f); f(); }
["tlOpacity", "blurStrength", "dubVol", "gradeTintOp", "gradeBr", "gradeCt", "gradeSa", "gradeVg"].forEach(bindRange);

const SH = { mode: "auto" };
$("shadeModeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  SH.mode = b.dataset.m;
  document.querySelectorAll("#shadeModeSeg button").forEach((x) => x.classList.toggle("active", x === b));
  $("shadeSliderBox").classList.toggle("hidden", SH.mode !== "custom");
});
$("shadeOpacity").addEventListener("input", () => { $("shadeVal").textContent = $("shadeOpacity").value; });
$("vizOn").addEventListener("change", () => $("vizControls").classList.toggle("hidden", !$("vizOn").checked));
function syncShapeColor() {   // None ဆိုလျှင် shape အရောင် မလိုတော့
  const none = $("vizStyle").value === "none";
  $("vizColor").disabled = none; $("vizColorName").disabled = none;
}
$("vizStyle").addEventListener("change", syncShapeColor);
syncShapeColor();

/* ---------- ဗီဒီယိုအရွယ်အစား (box လေးများ) ---------- */
$("sizeBoxes").addEventListener("click", (e) => {
  const b = e.target.closest(".size"); if (!b) return;
  document.querySelectorAll("#sizeBoxes .size").forEach((x) => x.classList.toggle("active", x === b));
  $("vizAspect").value = b.dataset.v;
  $("vizAspect").dispatchEvent(new Event("change"));
});
