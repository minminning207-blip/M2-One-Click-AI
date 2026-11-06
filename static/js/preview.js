/* Preview canvas (ဆွဲရွှေ့ခြင်း၊ Blur၊ player၊ shape) */

/* ======================================================================
   Preview canvas
   ====================================================================== */
const cv = $("pv"), ctx = cv.getContext("2d");
const pvVideo = document.createElement("video");   // ရွေးထားသော ဗီဒီယိုကို preview ထဲမှာ ဖွင့်ကြည့်ရန်
pvVideo.muted = true; pvVideo.playsInline = true; pvVideo.preload = "auto";
let pvUrl = null;
function sizeCanvas() {   // ဗီဒီယိုအရွယ်အစားအလိုက် canvas အချိုးကို ပြောင်းသည်
  const a = $("vizAspect").value;
  const d = a === "16:9" ? [800, 450] : a === "1:1" ? [600, 600] : [450, 800];
  cv.width = d[0]; cv.height = d[1];
  $("pvBox").style.maxWidth = a === "16:9" ? "760px" : a === "1:1" ? "480px" : "340px";
}
$("vizAspect").addEventListener("change", sizeCanvas);
sizeCanvas();

/* ---------- Preview player (▶ / seek / အသံ) ---------- */
const pvPlay = $("pvPlay"), pvSeek = $("pvSeek"), pvTime = $("pvTime"), pvMute = $("pvMute");
let seeking = false;
function pvEnable(on) { [pvPlay, pvSeek, pvMute].forEach((el) => { el.disabled = !on; }); }
function pvRefresh() {
  const d = isFinite(pvVideo.duration) ? pvVideo.duration : 0, t = pvVideo.currentTime || 0;
  if (!seeking && d > 0) pvSeek.value = Math.round((t / d) * 1000);
  pvTime.textContent = fmtTime(t) + " / " + fmtTime(d);
  pvPlay.textContent = pvVideo.paused ? "▶" : "⏸";
  pvMute.textContent = pvVideo.muted ? "🔇" : "🔊";
}
$("video").addEventListener("change", () => {
  pvVideo.pause();
  if (pvUrl) URL.revokeObjectURL(pvUrl);
  const f = $("video").files[0];
  pvEnable(false);
  if (!f) { pvVideo.removeAttribute("src"); pvVideo.load(); pvRefresh(); return; }
  pvUrl = URL.createObjectURL(f); pvVideo.src = pvUrl;
  pvVideo.addEventListener("loadedmetadata", () => {
    try { pvVideo.currentTime = Math.min(1, (pvVideo.duration || 2) / 2); } catch (_) {}
    pvEnable(true); pvRefresh();
  }, { once: true });
});
["timeupdate", "play", "pause", "ended", "volumechange", "seeked"].forEach((ev) => pvVideo.addEventListener(ev, pvRefresh));
pvPlay.addEventListener("click", () => { if (pvVideo.paused) pvVideo.play().catch(() => {}); else pvVideo.pause(); });
pvSeek.addEventListener("input", () => { seeking = true; if (pvVideo.duration) pvVideo.currentTime = (pvSeek.value / 1000) * pvVideo.duration; });
pvSeek.addEventListener("change", () => { seeking = false; });
pvMute.addEventListener("click", () => { pvVideo.muted = !pvVideo.muted; });

/* ---------- ပုံ logo (preview အတွက်) ---------- */
const LOGO = { img: null, url: null };
$("logoFile").addEventListener("change", () => {
  if (LOGO.url) URL.revokeObjectURL(LOGO.url);
  const f = $("logoFile").files[0];
  if (!f) { LOGO.img = null; return; }
  LOGO.url = URL.createObjectURL(f);
  const im = new Image(); im.onload = () => { LOGO.img = im; }; im.src = LOGO.url;
});

