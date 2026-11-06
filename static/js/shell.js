/* Sidebar၊ ကျွန်ုပ်၏ ဗီဒီယိုများ၊ Profile၊ Settings၊ Logout */

const SHELL = { user: null, retention: 48, view: "create" };
const fmtSize = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
const fmtDate = (t) => new Date(t * 1000).toLocaleString();
const fmtLeft = (h) => (h >= 1 ? `${Math.floor(h)} နာရီ ${Math.round((h % 1) * 60)} မိနစ်` : `${Math.max(1, Math.round(h * 60))} မိနစ်`);

/* ---------- API helper (JSON) ---------- */
async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  let data = null; try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(errMsg(data, res));
  return data;
}
const note = (id, msg, ok) => { $(id).innerHTML = msg ? `<div class="${ok ? "okmsg" : "err"}" style="margin-top:10px">${esc(msg)}</div>` : ""; };

/* ---------- ဘေးဘား (mobile drawer) ---------- */
function closeDrawer() { $("sidebar").classList.remove("open"); $("backdrop").classList.remove("show"); }
$("menuBtn").addEventListener("click", () => { $("sidebar").classList.toggle("open"); $("backdrop").classList.toggle("show"); });
$("backdrop").addEventListener("click", closeDrawer);

/* ---------- view ပြောင်းခြင်း ---------- */
function showView(name) {
  SHELL.view = name;
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + name));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  closeDrawer();
  window.scrollTo({ top: 0 });
  if (name === "videos") refreshVideos();
  if (name === "profile" || name === "settings") loadMe();
}
$("nav").addEventListener("click", (e) => { const b = e.target.closest("button[data-view]"); if (b) showView(b.dataset.view); });

/* ---------- ကိုယ်ရေးအချက်အလက် ---------- */
async function loadMe() {
  try {
    const d = await api("/api/auth/me");
    SHELL.user = d.user; SHELL.retention = d.retention_hours || 48;
    $("sbName").textContent = d.user.name || d.user.email.split("@")[0];
    $("sbEmail").textContent = d.user.email;
    $("pfEmail").innerHTML = esc(d.user.email) + (d.user.verified ? '<span class="badge">အတည်ပြုပြီး</span>' : '<span class="badge no">မအတည်ပြုရသေး</span>');
    if (document.activeElement !== $("pfName")) $("pfName").value = d.user.name || "";   // အသုံးပြုသူ ရိုက်နေစဉ် ပြန်မဖျက်ရေးရန်
    $("pfCreated").textContent = fmtDate(d.user.created);
    $("pfUsage").textContent = `${d.usage.count} ခု · ${fmtSize(d.usage.bytes)}`;
    $("retentionNote").textContent = `ထုတ်ထားသော ဗီဒီယိုများကို ${SHELL.retention} နာရီပြည့်လျှင် အလိုအလျောက် ဖျက်ပါသည်။ ဖျက်မခံရခင် ဒေါင်းလုဒ်လုပ်ထားပါ။`;
  } catch (_) {}
}
$("pfSave").addEventListener("click", async () => {
  try { await api("/api/auth/profile", "POST", { name: $("pfName").value }); note("pfMsg", "သိမ်းပြီးပါပြီ။", true); loadMe(); }
  catch (e) { note("pfMsg", e.message, false); }
});

/* ---------- Settings ---------- */
$("pwSave").addEventListener("click", async () => {
  if ($("pwNew").value !== $("pwNew2").value) return note("pwMsg", "Password အသစ် နှစ်ခု မတူပါ။", false);
  try {
    await api("/api/auth/password", "POST", { current: $("pwCur").value, new: $("pwNew").value });
    $("pwCur").value = $("pwNew").value = $("pwNew2").value = "";
    note("pwMsg", "Password ပြောင်းပြီးပါပြီ။ တခြားစက်များမှ Login ထွက်သွားပါမည်။", true);
  } catch (e) { note("pwMsg", e.message, false); }
});
$("delAcc").addEventListener("click", async () => {
  if (!$("delPw").value) return note("delMsg", "Password ရိုက်ထည့်ပါ။", false);
  if (!confirm("အကောင့်နှင့် ဗီဒီယိုအားလုံး အပြီးအပိုင် ပျက်သွားမည်။ သေချာပါသလား?")) return;
  try { await api("/api/auth/delete", "POST", { password: $("delPw").value }); location.href = "/auth.html"; }
  catch (e) { note("delMsg", e.message, false); }
});

