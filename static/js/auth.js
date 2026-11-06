/* Login / Register / Password reset စာမျက်နှာ */
const Q = new URLSearchParams(location.search);
const CFG = { recaptcha_site_key: "", signup_enabled: true, verification_required: true, mail_configured: true, google_enabled: false };
const WIDGETS = {};          // pane -> reCAPTCHA widget id
let PENDING_EMAIL = "";

const show = (m, ok) => { $("msg").innerHTML = m ? `<div class="${ok ? "okmsg" : "err"}">${esc(m)}</div>` : ""; };
function pane(name) {
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("hidden", p.id !== "form-" + name));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("tabs").classList.toggle("hidden", name === "forgot" || name === "reset");
  $("googleBox").classList.toggle("hidden", !CFG.google_enabled || name === "forgot" || name === "reset");
  show("");
  renderCaptcha(name);
}
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) pane(b.dataset.tab); });
document.addEventListener("click", (e) => { const b = e.target.closest("[data-goto]"); if (b) pane(b.dataset.goto); });

/* ---------- Google reCAPTCHA ---------- */
let CAPTCHA_READY = false;
window.onRecaptchaLoad = () => { CAPTCHA_READY = true; const cur = document.querySelector(".pane:not(.hidden)"); if (cur) renderCaptcha(cur.id.replace("form-", "")); };
function renderCaptcha(name) {
  const box = $("cap-" + name);
  if (!box || !CFG.recaptcha_site_key || !CAPTCHA_READY || WIDGETS[name] !== undefined || box.classList.contains("hidden")) return;
  WIDGETS[name] = grecaptcha.render(box, { sitekey: CFG.recaptcha_site_key, theme: "dark" });
}
const captchaToken = (name) => (CFG.recaptcha_site_key && WIDGETS[name] !== undefined ? grecaptcha.getResponse(WIDGETS[name]) : "");
const captchaReset = (name) => { if (CFG.recaptcha_site_key && WIDGETS[name] !== undefined) grecaptcha.reset(WIDGETS[name]); };

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null; try { data = await res.json(); } catch (_) {}
  if (!res.ok) { const e = new Error(errMsg(data, res)); e.code = data && data.code; throw e; }
  return data;
}
async function submit(btn, fn) {
  btn.disabled = true; show("");
  try { await fn(); } catch (e) { show(e.message, false); return e; } finally { btn.disabled = false; }
}

/* ---------- Google sign-in errors ---------- */
const GOOGLE_ERRORS = {
  google_off: "Google sign-in is not enabled on this server.",
  google_denied: "Google sign-in was cancelled or failed. Please try again.",
  google_failed: "Could not get your details from Google. Please try again.",
  google_unverified: "This Google account's email address is not verified by Google.",
  google_conflict: "This email is already linked to a different Google account.",
  signup_closed: "New account registration is temporarily closed.",
  rate_limited: "Too many attempts. Please wait a moment and try again.",
};

/* ---------- Login ---------- */
$("form-login").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!$("lgEmail").value.trim() || !$("lgPw").value) return show("Email နှင့် Password ရိုက်ထည့်ပါ။", false);
  submit($("lgBtn"), async () => {
    try {
      await post("/api/auth/login", { email: $("lgEmail").value, password: $("lgPw").value, captcha: captchaToken("login") });
      location.href = "/";
    } catch (er) {
      captchaReset("login");
      if (er.code === "captcha_required" && CFG.recaptcha_site_key) { $("cap-login").classList.remove("hidden"); renderCaptcha("login"); }
      if (er.code === "not_verified") { PENDING_EMAIL = $("lgEmail").value; $("resendBtn").classList.remove("hidden"); }
      throw er;
    }
  });
});
$("resendBtn").addEventListener("click", () => {
  if (CFG.recaptcha_site_key) { $("cap-login").classList.remove("hidden"); renderCaptcha("login"); }
  submit($("resendBtn"), async () => {
    await post("/api/auth/resend", { email: PENDING_EMAIL || $("lgEmail").value, captcha: captchaToken("login") });
    captchaReset("login");
    show("အတည်ပြု email ပို့ပြီးပါပြီ (အကောင့်ရှိပြီး မအတည်ပြုရသေးလျှင်)။ inbox နှင့် spam ကိုစစ်ပါ။", true);
  });
});

