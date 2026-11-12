/* Praça · núcleo: utilidades, API, tempo real (SSE), modais, autenticação (visitante/membro), casca e rotas */
const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const VER = '<span class="ver" title="Verificado">✔</span>';
const isMobile = () => matchMedia("(max-width:760px)").matches;
const av = (n, cls = "") => `<div class="av ${cls}">${esc((n || "?")[0]).toUpperCase()}</div>`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mmss = s => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s % 60).padStart(2, "0"); };
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "agora"; if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) return Math.floor(s / 3600) + " h"; return Math.floor(s / 86400) + " d";
}
function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

let me = null, cfg = {}, rtc = {};
const bus = {};
const on = (ev, fn) => ((bus[ev] ||= []).push(fn), fn);
const off = (ev, fn) => { bus[ev] = (bus[ev] || []).filter(f => f !== fn); };
const emit = (ev, d) => (bus[ev] || []).slice().forEach(f => { try { f(d); } catch (e) { console.error(ev, e); } });
let pageCleanups = [];
const pageOn = (ev, fn) => { on(ev, fn); pageCleanups.push(() => off(ev, fn)); };

function toast(msg, kind = "") {
  let box = $("#toasts"); if (!box) { box = h('<div id="toasts" role="status" aria-live="polite"></div>'); document.body.append(box); }
  const t = h(`<div class="toast ${kind}"></div>`); t.textContent = msg; box.append(t); setTimeout(() => t.remove(), 4200);
}

/* ---------------- API ---------------- */
async function api(path, method = "GET", body, cap, opt = {}) {
  const r = await fetch("/api" + path, {method, headers: {...(body && !(body instanceof FormData) ? {"Content-Type": "application/json"} : {}), ...(cap ? {"X-Captcha": cap} : {})}, body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined});
  const d = await r.json().catch(() => ({}));
  if (r.status === 403 && d.error === "banned") { if (me) kicked(d.message); throw d; }
  if (r.status === 401) { if (opt.quiet) throw d; if (me) { me = null; boot(); } else authModal(); throw d; }
  if (r.status === 428) { const a = prompt("Verificação anti-bot: quanto é " + d.question + "?"); if (a) return api(path, method, body, a.trim(), opt); throw d; }
  if (!r.ok) throw d;
  return d;
}
const apiQ = (p, m, b) => api(p, m, b, null, {quiet: true});
const needMember = () => { if (me) return true; authModal(null, "reg", "Crie sua conta para interagir na Praça."); return false; };

function kicked(msg) {
  me = null; es?.close(); emit("shutdown");
  $("#root").innerHTML = ""; authModal(msg || "Sua sessão foi encerrada.", "login", null, true);
}

/* ---------------- tempo real ---------------- */
let es, wasLive = false;
const EVENTS = ["hello", "feed.new", "post.deleted", "post.likes", "chat.msg", "media.ready", "call.incoming", "call.cancelled", "call.state", "call.signal", "call.ended",
  "call.answered_elsewhere", "rooms.changed", "room.roster", "room.ended", "room.kicked", "room.invited", "room.hand", "room.invite_speaker", "room.forced_mute",
  "room.react", "room.chat", "room.signal", "auth.revoked"];
function connectEvents() {
  es?.close(); es = new EventSource("/api/events");
  EVENTS.forEach(n => es.addEventListener(n, e => emit(n, JSON.parse(e.data))));
  es.onopen = () => setLive(true); es.onerror = () => setLive(false);
}
function setLive(v) { $$(".live").forEach(d => d.classList.toggle("on", v)); if (v && wasLive) emit("reconnected"); wasLive = true; }
on("auth.revoked", () => kicked("Sua conta foi suspensa ou banida pela moderação."));