/* ---------- Logout ---------- */
$("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", "POST"); } catch (_) {}
  location.href = "/auth.html";
});

/* ---------- ကျွန်ုပ်၏ ဗီဒီယိုများ ---------- */
function videoCard(v) {
  const dl = (url, label) => url ? `<a class="btn-sm" href="${esc(url)}" download>${label}</a>` : "";
  const post = v.post ? `
    <details class="vpost"><summary>📝 Title / Caption / Hashtag</summary>
      <textarea readonly style="min-height:150px;margin-top:8px" id="vp-${esc(v.id)}">${esc([v.post.title, v.post.caption, (v.post.hashtags || []).join(" ")].filter(Boolean).join("\n\n"))}</textarea>
      <button type="button" class="btn-sm" data-vcopy="vp-${esc(v.id)}" style="margin-top:8px">ကူးယူ</button>
    </details>` : "";
  return `
  <div class="vcard" data-id="${esc(v.id)}">
    <div class="vt">🎬 ${esc(v.title)}</div>
    <div class="vm">${esc(fmtDate(v.created))} · ${esc(fmtSize(v.size))}${v.duration ? " · " + fmtTime(v.duration) : ""}<br>
      <span class="exp">⏳ ${esc(fmtLeft(v.hours_left))} ကျန်သည် (ပြီးလျှင် အလိုအလျောက်ဖျက်မည်)</span></div>
    <div class="vactions">
      <button type="button" class="btn-sm" data-vplay="${esc(v.id)}">▶ ကြည့်ရန်</button>
      ${dl(v.video_url, "⬇ MP4")}${dl(v.audio_url, "⬇ MP3")}${dl(v.srt_url, "⬇ SRT")}${dl(v.poster_url, "⬇ Poster (jpg)")}
      <button type="button" class="btn-sm danger" data-vdel="${esc(v.id)}">🗑 ဖျက်ရန်</button>
    </div>
    <div class="vplayer"></div>
    ${post}
  </div>`;
}
let VIDEOS = [];
async function refreshVideos() {
  try {
    const d = await api("/api/videos");
    VIDEOS = d.videos || [];
    $("videosNote").textContent = `ထုတ်ထားသော ဗီဒီယိုများကို ${d.retention_hours} နာရီပြည့်လျှင် အလိုအလျောက် ဖျက်ပါသည်။ (${d.usage.count} ခု · ${fmtSize(d.usage.bytes)})`;
    $("videosList").innerHTML = VIDEOS.length ? VIDEOS.map(videoCard).join("") : '<div class="empty">ဗီဒီယို မရှိသေးပါ။ “ဗီဒီယိုအသစ် ပြုလုပ်ရန်” မှ စတင်ပါ။</div>';
  } catch (e) { $("videosList").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
window.refreshVideos = refreshVideos;
$("videosList").addEventListener("click", async (e) => {
  const play = e.target.closest("[data-vplay]"), del = e.target.closest("[data-vdel]"), cp = e.target.closest("[data-vcopy]");
  if (play) {
    const card = play.closest(".vcard"), box = card.querySelector(".vplayer"), v = VIDEOS.find((x) => x.id === play.dataset.vplay);
    if (box.firstChild) { box.firstChild.pause(); box.innerHTML = ""; play.textContent = "▶ ကြည့်ရန်"; return; }
    box.innerHTML = `<video controls playsinline autoplay src="${esc(v.video_url)}"></video>`; play.textContent = "⏹ ပိတ်ရန်";
  }
  if (del) {
    if (!confirm("ဒီဗီဒီယိုကို ဖျက်မည်လား? ပြန်မရနိုင်ပါ။")) return;
    try { await api("/api/videos/" + encodeURIComponent(del.dataset.vdel), "DELETE"); refreshVideos(); } catch (er) { toast(er.message); }
  }
  if (cp) copyText($(cp.dataset.vcopy).value, cp);
});

/* ---------- စတင်ချိန် ---------- */
loadMe();