/* ---------- ရလဒ်ဗီဒီယို ဖွင့်သည့်အခါ အသံမှ အစစ်အမှန် လှုပ်ရှားမှုကို ယူရန် (Web Audio) ---------- */
let actx = null, analyser = null, waveBuf = null, freqBuf = null, CAPS = [];
function attachAnalyser(el) {
  try {
    if (!actx) {
      actx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = actx.createAnalyser(); analyser.fftSize = 2048; analyser.smoothingTimeConstant = 0.75;
      analyser.connect(actx.destination);
      waveBuf = new Uint8Array(analyser.fftSize); freqBuf = new Uint8Array(analyser.frequencyBinCount);
    }
    // createMediaElementSource ကို element တစ်ခုအတွက် တစ်ကြိမ်သာ ခေါ်လို့ရသည်
    if (!el._src) { el._src = actx.createMediaElementSource(el); el._src.connect(analyser); }
    if (actx.state === "suspended") actx.resume();
  } catch (_) {}
}

/* ---------- စာကြောင်းခွဲ (canvas preview အတွက်) ---------- */
function graphemes(w) {
  try { return [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(w)].map((s) => s.segment); }
  catch (_) { return Array.from(w); }
}
function wrapCanvas(c, text, maxW) {
  const words = text.split(/\s+/).filter(Boolean), lines = []; let cur = "";
  for (const w of words) {
    const units = c.measureText(w).width > maxW ? graphemes(w) : [w];
    units.forEach((u, i) => {
      const joiner = (units.length === 1 || i === units.length - 1) ? " " : "";
      if (cur && c.measureText((cur + u).trimEnd()).width > maxW) { lines.push(cur.trimEnd()); cur = u + joiner; }
      else cur += u + joiner;
    });
  }
  if (cur.trim()) lines.push(cur.trimEnd());
  return lines.length ? lines : [text];
}

