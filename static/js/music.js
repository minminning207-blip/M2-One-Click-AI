/* Background music - search Openverse (through our server), preview, pick a track */

const MUSIC = { id: "", title: "", creator: "", license: "", q: "", page: 1, pages: 1 };
const MUSIC_ITEMS = {};          // id -> track (so Play can use its preview url)
let MUSIC_AUDIO = null, MUSIC_PLAYING = "";

const musicMsg = (m) => { $("musicMsg").innerHTML = m ? `<div class="err">${esc(m)}</div>` : ""; };
const musicLic = (t) => (t.license === "cc0" ? "CC0" : t.license === "pdm" ? "Public Domain" : `CC ${t.license.toUpperCase()} ${t.license_version || ""}`.trim());
const musicDur = (sec) => (sec ? `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}` : "");

function musicStop() {
  if (MUSIC_AUDIO) { MUSIC_AUDIO.pause(); MUSIC_AUDIO = null; }
  MUSIC_PLAYING = "";
  document.querySelectorAll("#musicList [data-play]").forEach((b) => { b.textContent = "▶ Play"; });
}

function musicRow(t) {
  const sel = MUSIC.id === t.id;
  return `<div class="music-item${sel ? " sel" : ""}" data-id="${esc(t.id)}">
    <button type="button" class="btn-sm" data-play="${esc(t.id)}">▶ Play</button>
    <div class="mi">
      <div class="mt" title="${esc(t.title)}">${esc(t.title)}</div>
      <div class="ms">${esc(t.creator)} · ${esc(musicDur(t.duration))}<span class="badge">${esc(musicLic(t))}</span></div>
    </div>
    <button type="button" class="btn-sm" data-use="${esc(t.id)}">${sel ? "✔ Selected" : "Use"}</button>
  </div>`;
}

function musicRefreshPicked() {
  const box = $("musicPicked");
  box.classList.toggle("hidden", !MUSIC.id);
  if (MUSIC.id) box.textContent = `Selected: "${MUSIC.title}" by ${MUSIC.creator} · ${MUSIC.license}`;
  document.querySelectorAll("#musicList .music-item").forEach((row) => {
    const on = row.dataset.id === MUSIC.id;
    row.classList.toggle("sel", on);
    row.querySelector("[data-use]").textContent = on ? "✔ Selected" : "Use";
  });
}

async function musicSearch(page) {
  const q = $("musicQ").value.trim();
  if (page === 1) { MUSIC.q = q; musicStop(); $("musicList").innerHTML = ""; }
  musicMsg("");
  $("musicSearch").disabled = true; $("musicMore").disabled = true;
  $("musicList").insertAdjacentHTML("beforeend", '<div class="hint" id="musicLoading"><i class="spin"></i> Searching...</div>');
  try {
    const res = await fetch(`/api/music/search?q=${encodeURIComponent(MUSIC.q)}&page=${page}`);
    let d = null; try { d = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(errMsg(d, res));
    MUSIC.page = d.page; MUSIC.pages = d.page_count;
    d.results.forEach((t) => { MUSIC_ITEMS[t.id] = t; });
    $("musicList").insertAdjacentHTML("beforeend", d.results.map(musicRow).join(""));
    if (page === 1 && !d.results.length) musicMsg("No tracks found. Try another word (for example: calm, piano, epic, lofi).");
    $("musicMoreBox").classList.toggle("hidden", !(MUSIC.page < Math.min(MUSIC.pages, 12) && d.results.length));
  } catch (e) {
    musicMsg(e.message);
  } finally {
    const l = $("musicLoading"); if (l) l.remove();
    $("musicSearch").disabled = false; $("musicMore").disabled = false;
  }
}

$("musicSearch").addEventListener("click", () => musicSearch(1));
$("musicQ").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); musicSearch(1); } });
$("musicMore").addEventListener("click", () => musicSearch(MUSIC.page + 1));

$("musicList").addEventListener("click", (e) => {
  const p = e.target.closest("[data-play]"), u = e.target.closest("[data-use]");
  if (p) {
    const id = p.dataset.play, wasPlaying = MUSIC_PLAYING === id;
    musicStop();
    if (wasPlaying || !MUSIC_ITEMS[id]) return;
    MUSIC_AUDIO = new Audio(MUSIC_ITEMS[id].url);
    MUSIC_AUDIO.volume = 0.8;
    MUSIC_AUDIO.onended = musicStop;
    MUSIC_AUDIO.onerror = () => { musicStop(); musicMsg("This preview could not be played. You can still try Use, or pick another track."); };
    MUSIC_AUDIO.play().catch(() => {});
    MUSIC_PLAYING = id; p.textContent = "❚❚ Stop";
  } else if (u) {
    const t = MUSIC_ITEMS[u.dataset.use]; if (!t) return;
    MUSIC.id = t.id; MUSIC.title = t.title; MUSIC.creator = t.creator; MUSIC.license = musicLic(t);
    musicRefreshPicked();
  }
});

bindToggle("musicOn", "musicPanel");
bindRange("musicVol");
$("musicOn").addEventListener("change", () => { if (!$("musicOn").checked) musicStop(); });

function getMusic() {       // sent to the server inside settings.music (only the track id, never a URL)
  const on = $("musicOn").checked && !!MUSIC.id;
  return { enabled: on, id: on ? MUSIC.id : "", volume: +$("musicVol").value, duck: $("musicDuck").checked };
}

/* show the panel only if the server has music enabled */
fetch("/api/music/status").then((r) => (r.ok ? r.json() : null)).then((d) => {
  if (d && d.enabled) $("musicBox").classList.remove("hidden");
}).catch(() => {});