/* ---------------- modais ---------------- */
let modalStack = [];
function openModal(html, {full = false, onClose} = {}) {
  let ov = $("#overlay");
  if (!ov) { ov = h('<div id="overlay" role="dialog" aria-modal="true"></div>'); document.body.append(ov); }
  const m = h(`<div class="modal ${full ? "full" : ""}"><button class="close" aria-label="Fechar">×</button>${html}</div>`);
  ov.innerHTML = ""; ov.append(m);
  const close = () => { ov.remove(); modalStack = []; onClose && onClose(); };
  m.querySelector(".close").onclick = close;
  ov.onmousedown = e => { if (e.target === ov) close(); };
  m.close = close; modalStack = [m]; return m;
}
const closeModal = () => modalStack[0]?.close();
addEventListener("keydown", e => { if (e.key === "Escape" && modalStack[0] && !$("#viewer")) closeModal(); });

/* ---------------- login / cadastro (modal; visitantes navegam sem conta) ---------------- */
function authModal(msg, mode = "login", _x, hard) {
  let m = "login";
  const mo = openModal(`<h2>Praça</h2><div class="mut">Converse, poste, ligue e participe das Mesas de Debate.</div>
   <div class="tabsf" style="margin-top:12px"><button id="tl" class="on">Entrar</button><button id="tr">Criar conta</button></div>
   <div style="display:grid;gap:10px"><input id="u" placeholder="Usuário (letras, números e _)" autocomplete="username" autocapitalize="none">
   <input id="d" placeholder="Nome de exibição" style="display:none"><input id="p" type="password" placeholder="Senha (mín. 6)" autocomplete="current-password">
   <div class="err" id="e">${esc(msg || "")}</div><button class="btn" id="go">Entrar</button></div>`, {onClose: () => { }});
  if (hard) { mo.querySelector(".close").hidden = true; $("#overlay").onmousedown = null; }
  const set = x => { m = x; $("#tl").className = x === "login" ? "on" : ""; $("#tr").className = x === "reg" ? "on" : ""; $("#d").style.display = x === "reg" ? "" : "none"; $("#go").textContent = x === "reg" ? "Criar conta" : "Entrar"; };
  $("#tl").onclick = () => set("login"); $("#tr").onclick = () => set("reg"); set(mode);
  const submit = async () => {
    try { await api(m === "reg" ? "/register" : "/login", "POST", {username: $("#u").value, password: $("#p").value, display: $("#d").value}, null, {quiet: true}); mo.close(); boot(); }
    catch (e) { $("#e").textContent = e.message || e.error || "Erro"; }
  };
  $("#go").onclick = submit; $("#p").onkeydown = e => { if (e.key === "Enter") submit(); }; $("#u").focus();
}