/* ---------- ဆွဲလို့ရသော အရာများ (hit boxes) ---------- */
let HIT = [], HOVER = null, drag = null;
/* ---------- မှုန်ဝါးအကွက် (ဘယ်ထိပ်ထောင့် x,y + အကျယ် w + အမြင့် h ၊ canvas ၏ ၀-၁ အချိုး) ---------- */
const BLUR_DEF = { x: 0.1, y: 0.78, w: 0.8, h: 0.1 }, BLUR = Object.assign({}, BLUR_DEF), BLUR_HS = 14;
const CTX_FILTER = "filter" in ctx;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
function drawBlur(W, H, hasVideo) {
  if (!$("blurOn").checked) return;
  const x = BLUR.x * W, y = BLUR.y * H, w = BLUR.w * W, h = BLUR.h * H;
  if (hasVideo) {
    ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
    if (CTX_FILTER) {   // ထွက်လာမည့် ဗီဒီယိုနှင့် အားတူအောင် (final အကျယ်နှင့် အချိုးကျ)
      const sigma = (4 + +$("blurStrength").value * 0.36) * W / (W > H ? 1920 : 1080);
      ctx.filter = `blur(${sigma.toFixed(1)}px)`; ctx.drawImage(cv, 0, 0); ctx.filter = "none";
    } else { ctx.fillStyle = "rgba(0,0,0,.45)"; ctx.fillRect(x, y, w, h); }   // ctx.filter မထောက်ပံ့သော browser
    ctx.restore();
  }
  ctx.save(); ctx.setLineDash([]); ctx.strokeStyle = "rgba(255,170,0,.95)"; ctx.lineWidth = 2; ctx.strokeRect(x, y, w, h);
  ctx.fillStyle = "rgba(255,170,0,.95)"; ctx.fillRect(x + w - BLUR_HS, y + h - BLUR_HS, BLUR_HS, BLUR_HS); ctx.restore();
  HIT.push({ key: "blur", cx: x + w / 2, cy: y + h / 2, w, h });
  HIT.push({ key: "blurR", cx: x + w - BLUR_HS / 2, cy: y + h - BLUR_HS / 2, w: BLUR_HS + 16, h: BLUR_HS + 16 });
}
$("blurReset").addEventListener("click", () => Object.assign(BLUR, BLUR_DEF));
function drawLogoImage(W, H) {
  const im = LOGO.img;
  if (!$("addLogo").checked || !im || !im.naturalWidth) return;
  const w = W * 0.15, h = (w * im.naturalHeight) / im.naturalWidth, m = (20 * W) / (W > H ? 1920 : 1080);
  let cx, cy;
  if (POS.logo) { cx = POS.logo.x * W; cy = POS.logo.y * H; }
  else {
    const p = $("logoPos").value;
    cx = p.endsWith("left") ? m + w / 2 : W - m - w / 2;
    cy = p.startsWith("top") ? m + h / 2 : H - m - h / 2;
  }
  ctx.drawImage(im, cx - w / 2, cy - h / 2, w, h);
  HIT.push({ key: "logo", cx, cy, w, h });
}
function drawTextLogo(W, H) {
  const t = $("tlText").value.trim();
  if (!$("textLogoOn").checked || !t) return;
  const px = (+$("tlSize").value * W) / (W > H ? 1920 : 1080), m = W * 0.04;
  ctx.save();
  ctx.font = `bold ${px}px "${$("logoFont").value}", "Noto Sans Myanmar", "Myanmar Text", sans-serif`;
  const w = ctx.measureText(t).width, h = px * 1.2;
  let cx, cy;
  if (POS.tlogo) { cx = POS.tlogo.x * W; cy = POS.tlogo.y * H; }
  else {
    const p = $("tlPos").value;
    cx = p.endsWith("left") ? m + w / 2 : p.endsWith("right") ? W - m - w / 2 : W / 2;
    cy = p.startsWith("top") ? H * 0.05 + h / 2 : H - H * 0.05 - h / 2;
  }
  ctx.globalAlpha = $("tlOpacity").value / 100; ctx.fillStyle = $("tlColor").value;
  ctx.strokeStyle = "#000"; ctx.lineWidth = px * 0.12; ctx.lineJoin = "round";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.strokeText(t, cx, cy); ctx.fillText(t, cx, cy);
  ctx.restore();
  HIT.push({ key: "tlogo", cx, cy, w, h });
}
function drawSubtitle(W, H, text) {
  const st = getSub(); if (!st.enabled) return;
  const k = W / (W > H ? 1920 : 1080), px = st.font_size * k;
  const fam = `"${st.font_family}", "Noto Sans Myanmar", "Padauk", "Myanmar Text", "Segoe UI", sans-serif`;
  ctx.font = `bold ${px}px ${fam}`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
  const lines = [text.replace(/\s+/g, " ").trim()], lh = px * 1.25, total = lines.length * lh;   // စာတန်းတစ်ကြောင်းတည်းသာ
  const o = OUTLINES[$("subOutline").value];
  const cx = POS.sub ? POS.sub.x * W : W / 2;
  const cy = POS.sub ? POS.sub.y * H : st.position === "top" ? H * 0.10 + total / 2 : st.position === "middle" ? H / 2 : H - H * 0.12 - total / 2;
  const top = cy - total / 2;
  let maxW = 0;
  lines.forEach((ln, i) => {
    const y = top + i * lh + lh / 2, lw = ctx.measureText(ln).width;
    maxW = Math.max(maxW, lw);
    if (o.mode === "box") {
      const w = lw + st.font_size * 0.44 * k;
      ctx.fillStyle = `rgba(0,0,0,${o.opacity})`; ctx.fillRect(cx - w / 2, y - lh / 2, w, lh);
    }
    if (o.mode === "outline") { ctx.strokeStyle = o.color; ctx.lineWidth = Math.max(2, st.font_size * 0.07) * 2 * k; ctx.strokeText(ln, cx, y); }
    if (o.mode === "shadow") { ctx.shadowColor = "rgba(0,0,0,.85)"; ctx.shadowOffsetX = ctx.shadowOffsetY = Math.max(2, st.font_size * 0.06) * k; ctx.shadowBlur = px * 0.08; }
    ctx.fillStyle = st.color; ctx.fillText(ln, cx, y);
    ctx.shadowColor = "transparent"; ctx.shadowBlur = 0; ctx.shadowOffsetX = ctx.shadowOffsetY = 0;
  });
  HIT.push({ key: "sub", cx, cy, w: maxW + px * 0.5, h: total + px * 0.2 });
}
function drawHandles() {   // ဆွဲလို့ရသော အရာများကို မျဉ်းစက်ကွင်းဖြင့် ပြသည် (preview တွင်သာ)
  ctx.save(); ctx.setLineDash([6, 4]);
  for (const h of HIT) {
    if (h.key.startsWith("blur")) continue;
    const active = drag ? drag.key === h.key : HOVER === h.key;
    ctx.strokeStyle = active ? "rgba(255,255,255,.95)" : "rgba(255,255,255,.35)";
    ctx.lineWidth = active ? 2 : 1;
    ctx.strokeRect(h.cx - h.w / 2, h.cy - h.h / 2, h.w, h.h);
  }
  ctx.restore();
}