/* ---------- Register ---------- */
$("form-register").addEventListener("submit", (e) => {
  e.preventDefault();
  if ($("rgPw").value !== $("rgPw2").value) return show("Password နှစ်ခု မတူပါ။", false);
  submit($("rgBtn"), async () => {
    try {
      const r = await post("/api/auth/register", { name: $("rgName").value, email: $("rgEmail").value, password: $("rgPw").value, captcha: captchaToken("register") });
      captchaReset("register");
      PENDING_EMAIL = $("rgEmail").value;
      $("lgEmail").value = $("rgEmail").value;
      pane("login");
      show(r.verify_required
        ? "အကောင့်ဖွင့်ပြီးပါပြီ။ သင့် Email ထံ အတည်ပြု link ပို့ထားပါသည်။ inbox (spam ပါ) ကိုစစ်ပြီး link ကို နှိပ်ပါ။"
        : "အကောင့်ဖွင့်ပြီးပါပြီ။ Login ဝင်နိုင်ပါပြီ။", true);
    } catch (er) { captchaReset("register"); throw er; }
  });
});

/* ---------- Password မေ့ ---------- */
$("form-forgot").addEventListener("submit", (e) => {
  e.preventDefault();
  submit($("fgBtn"), async () => {
    try {
      await post("/api/auth/forgot", { email: $("fgEmail").value, captcha: captchaToken("forgot") });
      captchaReset("forgot");
      show("ထို Email ဖြင့် အကောင့်ရှိလျှင် Password ပြန်သတ်မှတ်ရန် link ကို ပို့ပြီးပါပြီ။ inbox (spam ပါ) ကိုစစ်ပါ။ (link သည် ၁ နာရီသာ သက်တမ်းရှိသည်)", true);
    } catch (er) { captchaReset("forgot"); throw er; }
  });
});

/* ---------- Password အသစ် ---------- */
$("form-reset").addEventListener("submit", (e) => {
  e.preventDefault();
  if ($("rsPw").value !== $("rsPw2").value) return show("Password နှစ်ခု မတူပါ။", false);
  submit($("rsBtn"), async () => {
    await post("/api/auth/reset", { token: Q.get("token") || "", password: $("rsPw").value });
    history.replaceState(null, "", "/auth.html");
    pane("login");
    show("Password ပြောင်းပြီးပါပြီ။ Password အသစ်ဖြင့် Login ဝင်ပါ။", true);
  });
});

/* ---------- စတင်ချိန် ---------- */
(async () => {
  try { Object.assign(CFG, await (await fetch("/api/auth/config")).json()); } catch (_) {}
  if (!CFG.signup_enabled) document.querySelector('[data-tab="register"]').classList.add("hidden");
  if (!CFG.mail_configured) {
    $("devNote").innerHTML = '<div class="warn">⚠ Email ပို့စနစ် (SMTP) မသတ်မှတ်ရသေးပါ (dev mode) - အတည်ပြု/Reset link များကို server ၏ terminal ထဲတွင် ပြပါမည်။</div>';
  }
  if (CFG.recaptcha_site_key) {
    $("cap-login").classList.add("hidden");           // Login မှားမှသာ CAPTCHA တောင်းသည်
    const s = document.createElement("script");
    s.src = "https://www.google.com/recaptcha/api.js?render=explicit&onload=onRecaptchaLoad"; s.async = true; s.defer = true;
    document.head.appendChild(s);
  } else {
    document.querySelectorAll(".captcha-box").forEach((b) => b.classList.add("hidden"));
  }
  if (Q.get("mode") === "reset" && Q.get("token")) pane("reset");
  else pane("login");
  if (Q.get("verified") === "1") show("Email အတည်ပြုပြီးပါပြီ။ Login ဝင်နိုင်ပါပြီ။", true);
  if (Q.get("verified") === "0") { show("အတည်ပြု link သက်တမ်းကုန်သွားပြီ (သို့) သုံးပြီးသားဖြစ်ပါသည်။ Login ဝင်ကြည့်ပါ၊ မရလျှင် 'အတည်ပြု email ပြန်ပို့ပါ' ကိုနှိပ်ပါ။", false); $("resendBtn").classList.remove("hidden"); }
  // Google login error (redirected back from /api/auth/google/callback)
  const gErr = Q.get("google_error");
  if (gErr) {
    show(GOOGLE_ERRORS[gErr] || "Google sign-in failed. Please try again.", false);
    history.replaceState(null, "", "/auth.html");
  }
  fetch("/api/auth/me").then((r) => { if (r.ok && !Q.get("mode")) location.href = "/"; }).catch(() => {});
})();