/* ---------------- casca do app ---------------- */
async function boot() {
  try { cfg = await (await fetch("/api/limits")).json(); } catch { }
  try { me = await apiQ("/me"); } catch { me = null; }
  try { rtc = await (await fetch("/api/rtc-config")).json(); } catch { rtc = {iceServers: []}; }
  const nav = me ? `
    <a href="#/feed" data-v="feed"><span class="ico">🏠</span>Início</a>
    <a href="#/search" data-v="search" class="only-d"><span class="ico">🔍</span>Buscar</a>
    <a href="#/rooms" data-v="rooms"><span class="ico">🎙</span>Mesas</a>
    <a href="#/new" class="cta" id="newbtn"><span class="ico">＋</span><span>Postar</span></a>
    <a href="#/chats" data-v="chats"><span class="ico">✉️</span>Mensagens<span class="badge" id="ub" hidden></span></a>
    <a href="#/u/${me.username}" data-v="u"><span class="ico">👤</span>Perfil</a>
    <a href="#/privacy" data-v="privacy" class="only-d"><span class="ico">🔒</span>Privacidade</a>
    ${me.role !== "member" ? '<a href="/admin" class="only-d" target="_blank"><span class="ico">🛡</span>Moderação</a>' : ""}
    <button id="out" class="only-d"><span class="ico">↩</span>Sair</button>` : `
    <a href="#/feed" data-v="feed"><span class="ico">🏠</span>Início</a>
    <a href="#/rooms" data-v="rooms"><span class="ico">🎙</span>Mesas</a>
    <a href="#/trending" data-v="trending"><span class="ico">📍</span>Locais</a>
    <button id="login1" class="cta"><span class="ico">→</span><span>Entrar</span></button>`;
  $("#root").innerHTML = `<div id="topbar"><b>Praça</b><span><span class="live" title="Tempo real"></span>${me ? '<button id="tsearch" aria-label="Buscar">🔍</button><button id="tpriv" aria-label="Privacidade">🔒</button>' : '<button class="btn sm" id="login2">Entrar</button>'}</span></div>
   <div id="app"><nav aria-label="Principal"><div class="logo">Praça <span class="live" title="Conectado em tempo real"></span></div>${nav}</nav><main id="main"></main><aside id="side"></aside></div>`;
  if (me) {
    $("#out").onclick = async () => { await apiQ("/logout", "POST").catch(() => { }); location.hash = "#/feed"; me = null; es?.close(); boot(); };
    $("#tsearch").onclick = () => location.hash = "#/search"; $("#tpriv").onclick = () => location.hash = "#/privacy";
    $("#newbtn").onclick = e => { e.preventDefault(); openComposer(); };
  } else { $("#login1").onclick = () => authModal(); $("#login2").onclick = () => authModal(); }
  connectEvents();
  if (me && !cfg._u) { cfg._u = 1; on("chat.msg", unread); }
  if (me) unread();
  sideWidgets();
  route();
}
async function unread() {
  if (!me) return;
  try { const c = await apiQ("/chats"), n = c.reduce((a, x) => a + x.unread, 0), b = $("#ub"); if (b) { b.hidden = !n; b.textContent = n; } } catch { }
}
let sideTimer;
function sideWidgets() {
  const draw = async () => {
    const box = $("#side"); if (!box) return;
    const [rooms, places] = await Promise.all([fetch("/api/rooms").then(r => r.json()).catch(() => []), fetch("/api/places/trending").then(r => r.json()).catch(() => [])]);
    box.innerHTML = `<div class="card"><div class="row sp"><b>🎙 Mesas ao vivo</b><a class="mut" href="#/rooms">ver todas</a></div>
      ${rooms.slice(0, 3).map(r => `<a class="item" href="#/rooms" style="margin-top:10px">${av(r.host.display, "sm")}<div class="t"><b>${esc(r.title)}</b><span class="mut"><span class="lvtag">● AO VIVO</span> ${r.speaker_count + r.listeners} ouvindo</span></div></a>`).join("") || '<div class="mut" style="margin-top:8px">Nenhuma Mesa agora. Que tal abrir uma?</div>'}</div>
      <div class="card"><b>📍 Esquinas em alta</b>${places.map(p => `<a class="item" href="#/local/${p.id}" style="margin-top:10px"><div class="t"><b>${esc(p.name)}</b><span class="mut">${p.n} post(s) esta semana</span></div></a>`).join("") || '<div class="mut" style="margin-top:8px">Marque um local no seu próximo post.</div>'}</div>`;
  };
  draw(); on("rooms.changed", draw); clearInterval(sideTimer); sideTimer = setInterval(draw, 60000);
}

/* ---------------- rotas ---------------- */
const views = {};
function route() {
  pageCleanups.forEach(f => f()); pageCleanups = [];
  const [, v = "feed", a] = location.hash.split("/");
  $$("nav a[data-v]").forEach(x => x.classList.toggle("on", x.dataset.v === (v === "chat" ? "chats" : v === "local" ? "trending" : v) && (v !== "u" || !me || decodeURIComponent(a || "") === me.username)));
  scrollTo(0, 0);
  if (v === "new") { history.replaceState(null, "", "#/feed"); return route(); }
  (views[v] || views.feed)(a && decodeURIComponent(a));
}
addEventListener("hashchange", () => { if (me !== undefined) route(); });