/* ---------- drag (mouse + touch) ---------- */
function canvasPoint(e) {
  const r = cv.getBoundingClientRect();
  return { x: ((e.clientX - r.left) * cv.width) / r.width, y: ((e.clientY - r.top) * cv.height) / r.height };
}
function hitTest(p) {
  for (let i = HIT.length - 1; i >= 0; i--) {   // နောက်မှဆွဲထားသည်က အပေါ်ဆုံး
    const h = HIT[i], pad = 8;
    if (Math.abs(p.x - h.cx) <= h.w / 2 + pad && Math.abs(p.y - h.cy) <= h.h / 2 + pad) return h;
  }
  return null;
}
function dragStart(p) {
  const h = hitTest(p); if (!h) return false;
  drag = { key: h.key, dx: p.x - h.cx, dy: p.y - h.cy, w: h.w, h: h.h };
  return true;
}
function dragMove(p) {
  const W = cv.width, H = cv.height;
  if (drag.key === "blur") {   // မှုန်ဝါးအကွက် နေရာရွှေ့
    const bw = BLUR.w * W, bh = BLUR.h * H;
    BLUR.x = clamp(p.x - drag.dx - bw / 2, 0, W - bw) / W; BLUR.y = clamp(p.y - drag.dy - bh / 2, 0, H - bh) / H;
    return;
  }
  if (drag.key === "blurR") {  // မှုန်ဝါးအကွက် အရွယ်ပြောင်း (ဘယ်ထိပ်ထောင့် တည်ငြိမ်)
    const right = p.x - drag.dx + BLUR_HS / 2, bottom = p.y - drag.dy + BLUR_HS / 2;
    BLUR.w = clamp((right - BLUR.x * W) / W, 0.04, 1 - BLUR.x); BLUR.h = clamp((bottom - BLUR.y * H) / H, 0.03, 1 - BLUR.y);
    return;
  }
  const hw = Math.min(drag.w / 2, W / 2), hh = Math.min(drag.h / 2, H / 2);
  const cx = Math.min(W - hw, Math.max(hw, p.x - drag.dx)), cy = Math.min(H - hh, Math.max(hh, p.y - drag.dy));
  POS[drag.key] = { x: cx / W, y: cy / H };
  $(POS_SELECT[drag.key]).value = "custom";
}
cv.addEventListener("mousedown", (e) => { if (dragStart(canvasPoint(e))) { cv.style.cursor = "grabbing"; e.preventDefault(); } });
cv.addEventListener("mousemove", (e) => {
  if (drag) return;
  const h = hitTest(canvasPoint(e)); HOVER = h ? h.key : null; cv.style.cursor = h ? (h.key === "blurR" ? "nwse-resize" : "grab") : "default";
});
cv.addEventListener("mouseleave", () => { if (!drag) HOVER = null; });
window.addEventListener("mousemove", (e) => { if (drag) dragMove(canvasPoint(e)); });
window.addEventListener("mouseup", () => { if (drag) { drag = null; cv.style.cursor = "default"; } });
cv.addEventListener("touchstart", (e) => { if (e.touches.length === 1 && dragStart(canvasPoint(e.touches[0]))) e.preventDefault(); }, { passive: false });
cv.addEventListener("touchmove", (e) => { if (drag) { e.preventDefault(); dragMove(canvasPoint(e.touches[0])); } }, { passive: false });
["touchend", "touchcancel"].forEach((ev) => cv.addEventListener(ev, () => { drag = null; }));

/* ---------- နောက်ခံ (ဗီဒီယို / gradient) + အရောင် + အောက်ခြေအရိပ် ---------- */
function hexA(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }
function drawBackground(W, H) {
  const hasVideo = pvVideo.readyState >= 2 && pvVideo.videoWidth;
  if (hasVideo) {
    const vw = pvVideo.videoWidth, vh = pvVideo.videoHeight, g = $("gradeOn").checked;
    const flip = $("autoEdit").checked && $("flipSpeed").checked, zoom = $("autoEdit").checked && $("zoom").checked ? 1.06 : 1;
    const gfilter = g ? `brightness(${1 + $("gradeBr").value / 100}) contrast(${$("gradeCt").value / 100}) saturate(${$("gradeSa").value / 100})` : "none";
    ctx.save();
    if (flip) { ctx.translate(W, 0); ctx.scale(-1, 1); }
    const cover = Math.max(W / vw, H / vh);   // နောက်ခံ: ပြည့်အောင်ဆန့် + မှောင်စေသည်
    ctx.filter = gfilter;
    ctx.drawImage(pvVideo, (W - vw * cover) / 2, (H - vh * cover) / 2, vw * cover, vh * cover);
    ctx.filter = "none";
    ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fillRect(0, 0, W, H);
    const fit = Math.min(W / vw, H / vh) * zoom;   // အရှေ့: အချိုးမပျက်ဆန့်သည်
    ctx.beginPath(); ctx.rect(0, 0, W, H); ctx.clip();
    ctx.filter = gfilter;
    ctx.drawImage(pvVideo, (W - vw * fit) / 2, (H - vh * fit) / 2, vw * fit, vh * fit);
    ctx.filter = "none";
    ctx.restore();
    if (g) {
      if (+$("gradeTintOp").value > 0) { ctx.fillStyle = hexA($("gradeTint").value, $("gradeTintOp").value / 100); ctx.fillRect(0, 0, W, H); }
      if (+$("gradeVg").value > 0) {
        const vg = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.25, W / 2, H / 2, Math.max(W, H) * 0.75);
        vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, `rgba(0,0,0,${$("gradeVg").value / 100 * 0.8})`);
        ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);
      }
    }
  } else {   // ဗီဒီယိုမပါလျှင် Background ရွေးချယ်မှုအတိုင်း gradient နောက်ခံ
    const map = { purple: ["#1b1540", "#4a0a52"], blue: ["#0b1d3a", "#0a5f7a"], sunset: ["#2b1055", "#d1457a"], dark: ["#0d0f14", "#20242f"], light: ["#e8ebf2", "#aab4c8"] };
    const c = map[$("vizBg").value] || map.purple;
    const g = ctx.createLinearGradient(0, 0, W, H); g.addColorStop(0, c[0]); g.addColorStop(1, c[1]);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }
  if (hasVideo && $("shadeOn").checked) {   // အောက်ခြေအရိပ်
    const a = SH.mode === "custom" ? +$("shadeOpacity").value / 100 : 0.55;
    const col = $("shadeColor").value;
    const g2 = ctx.createLinearGradient(0, H * 0.72, 0, H); g2.addColorStop(0, hexA(col, 0)); g2.addColorStop(1, hexA(col, a));
    ctx.fillStyle = g2; ctx.fillRect(0, H * 0.72, W, H * 0.28);
  }
  return hasVideo;
}

function bandsOf(src, n) {   // spectrum ကို log အပိုင်း n ခုသို့ ခွဲသည်
  const out = new Array(n), lo = 2, hi = Math.min(src.length - 1, 380);
  for (let b = 0; b < n; b++) {
    const a = Math.floor(lo * Math.pow(hi / lo, b / n)), z = Math.max(a + 1, Math.floor(lo * Math.pow(hi / lo, (b + 1) / n)));
    let s = 0; for (let i = a; i < z; i++) s += src[i];
    out[b] = Math.min(1, (s / (z - a)) / 255 * 1.15);
  }
  return out;
}
function drawShape(W, H, wave, freq, hasVideo) {
  if (!$("vizOn").checked) return;
  const style = $("vizStyle").value, color = $("vizColor").value, portrait = H >= W;
  if (style === "none") return;   // None ရွေးထားလျှင် shape မဆွဲ
  let sw, sh;
  if (["scope", "scope_polar", "ring"].includes(style)) { sw = sh = Math.min(W, H) * (portrait ? 0.40 : 0.42); }
  else if (style === "volume") { sw = W * 0.86; sh = H * (portrait ? 0.07 : 0.10); }
  else { sw = W * 0.86; sh = H * (portrait ? 0.16 : 0.28); }
  const pad = Math.max(8, 0.15 * Math.min(sw, sh)), pw = sw + 2 * pad, ph = sh + 2 * pad;
  const pos = $("vizPos").value;
  const top = pos === "top" ? H * 0.08 : pos === "bottom" ? H - ph - H * 0.14 : (H - ph) / 2;
  const px = (W - pw) / 2, cx = W / 2, cy = top + ph / 2;
  const bg = $("vizBg").value;
  if (hasVideo && bg !== "none") {   // ဗီဒီယိုပေါ်တွင် shape နောက်ကွယ်က panel
    const cols = { purple: ["#1b1540", "#4a0a52"], blue: ["#0b1d3a", "#0a5f7a"], sunset: ["#2b1055", "#d1457a"] };
    if (bg === "dark") ctx.fillStyle = "rgba(0,0,0,.45)";
    else if (bg === "light") ctx.fillStyle = "rgba(255,255,255,.30)";
    else { const g = ctx.createLinearGradient(px, top, px + pw, top + ph); g.addColorStop(0, hexA(cols[bg][0], 0.62)); g.addColorStop(1, hexA(cols[bg][1], 0.62)); ctx.fillStyle = g; }
    ctx.fillRect(px, top, pw, ph);
  }
  const x0 = cx - sw / 2, y0 = cy - sh / 2;
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = 2;
  if (style === "ring") {
    const n = 64, a = bandsOf(freq, n / 2), base = sw * 0.20 * 1.1, ext = sw * 0.27;
    ctx.lineWidth = Math.max(2, sw * 0.012);
    ctx.beginPath(); ctx.arc(cx, cy, base - 4, 0, Math.PI * 2); ctx.stroke();
    for (let i = 0; i < n; i++) {
      const idx = i < n / 2 ? i : n - 1 - i, ang = (i / n) * Math.PI * 2 - Math.PI / 2, len = 2 + a[idx] * ext;
      ctx.beginPath(); ctx.moveTo(cx + Math.cos(ang) * base, cy + Math.sin(ang) * base);
      ctx.lineTo(cx + Math.cos(ang) * (base + len), cy + Math.sin(ang) * (base + len)); ctx.stroke();
    }
  } else if (style === "wave") {
    ctx.beginPath();
    for (let x = 0; x < sw; x += 2) {
      const v = Math.abs(wave[Math.floor((x / sw) * (wave.length - 1))] - 128) / 128, a = Math.max(1, v * sh * 0.8);
      ctx.moveTo(x0 + x, cy - a); ctx.lineTo(x0 + x, cy + a);
    }
    ctx.stroke();
  } else if (style === "wave_line") {
    ctx.beginPath();
    for (let x = 0; x <= sw; x += 2) {
      const v = (wave[Math.floor((x / sw) * (wave.length - 1))] - 128) / 128, y = cy + v * sh * 0.5;
      if (x === 0) ctx.moveTo(x0 + x, y); else ctx.lineTo(x0 + x, y);
    }
    ctx.stroke();
  } else if (style === "bars") {
    const n = 40, a = bandsOf(freq, n), bw = sw / n;
    for (let i = 0; i < n; i++) { const h = Math.max(2, a[i] * sh); ctx.fillRect(x0 + i * bw + bw * 0.15, y0 + sh - h, bw * 0.7, h); }
  } else if (style === "spectrum_line") {
    const n = 64, a = bandsOf(freq, n); ctx.beginPath();
    for (let i = 0; i < n; i++) { const x = x0 + (i / (n - 1)) * sw, y = y0 + sh - a[i] * sh; if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
    ctx.stroke();
  } else if (style === "dots") {
    const n = 40, a = bandsOf(freq, n), bw = sw / n;
    for (let i = 0; i < n; i++) { ctx.beginPath(); ctx.arc(x0 + i * bw + bw / 2, y0 + sh - a[i] * sh, Math.max(2, bw * 0.22), 0, Math.PI * 2); ctx.fill(); }
  } else if (style === "scope") {   // Lissajous (vector shape)
    ctx.lineWidth = 1.2; ctx.globalAlpha = 0.85; ctx.beginPath();
    for (let i = 0; i < 700; i++) {
      const x = (wave[i] - 128) / 128, y = (wave[(i + 24) % wave.length] - 128) / 128;
      const qx = cx + x * sw * 0.55, qy = cy + y * sw * 0.55;
      if (i === 0) ctx.moveTo(qx, qy); else ctx.lineTo(qx, qy);
    }
    ctx.stroke(); ctx.globalAlpha = 1;
  } else if (style === "scope_polar") {   // စက်ဝိုင်းပုံ waveform
    ctx.lineWidth = 1.5; ctx.globalAlpha = 0.9; ctx.beginPath();
    const N = 360;
    for (let i = 0; i <= N; i++) {
      const v = (wave[Math.floor((i / N) * (wave.length - 1))] - 128) / 128, ang = (i / N) * Math.PI * 2, r = sw * (0.28 + v * 0.22);
      const qx = cx + Math.cos(ang) * r, qy = cy + Math.sin(ang) * r;
      if (i === 0) ctx.moveTo(qx, qy); else ctx.lineTo(qx, qy);
    }
    ctx.stroke(); ctx.globalAlpha = 1;
  } else if (style === "volume") {
    let s = 0; for (let i = 0; i < wave.length; i++) { const v = (wave[i] - 128) / 128; s += v * v; }
    const rms = Math.min(1, Math.sqrt(s / wave.length) * 3.2);
    ctx.fillRect(x0, y0, sw * rms, sh); ctx.globalAlpha = 0.25; ctx.fillRect(x0, y0, sw, sh); ctx.globalAlpha = 1;
  }
}

function frame(t) {
  requestAnimationFrame(frame);
  const W = cv.width, H = cv.height;
  HIT = [];
  const hasVideo = drawBackground(W, H);
  drawBlur(W, H, hasVideo);
  drawLogoImage(W, H);
  const rv = $("resVideo");
  const playing = analyser && !rv.paused && !rv.ended && rv.getAttribute("src");
  let wave, freq;
  if (playing) { analyser.getByteTimeDomainData(waveBuf); analyser.getByteFrequencyData(freqBuf); wave = waveBuf; freq = freqBuf; }
  else {   // အသံမဖွင့်ထားချိန် သရုပ်ပြလှုပ်ရှားမှု
    wave = new Uint8Array(1024); freq = new Uint8Array(512);
    for (let i = 0; i < 1024; i++) wave[i] = 128 + 30 * Math.sin(i * 0.045 + t / 260) * Math.sin(i * 0.004 + t / 900);
    for (let i = 0; i < 512; i++) freq[i] = Math.max(0, 80 + 70 * Math.sin(t / 700 + i * 0.09) * Math.exp(-i / 240));
  }
  drawShape(W, H, wave, freq, hasVideo);
  // စာတန်းထိုး: ရလဒ်ဗီဒီယို ဖွင့်နေလျှင် လက်ရှိစာတန်း၊ မဟုတ်လျှင် နမူနာစာတန်း
  let text = "နမူနာ စာတန်းထိုး · Sample subtitle";
  if (CAPS.length && rv.getAttribute("src") && (playing || rv.currentTime > 0)) {
    const tt = rv.currentTime, c = CAPS.find((x) => tt >= x.start && tt <= x.end);
    text = c ? c.text : "";
  }
  if (text) drawSubtitle(W, H, text);
  drawTextLogo(W, H);
  drawHandles();
}
