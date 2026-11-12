/* Praça · ao vivo: malha WebRTC, chamadas de voz/vídeo (1:1 e grupo até 20) e Mesas de Debate */
const sig = (path, to, data) => api(path, "POST", {to, data}).catch(() => { });

/* ---------- áudio: medidor de fala, toques ---------- */
let AC; const actx = () => (AC ||= new (window.AudioContext || window.webkitAudioContext)());
function watchLevel(stream, cb) {
  if (!stream.getAudioTracks().length) return () => { };
  let an, buf, on_ = false, t;
  try { const c = actx(), src = c.createMediaStreamSource(stream); an = c.createAnalyser(); an.fftSize = 512; src.connect(an); buf = new Uint8Array(an.fftSize); } catch { return () => { }; }
  t = setInterval(() => { an.getByteTimeDomainData(buf); let s = 0; for (const v of buf) s += (v - 128) ** 2; const sp = Math.sqrt(s / buf.length) > 6; if (sp !== on_) { on_ = sp; cb(sp); } }, 160);
  return () => clearInterval(t);
}
function tone(kind) {   // "ring" (receber) | "back" (chamando)
  let stop = false, timer;
  const beep = () => { try { const c = actx(); c.resume(); [kind === "ring" ? [440, 480] : [425]][0].forEach(f => { const o = c.createOscillator(), g = c.createGain(); o.frequency.value = f; g.gain.value = .06; o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + (kind === "ring" ? .9 : .8)); }); } catch { } };
  const loop = () => { if (stop) return; beep(); kind === "ring" && navigator.vibrate?.([500, 250, 500]); timer = setTimeout(loop, kind === "ring" ? 2600 : 3200); };
  loop(); return () => { stop = true; clearTimeout(timer); navigator.vibrate?.(0); };
}

/* ---------- malha de conexões P2P ---------- */
class Mesh {
  constructor(o) { Object.assign(this, o); this.peers = new Map(); this.pend = new Map(); this.readySent = new Map(); this.local = this.local || {audio: null, video: null}; }
  mk(user, sg, answering) {
    const pc = new RTCPeerConnection({iceServers: rtc.iceServers || []});
    const p = {user, pc, sig: sg, stream: new MediaStream(), remote: false, t: {}};
    if (!answering) {      // quem oferta cria os transceptores; quem atende reaproveita os da oferta (senão a mídia não sai)
      p.t.audio = pc.addTransceiver("audio", {direction: "sendrecv"});
      if (this.video) p.t.video = pc.addTransceiver("video", {direction: "sendrecv"});
      for (const k of Object.keys(p.t)) if (this.local[k]) p.t[k].sender.replaceTrack(this.local[k]).catch(() => { });
    }
    pc.onicecandidate = e => e.candidate && this.send(user, {t: "ice", c: e.candidate, sig: sg});
    pc.ontrack = e => { p.stream.addTrack(e.track); this.onStream(user, p.stream); };
    let dc; pc.onconnectionstatechange = () => {
      clearTimeout(dc);
      if (pc.connectionState === "failed") this.fail(user, p);
      else if (pc.connectionState === "disconnected") dc = setTimeout(() => this.peers.get(user) === p && pc.connectionState !== "connected" && this.fail(user, p), 8000);
    };
    this.peers.set(user, p); this.tune?.(); return p;
  }
  fail(user, p) { if (this.peers.get(user) !== p) return; this.close(user); this.readySent.delete(user); if (this.offerer(user)) this.sync(this.last || []); else this.send(user, {t: "retry", sig: p.sig}); }
  async offer(p) {
    try { const o = await p.pc.createOffer(); await p.pc.setLocalDescription(o); this.send(p.user, {t: "offer", sdp: p.pc.localDescription.sdp, sig: p.sig}); } catch (e) { console.warn("offer", e); }
  }
  sync(wanted) {
    this.last = wanted;
    for (const u of [...this.peers.keys()]) if (!wanted.includes(u)) this.close(u);
    for (const u of wanted) {
      const sg = this.sigOf(u); let p = this.peers.get(u);
      if (p && p.sig !== sg) { this.close(u); p = null; }
      if (this.offerer(u)) { if (!p) this.offer(this.mk(u, sg)); }
      else if (this.readySent.get(u) !== sg) { this.readySent.set(u, sg); this.send(u, {t: "ready", sig: sg}); }
    }
  }
  async signal(from, d) {
    if (!this.last?.includes(from)) { (this.pend.get(from) || this.pend.set(from, []).get(from)).push(d); return; }
    const expect = this.sigOf(from);
    if (d.sig !== expect) return;                       // sinal de uma versão antiga
    let p = this.peers.get(from);
    if (d.t === "ready" || d.t === "retry") { if (this.offerer(from) && (!p || d.t === "retry")) { if (p) this.close(from); this.offer(this.mk(from, expect)); } return; }
    if (d.t === "offer") {
      if (p) this.close(from); p = this.mk(from, expect, true);
      await p.pc.setRemoteDescription({type: "offer", sdp: d.sdp}); p.remote = true;
      for (const tr of p.pc.getTransceivers()) { const k = tr.receiver.track.kind; p.t[k] = tr; tr.direction = "sendrecv"; if (this.local[k]) await tr.sender.replaceTrack(this.local[k]).catch(() => { }); }
      this.tune?.();
      await p.pc.setLocalDescription(await p.pc.createAnswer()); this.send(from, {t: "answer", sdp: p.pc.localDescription.sdp, sig: expect});
    } else if (d.t === "answer" && p && !p.remote) { await p.pc.setRemoteDescription({type: "answer", sdp: d.sdp}); p.remote = true; }
    else if (d.t === "ice" && p) { try { if (p.remote) await p.pc.addIceCandidate(d.c); else (p.q ||= []).push(d.c); } catch { } }
    if (p?.remote && p.q) { for (const c of p.q.splice(0)) try { await p.pc.addIceCandidate(c); } catch { } }
  }
  flush() { for (const [u, l] of this.pend) { this.pend.delete(u); l.forEach(d => this.signal(u, d)); } }
  close(u) { const p = this.peers.get(u); if (!p) return; this.peers.delete(u); try { p.pc.close(); } catch { } this.onGone?.(u); }
  closeAll() { [...this.peers.keys()].forEach(u => this.close(u)); }
  setTrack(kind, track) { this.local[kind] = track; for (const p of this.peers.values()) p.t[kind]?.sender.replaceTrack(track).catch(() => { }); }
}

async function getLocal(video) {
  const opt = {audio: {echoCancellation: true, noiseSuppression: true}, video: video ? {facingMode: "user", width: {ideal: 1280}, height: {ideal: 720}} : false};
  let st = null;
  try { st = await navigator.mediaDevices.getUserMedia(opt); }
  catch (e) { if (video) { try { st = await navigator.mediaDevices.getUserMedia({audio: opt.audio}); toast("Sem acesso à câmera: entrando só com áudio.", "err"); } catch { } } }
  if (!st) { if (!confirm("Não consegui acessar o microfone (permissão negada ou sem dispositivo). Entrar mesmo assim, só para ouvir?")) return null; return {audio: null, video: null}; }
  return {audio: st.getAudioTracks()[0] || null, video: st.getVideoTracks()[0] || null};
}
const stopLocal = l => l && [l.audio, l.video].forEach(t => t && t.stop());
let wake = null; const keepAwake = async on_ => { try { if (on_) wake = await navigator.wakeLock?.request("screen"); else { await wake?.release(); wake = null; } } catch { } };
const END_MSG = {declined: "A chamada foi recusada.", no_answer: "Ninguém atendeu.", missed: "Ninguém atendeu.", ended: "Chamada encerrada.", left: "Você saiu da chamada.", max_duration: "A chamada atingiu o limite de 4 horas.", timeout: "Sem resposta."};

/* =============================== CHAMADAS =============================== */
let CALL = null, INC = null;
async function startCall(names, video) {
  if (!needMember()) return;
  if (CALL || ROOM) return toast("Você já está em uma chamada ou Mesa.", "err");
  const local = await getLocal(video); if (!local) return;
  try { const s = await api("/calls", "POST", {to: names, video}); openCall(s, local); }
  catch (e) { stopLocal(local); toast(e.error || "Não foi possível ligar.", "err"); }
}
function openCall(snap, local) {
  const C = CALL = {id: snap.id, snap, local, video: snap.video, tiles: {}, streams: {}, timers: [], stops: {}, pinned: null, facing: "user", camTrack: local.video, sharing: false, mini: false};
  const el = C.el = h(`<div class="fs" role="dialog" aria-label="Chamada"><div class="top"><div><b id="ct"></b><div class="mut" id="cs"></div></div><button class="ic" id="mn" title="Minimizar (Esc)" aria-label="Minimizar">▾</button></div>
    <div class="vgrid" id="vg"></div><div class="ctl"><button class="ic" id="bm" title="Microfone (M)" aria-label="Microfone">🎤</button><button class="ic" id="bc" title="Câmera (V)" aria-label="Câmera">📷</button>
    <button class="ic" id="bf" title="Inverter câmera" aria-label="Inverter câmera" hidden>🔄</button><button class="ic" id="bs" title="Compartilhar tela (S)" aria-label="Compartilhar tela" hidden>🖥</button>
    <button class="ic" id="ba" title="Adicionar pessoas" aria-label="Adicionar pessoas">➕</button><button class="ic end" id="be" title="Encerrar" aria-label="Encerrar chamada">📞</button></div></div>`);
  document.body.append(el); keepAwake(true);
  C.mesh = new Mesh({video: true, local: {audio: local.audio, video: local.video}, send: (to, d) => sig(`/calls/${C.id}/signal`, to, d), sigOf: () => "c", offerer: u => me.username < u,
    onStream: (u, s) => { C.streams[u] = s; attach(u); C.stops[u]?.(); C.stops[u] = watchLevel(s, v => C.tiles[u]?.classList.toggle("speaking", v)); },
    onGone: u => { C.stops[u]?.(); },
    tune: () => { const n = C.mesh.peers.size + 1, bps = n <= 2 ? 1.5e6 : n <= 4 ? 7e5 : n <= 8 ? 3e5 : 1.2e5, sc = n <= 4 ? 1 : n <= 8 ? 2 : 4;
      C.mesh.peers.forEach(p => { const s = p.t.video?.sender; if (!s) return; const pr = s.getParameters(); if (!pr.encodings?.length) pr.encodings = [{}]; pr.encodings[0].maxBitrate = bps; pr.encodings[0].scaleResolutionDownBy = sc; s.setParameters(pr).catch(() => { }); }); }});
  if (local.audio) C.stops[me.username] = watchLevel(new MediaStream([local.audio]), v => C.tiles[me.username]?.classList.toggle("speaking", v));
  navigator.mediaDevices?.enumerateDevices?.().then(d => { $("#bf", el).hidden = !(("ontouchstart" in window) && d.filter(x => x.kind === "videoinput").length > 1); });
  $("#bs", el).hidden = !navigator.mediaDevices?.getDisplayMedia || "ontouchstart" in window;
  const st = () => api(`/calls/${C.id}/state`, "POST", {muted: !C.local.audio?.enabled, cam: !!(C.local.video && C.local.video.enabled), screen: C.sharing}).catch(() => { });
  const paint = () => { $("#bm", el).classList.toggle("off", !C.local.audio || !C.local.audio.enabled); $("#bc", el).classList.toggle("off", !(C.local.video && C.local.video.enabled)); $("#bs", el).classList.toggle("off", C.sharing); };
  $("#bm", el).onclick = C.toggleMic = () => { if (!C.local.audio) return toast("Sem microfone neste aparelho.", "err"); C.local.audio.enabled = !C.local.audio.enabled; paint(); st(); render(); };
  $("#bc", el).onclick = C.toggleCam = async () => {
    if (!C.local.video) { try { const s = await navigator.mediaDevices.getUserMedia({video: {facingMode: C.facing, width: {ideal: 1280}, height: {ideal: 720}}}); C.camTrack = s.getVideoTracks()[0]; C.local.video = C.camTrack; C.mesh.setTrack("video", C.camTrack); } catch { return toast("Não consegui acessar a câmera.", "err"); } }
    else C.local.video.enabled = !C.local.video.enabled;
    paint(); st(); render();
  };
  $("#bf", el).onclick = async () => {
    try { C.facing = C.facing === "user" ? "environment" : "user"; const s = await navigator.mediaDevices.getUserMedia({video: {facingMode: C.facing}}), t = s.getVideoTracks()[0]; C.camTrack?.stop(); C.camTrack = t; C.local.video = t; C.mesh.setTrack("video", t); render(); }
    catch { toast("Não foi possível inverter a câmera.", "err"); }
  };
  $("#bs", el).onclick = C.toggleShare = async () => {
    if (C.sharing) return stopShare();
    try { const s = await navigator.mediaDevices.getDisplayMedia({video: true}), t = s.getVideoTracks()[0]; C.sharing = true; C.screen = t; C.local.video = t; C.mesh.setTrack("video", t); t.onended = stopShare; paint(); st(); render(); }
    catch (e) { if (e.name !== "NotAllowedError") toast("Não foi possível compartilhar a tela.", "err"); }
  };
  const stopShare = () => { if (!C.sharing) return; C.sharing = false; C.screen?.stop(); C.local.video = C.camTrack?.readyState === "live" ? C.camTrack : null; C.mesh.setTrack("video", C.local.video); paint(); st(); render(); };
  $("#ba", el).onclick = () => inviteDlg();
  $("#be", el).onclick = () => hangup();
  $("#mn", el).onclick = C.minimize = () => { C.mini = !C.mini; el.hidden = C.mini; pillSync(); };
  const render = C.render = () => {
    const parts = C.snap.parts.filter(p => ["joined", "ringing"].includes(p.state)), grid = $("#vg", el);
    const joined = parts.filter(p => p.state === "joined"), pip = parts.length === 2 && joined.length === 2, others = parts.filter(p => p.username !== me.username);
    $("#ct", el).textContent = (C.snap.video ? "Chamada de vídeo" : "Chamada de voz") + " · " + (parts.length > 2 ? parts.length + " pessoas" : others[0] ? "@" + others[0].username : "");
    const names = new Set(parts.map(p => p.username));
    for (const u of Object.keys(C.tiles)) if (!names.has(u)) { C.tiles[u].remove(); delete C.tiles[u]; }
    for (const p of parts) {
      let t = C.tiles[p.username]; const mine = p.username === me.username;
      if (!t) {
        t = C.tiles[p.username] = h(`<div class="tile ${mine ? "local" : ""}" data-u="${esc(p.username)}"><video autoplay playsinline ${mine ? "muted" : ""}></video><div class="av">${esc(p.display[0]).toUpperCase()}</div><span class="nm"></span><span class="st"></span><span class="tag" hidden>🖥 Compartilhando tela</span></div>`);
        t.onclick = () => { C.pinned = C.pinned === p.username ? null : p.username; render(); }; attach(p.username);
      }
      const camOn = mine ? !!(C.local.video && C.local.video.enabled) : p.cam;
      t.classList.toggle("nocam", !camOn); t.classList.toggle("state-ringing", p.state === "ringing"); t.classList.toggle("screen", mine ? C.sharing : p.screen); t.classList.toggle("back", mine && C.facing === "environment");
      $(".nm", t).textContent = mine ? "Você" : p.display; $(".st", t).textContent = (p.muted || (mine && !C.local.audio?.enabled) ? "🔇" : "") + (p.state === "ringing" ? " Chamando…" : "");
      $(".tag", t).hidden = !(mine ? C.sharing : p.screen);
      t.classList.toggle("pip", pip && mine);
    }
    grid.dataset.n = parts.length; grid.style.setProperty("--cols", parts.length <= 1 ? 1 : parts.length <= 4 ? 2 : parts.length <= 9 ? 3 : parts.length <= 16 ? 4 : 5);
    const order = parts.map(p => C.tiles[p.username]);
    if (C.pinned && names.has(C.pinned) && parts.length > 2) {
      grid.classList.add("spot"); grid.innerHTML = '<div class="stagebox"></div><div class="strip"></div>';
      order.forEach(t => (t.dataset.u === C.pinned ? $(".stagebox", grid) : $(".strip", grid)).append(t));
    } else { C.pinned = null; grid.classList.remove("spot"); grid.replaceChildren(...order); }
    if (pip) $(".tile.local", el)?.classList.add("pip");
    $$("video", grid).forEach(v => v.play?.().catch(() => { }));
    C.mesh.sync(joined.map(p => p.username).filter(u => u !== me.username)); C.mesh.flush();
    const ring = parts.some(p => p.state === "ringing") && joined.length === 1;
    if (ring && !C.back) C.back = tone("back"); if (!ring && C.back) { C.back(); C.back = null; }
    if (joined.length > 1 && !C.t0) { C.t0 = Date.now(); }
    $$(".fs .tile.pip").forEach(t => { t.style.position = "absolute"; });
  };
  function attach(u) { const t = C.tiles[u]; if (!t) return; const v = $("video", t), s = u === me.username ? (C.local.video ? new MediaStream([C.local.video]) : null) : C.streams[u]; if (v && s && v.srcObject !== s) v.srcObject = s; }
  const origRender = render;
  C.timers.push(setInterval(() => { $("#cs", el).textContent = C.t0 ? mmss((Date.now() - C.t0) / 1000) : "Chamando…"; pillSync(); }, 500));
  C.key = e => { if (e.target.closest("input,textarea") || e.ctrlKey || e.metaKey) return; if (e.key === "m") C.toggleMic(); else if (e.key === "v") C.toggleCam(); else if (e.key === "s") C.toggleShare?.(); else if (e.key === "Escape" && !modalStack[0]) C.minimize(); };
  addEventListener("keydown", C.key);
  paint(); render(); st();
  // streams chegam depois dos tiles; religa vídeo local quando a câmera muda
  C.timers.push(setInterval(() => { Object.keys(C.tiles).forEach(attach); }, 1500));
}
function pillSync() {
  let p = $("#callpill");
  const on_ = (CALL && CALL.mini) || (ROOM && ROOM.mini);
  if (!on_) return p?.remove();
  if (!p) { p = h('<button class="pill" id="callpill"></button>'); p.onclick = () => { const X = CALL?.mini ? CALL : ROOM; X.mini = false; X.el.hidden = false; pillSync(); }; document.body.append(p); }
  p.textContent = CALL?.mini ? "📞 Em chamada · " + (CALL.t0 ? mmss((Date.now() - CALL.t0) / 1000) : "chamando…") + " · voltar" : "🎙 Mesa ao vivo · voltar";
}
function endCall(reason, msg) {
  const C = CALL; if (!C) return; CALL = null;
  C.timers.forEach(clearInterval); C.back?.(); C.mesh.closeAll(); Object.values(C.stops).forEach(f => f && f());
  stopLocal(C.local); C.camTrack?.stop(); C.screen?.stop(); removeEventListener("keydown", C.key); C.el.remove(); keepAwake(false); pillSync();
  toast(msg || END_MSG[reason] || "Chamada encerrada.");
}
const hangup = async () => { const C = CALL; if (!C) return; await api(`/calls/${C.id}/leave`, "POST").catch(() => { }); endCall("left"); };
function inviteDlg() {
  const m = openModal('<h2>Adicionar à chamada</h2><input id="is" placeholder="Buscar pessoas…"><div id="ir" style="margin-top:8px;max-height:40vh;overflow:auto"></div>');
  let w; const go = async () => { const r = await api("/search?q=" + encodeURIComponent($("#is", m).value)); $("#ir", m).innerHTML = r.filter(u => u.username !== me.username).map(u => `<div class="item card" data-n="${u.username}" style="padding:8px;margin-bottom:4px">${av(u.display, "sm")}<div class="t"><b>${esc(u.display)}</b><span class="mut">@${u.username}</span></div></div>`).join("");
    $$("[data-n]", m).forEach(e => e.onclick = async () => { try { await api(`/calls/${CALL.id}/invite`, "POST", {to: [e.dataset.n]}); toast("Convite enviado a @" + e.dataset.n, "ok"); m.close(); } catch (er) { toast(er.error || "Erro", "err"); } }); };
  $("#is", m).oninput = () => { clearTimeout(w); w = setTimeout(go, 250); }; go();
}
on("call.state", s => { if (CALL && CALL.id === s.id) { const gone = s.parts.filter(p => CALL.snap.parts.find(o => o.username === p.username && o.state === "joined") && p.state !== "joined" && p.state !== "ringing"); CALL.snap = s; CALL.render(); gone.forEach(p => toast("@" + p.username + " saiu da chamada")); } });
on("call.signal", d => CALL && CALL.id === d.id && CALL.mesh.signal(d.from, d.data));
on("call.ended", d => { if (CALL && CALL.id === d.id) endCall(d.reason); });
on("call.incoming", d => {
  if (CALL || ROOM || INC) return;
  const ov = INC = h(`<div class="ring" role="alertdialog" aria-label="Chamada recebida">${av(d.from.display)}<div style="font-size:1.5rem;font-weight:800">${esc(d.from.display)}</div><div class="mut" style="color:#9aa5c7">${d.group ? "chamada em grupo" : "está te chamando"} · ${d.video ? "vídeo 🎥" : "voz 📞"}${d.others?.length ? "<br>com " + esc(d.others.join(", ")) : ""}</div>
    <div class="acts"><button style="background:var(--red)" id="rd" aria-label="Recusar">✕</button><button style="background:var(--ok)" id="ra" aria-label="Atender">${d.video ? "🎥" : "📞"}</button></div></div>`);
  document.body.append(ov); const stop = tone("ring"); let note;
  if (document.hidden && "Notification" in window && Notification.permission === "granted") note = new Notification("📞 " + d.from.display + " está te chamando", {tag: "call", requireInteraction: true});
  const t0 = document.title, bl = setInterval(() => document.title = document.title === t0 ? "📞 Chamada recebida!" : t0, 800);
  const done = () => { stop(); clearInterval(bl); document.title = t0; ov.remove(); note?.close(); INC = null; off("call.cancelled", cx); clearTimeout(to); };
  const cx = x => { if (x.id === d.id) { done(); toast("Chamada perdida de " + d.from.display); } }; on("call.cancelled", cx);
  const to = setTimeout(() => { if (INC === ov) { done(); } }, (rtc.ring_sec || 45) * 1000 + 1500);
  $("#rd", ov).onclick = () => { done(); api(`/calls/${d.id}/decline`, "POST").catch(() => { }); };
  $("#ra", ov).onclick = async () => { done(); const local = await getLocal(d.video); if (!local) return api(`/calls/${d.id}/decline`, "POST").catch(() => { });
    try { openCall(await api(`/calls/${d.id}/accept`, "POST"), local); } catch (e) { stopLocal(local); toast(e.error || "A chamada já terminou.", "err"); } };
});

/* =============================== MESAS DE DEBATE =============================== */
let ROOM = null;
views.rooms = async () => {
  const guest = !me;
  const draw = async () => {
    const l = await fetch("/api/rooms").then(r => r.json()).catch(() => []); if (!$("#rl")) return;
    $("#rl").innerHTML = l.map(r => `<div class="card room" data-id="${r.id}" tabindex="0" role="button"><div class="row sp"><span class="lvtag">● AO VIVO</span><span class="mut">${r.privacy === "public" ? "🌐 pública" : r.privacy === "followers" ? "👥 seguidores" : "✉️ convidados"}</span></div>
      <h3>${esc(r.title)}</h3><div class="row wrap">${r.tags.map(t => `<span class="chip">#${esc(t)}</span>`).join("")}</div>
      <div class="mut" style="margin-top:6px">🎙 ${r.speakers.map(esc).join(", ")}${r.speaker_count > r.speakers.length ? "…" : ""} · 🎧 ${r.listeners} ouvindo · por @${r.host.username}</div></div>`).join("") || '<div class="card empty">Nenhuma Mesa ao vivo agora.<br>Abra a primeira e chame a vizinhança pra conversar!</div>';
    $$(".room").forEach(c => { const go = () => me ? joinRoom(+c.dataset.id) : authModal(null, "reg", null); c.onclick = go; c.onkeydown = e => e.key === "Enter" && go(); });
  };
  $("#main").innerHTML = `<div class="row sp"><div class="head">🎙 Mesas de Debate</div>${guest ? "" : '<button class="btn" id="cr">＋ Criar Mesa</button>'}</div>${guest ? '<div class="banner">Visitantes veem as Mesas ao vivo. Crie uma conta para entrar e participar.</div>' : ""}<div id="rl"></div>`;
  if (!guest) $("#cr").onclick = createRoomDlg;
  pageOn("rooms.changed", draw); draw();
};
function createRoomDlg() {
  if (CALL || ROOM) return toast("Você já está em uma chamada ou Mesa.", "err");
  const tags = [];
  const m = openModal(`<h2>🎙 Nova Mesa de Debate</h2><label class="mut">Título</label><input id="rt" maxlength="80" placeholder="Ex.: O futuro da Praça Central" style="margin-bottom:8px">
   <label class="mut">Tags (Enter para adicionar, até 5)</label><input id="rg" placeholder="esporte, política, música…"><div id="rc" class="row wrap" style="margin:6px 0"></div>
   <label class="mut">Privacidade</label><div class="seg" id="rp" role="radiogroup" style="display:flex;margin-bottom:8px"><button role="radio" aria-checked="true" data-v="public">🌐 Pública</button><button role="radio" aria-checked="false" data-v="followers">👥 Seguidores</button><button role="radio" aria-checked="false" data-v="invite">✉️ Convidados</button></div>
   <input id="ri" placeholder="Convidar: @usuario1, @usuario2" hidden><div class="err" id="re"></div><button class="btn" id="rs" style="width:100%">Começar agora</button>`);
  let pv = "public"; const ch = () => { $("#rc", m).innerHTML = tags.map(t => `<span class="chip">#${esc(t)} <a data-x="${esc(t)}" style="cursor:pointer">✕</a></span>`).join(""); $$("[data-x]", m).forEach(a => a.onclick = () => { tags.splice(tags.indexOf(a.dataset.x), 1); ch(); }); };
  $("#rg", m).onkeydown = e => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); const t = e.target.value.replace(/[^\p{L}\p{N}_-]/gu, "").toLowerCase().slice(0, 20); if (t && tags.length < 5 && !tags.includes(t)) tags.push(t); e.target.value = ""; ch(); } };
  $$("#rp button", m).forEach(b => b.onclick = () => { pv = b.dataset.v; $$("#rp button", m).forEach(x => x.setAttribute("aria-checked", x === b)); $("#ri", m).hidden = pv !== "invite"; });
  $("#rs", m).onclick = async () => {
    const t = $("#rt", m).value.trim(); if (t.length < 3) return $("#re", m).textContent = "Dê um título à Mesa (mín. 3 caracteres).";
    const local = await getLocal(false); if (!local) return;
    try { const r = await api("/rooms", "POST", {title: t, tags, privacy: pv, invited: $("#ri", m).value.split(/[,\s]+/).map(s => s.replace("@", "")).filter(Boolean)}); m.close(); openRoom(Object.assign(r, {you: "host"}), local); }
    catch (e) { stopLocal(local); $("#re", m).textContent = e.error || "Erro ao criar."; }
  };
}
async function joinRoom(id) {
  if (!needMember()) return; if (CALL) return toast("Encerre a chamada antes de entrar na Mesa.", "err");
  if (ROOM) { if (ROOM.id === id) { ROOM.mini = false; ROOM.el.hidden = false; pillSync(); return; } return toast("Você já está em outra Mesa.", "err"); }
  try { const r = await api(`/rooms/${id}/join`, "POST"); openRoom(r, {audio: null, video: null}); } catch (e) { toast(e.error || "Não foi possível entrar.", "err"); }
}
const EMO = ["👏", "❤️", "😂", "🔥", "👍", "😮"];
async function openRoom(snap, local) {
  const R = ROOM = {id: snap.id, snap, role: snap.you, mic: null, stops: {}, speaking: new Set(), mini: false, chat: snap.chat || [], hand: false, audios: {}, timers: []};
  const el = R.el = h(`<div class="fs" role="dialog" aria-label="Mesa de Debate"><div class="top"><div><b id="rt"></b><div class="mut" id="rs"></div></div><button class="ic" id="mn" aria-label="Minimizar" title="Minimizar">▾</button></div>
    <div class="rwrap"><div style="flex:1;min-width:0;display:flex;flex-direction:column"><div class="rbody"><div id="hq"></div><div class="stage-h">🎙 Palco de oradores</div><div class="spk" id="sp"></div><div class="stage-h">🎧 Plateia (<span id="lc">0</span>)</div><div class="aud" id="au"></div></div>
    <div class="ctl" id="cl"></div></div><div class="rchat" id="rch" hidden><div class="msgs" id="cm"></div><form id="cf"><input id="ci" maxlength="200" placeholder="Mensagem…" autocomplete="off"><button class="btn sm">↑</button></form></div></div><div id="unmute" class="hq" hidden><span>🔈 Toque para ativar o áudio da Mesa</span><button class="btn sm" id="um">Ativar</button></div></div>`);
  document.body.append(el); keepAwake(true);
  R.mesh = new Mesh({video: false, local: {audio: null}, send: (to, d) => sig(`/rooms/${R.id}/signal`, to, d),
    sigOf: u => { const mv = verOf(me.username), uv = verOf(u); return me.username < u ? mv + "." + uv : uv + "." + mv; },
    offerer: u => { const mS = R.role !== "listener", uS = roleOf(u) !== "listener"; return mS && !uS ? true : !mS && uS ? false : me.username < u; },
    onStream: (u, s) => { let a = R.audios[u]; if (!a) { a = R.audios[u] = document.createElement("audio"); a.autoplay = true; el.append(a); } a.srcObject = s; a.play().catch(() => { $("#unmute", el).hidden = false; }); R.stops[u]?.(); R.stops[u] = watchLevel(s, v => { v ? R.speaking.add(u) : R.speaking.delete(u); mark(); }); },
    onGone: u => { R.stops[u]?.(); R.audios[u]?.remove(); delete R.audios[u]; R.speaking.delete(u); }});
  const verOf = u => (R.snap.speakers.find(x => x.username === u) || R.snap.listeners.find(x => x.username === u) || {ver: 1}).ver;
  const roleOf = u => R.snap.speakers.find(x => x.username === u)?.role || "listener";
  const mine = () => R.snap.speakers.find(x => x.username === me.username);
  $("#um", el).onclick = () => { $$("audio", el).forEach(a => a.play()); $("#unmute", el).hidden = true; };
  const mark = () => $$(".ppl", el).forEach(p => p.classList.toggle("speaking", R.speaking.has(p.dataset.u)));
  const isHost = () => ["host", "cohost"].includes(R.role);
  async function micOn() {
    if (R.mic) return true;
    const l = await getLocal(false).catch(() => null); if (!l?.audio) return false;
    R.mic = l.audio; R.mic.enabled = false; R.mesh.local.audio = R.mic; R.stops[me.username] = watchLevel(new MediaStream([R.mic]), v => { v && R.mic.enabled ? R.speaking.add(me.username) : R.speaking.delete(me.username); mark(); }); return true;
  }
  const micOff = () => { if (R.mic) { R.mic.stop(); R.mic = null; R.mesh.local.audio = null; R.stops[me.username]?.(); } };
  const draw = R.draw = async () => {
    const s = R.snap, m = mine(); R.role = m ? m.role : "listener";
    if (R.role !== "listener" && !R.mic) { if (!(await micOn())) { toast("Sem acesso ao microfone: você voltou para a plateia.", "err"); api(`/rooms/${R.id}/demote`, "POST", {}).catch(() => { }); return; } }
    if (R.role === "listener" && R.mic) micOff();
    if (R.mic && m) R.mic.enabled = !m.muted;
    $("#rt", el).textContent = s.title; $("#rs", el).innerHTML = `<span class="lvtag" style="color:#ff8da4">● AO VIVO</span> ${s.tags.map(t => "#" + esc(t)).join(" ")} · ${s.speakers.length + s.listener_count} pessoas`;
    $("#sp", el).innerHTML = s.speakers.map(x => `<div class="ppl ${R.speaking.has(x.username) ? "speaking" : ""}" data-u="${esc(x.username)}" tabindex="0" role="button" aria-label="${esc(x.display)}">${av(x.display)}<span class="mic">${x.muted ? "🔇" : "🎤"}</span><div class="nm">${esc(x.display)}</div><div class="role">${{host: "Anfitrião", cohost: "Co-anfitrião", speaker: "Orador"}[x.role]}</div></div>`).join("");
    $("#lc", el).textContent = s.listener_count;
    $("#au", el).innerHTML = s.listeners.map(x => `<div class="ppl small" data-u="${esc(x.username)}" tabindex="0" role="button">${av(x.display)}${x.hand ? '<span class="hand">✋</span>' : ""}<div class="nm">${esc(x.display)}</div></div>`).join("");
    $("#hq", el).innerHTML = isHost() && s.hands.length ? `<div class="hq"><span>✋ ${s.hands.length} pedindo a palavra: <b>${s.hands.slice(0, 3).map(u => "@" + esc(u)).join(", ")}</b></span><button class="btn sm" id="hn">Chamar o 1º ao palco</button></div>` : "";
    if ($("#hn", el)) $("#hn", el).onclick = () => act("invite_speaker", {user: s.hands[0]});
    const sp = R.role !== "listener", muted = m?.muted !== false;
    $("#cl", el).innerHTML = `${sp ? `<button class="ic ${muted ? "off" : ""}" id="bm" title="Microfone (M)" aria-label="Microfone">${muted ? "🔇" : "🎤"}</button>` : `<button class="ic ${R.hand ? "off" : ""}" id="bh" title="Pedir a palavra (H)" aria-label="Pedir a palavra">✋</button>`}
      <button class="ic" id="br" aria-label="Reações">😀</button><button class="ic" id="bc" aria-label="Chat">💬</button>${sp && R.role !== "host" ? '<button class="ic" id="bd" title="Voltar para a plateia" aria-label="Voltar para a plateia">⬇</button>' : ""}
      ${R.role === "host" ? '<button class="btn danger" id="bx">Encerrar</button>' : ""}<button class="ic end" id="bl" title="Sair" aria-label="Sair da Mesa">🚪</button>`;
    if ($("#bm", el)) $("#bm", el).onclick = R.toggleMic = () => act("state", {muted: !muted});
    if ($("#bh", el)) $("#bh", el).onclick = R.toggleHand = () => { R.hand = !R.hand; act("hand", {up: R.hand}); draw(); };
    $("#br", el).onclick = e => { const b = e.currentTarget.getBoundingClientRect(); menu(b.left, b.top - 60, EMO.map(x => [x, () => act("react", {emoji: x})]), true); };
    $("#bc", el).onclick = () => { $("#rch", el).hidden = !$("#rch", el).hidden; $("#ci", el).focus(); };
    if ($("#bd", el)) $("#bd", el).onclick = () => act("demote", {});
    if ($("#bx", el)) $("#bx", el).onclick = () => confirm("Encerrar a Mesa para todos?") && act("end");
    $("#bl", el).onclick = () => leaveRoom();
    $("#mn", el).onclick = () => { R.mini = true; el.hidden = true; pillSync(); };
    $$(".ppl", el).forEach(p => { const go = e => personMenu(p.dataset.u, e); p.onclick = go; p.onkeydown = e => e.key === "Enter" && go(e); });
    $("#cm", el).innerHTML = R.chat.map(c => `<div><b>${esc(c.display)}</b> ${esc(c.text)}</div>`).join(""); $("#cm", el).scrollTop = 1e6;
    R.mesh.sync(wanted()); R.mesh.flush();
  };
  const wanted = () => { const me_s = R.role !== "listener"; return [...R.snap.speakers, ...R.snap.listeners].map(x => x.username).filter(u => u !== me.username && (me_s || roleOf(u) !== "listener")); };
  const act = (a, b = {}) => api(`/rooms/${R.id}/${a}`, "POST", b).catch(e => { toast(e.error || "Ação não permitida.", "err"); });
  function personMenu(u, ev) {
    const x = R.snap.speakers.find(s => s.username === u) || R.snap.listeners.find(s => s.username === u), r = roleOf(u), items = [["👤 Ver perfil", () => { R.mini = true; el.hidden = true; pillSync(); location.hash = "#/u/" + u; }]];
    if (isHost() && u !== me.username) {
      if (r === "listener") items.push(["🎙 Convidar ao palco", () => act("invite_speaker", {user: u})]);
      else if (r !== "host") { items.push(["🔇 Silenciar", () => act("mute", {user: u})], ["⬇ Mandar para a plateia", () => act("demote", {user: u})]); if (R.role === "host") items.push([r === "cohost" ? "Remover co-anfitrião" : "⭐ Tornar co-anfitrião", () => act("cohost", {user: u, on: r !== "cohost"})]); }
      if (r !== "host") items.push(["🚫 Remover da Mesa", () => confirm("Remover @" + u + "?") && act("kick", {user: u}), true]);
    }
    const b = ev.currentTarget.getBoundingClientRect(); menu(Math.min(b.left, innerWidth - 240), Math.min(b.bottom, innerHeight - items.length * 46 - 20), items);
  }
  function menu(x, y, items, row) {
    $(".menu")?.remove(); const m = h(`<div class="menu" style="left:${Math.max(8, x)}px;top:${Math.max(8, y)}px;${row ? "display:flex;min-width:0" : ""}">${items.map(([t, , d], i) => `<button data-i="${i}" class="${d ? "dg" : ""}">${t}</button>`).join("")}</div>`); document.body.append(m);
    $$("button", m).forEach(b => b.onclick = () => { m.remove(); items[+b.dataset.i][1](); }); setTimeout(() => addEventListener("pointerdown", function f(e) { if (!m.contains(e.target)) { m.remove(); removeEventListener("pointerdown", f); } }), 0);
  }
  $("#cf", el).onsubmit = e => { e.preventDefault(); const t = $("#ci", el).value.trim(); if (t) act("chat", {text: t}); $("#ci", el).value = ""; };
  R.key = e => { if (e.target.closest("input,textarea") || e.ctrlKey) return; if (e.key === "m" && R.toggleMic) R.toggleMic(); else if (e.key === "h" && R.toggleHand) R.toggleHand(); else if (e.key === "Escape" && !modalStack[0]) $("#mn", el).click(); };
  addEventListener("keydown", R.key);
  R.timers.push(setInterval(() => { }, 60000));
  await draw();
}
const leaveRoom = async () => { const R = ROOM; if (!R) return; await api(`/rooms/${R.id}/leave`, "POST").catch(() => { }); endRoom("Você saiu da Mesa."); };
function endRoom(msg) {
  const R = ROOM; if (!R) return; ROOM = null;
  R.timers.forEach(clearInterval); R.mesh.closeAll(); Object.values(R.stops).forEach(f => f && f()); R.mic?.stop(); removeEventListener("keydown", R.key); R.el.remove(); $(".menu")?.remove(); keepAwake(false); pillSync(); msg && toast(msg);
}
on("room.roster", s => { if (ROOM && ROOM.id === s.id) { ROOM.snap = s; ROOM.draw(); } });
on("room.signal", d => ROOM && ROOM.id === d.id && ROOM.mesh.signal(d.from, d.data));
on("room.ended", d => { if (ROOM && ROOM.id === d.id) endRoom(d.reason || "A Mesa foi encerrada."); });
on("room.kicked", d => { if (ROOM && ROOM.id === d.id) endRoom(d.reason || "Você foi removido da Mesa."); });
on("room.forced_mute", () => { toast("O anfitrião silenciou seu microfone."); });
on("room.hand", d => { if (ROOM && ["host", "cohost"].includes(ROOM.role)) toast("✋ " + d.display + " pediu a palavra"); });
on("room.chat", c => { if (ROOM) { ROOM.chat = [...ROOM.chat, c].slice(-100); const b = $("#cm", ROOM.el); if (b) { b.insertAdjacentHTML("beforeend", `<div><b>${esc(c.display)}</b> ${esc(c.text)}</div>`); b.scrollTop = 1e6; } } });
on("room.react", d => { if (ROOM && !ROOM.el.hidden) { const f = h(`<div class="fl" style="left:${20 + Math.random() * 60}%">${d.emoji}</div>`); ROOM.el.append(f); setTimeout(() => f.remove(), 2500); } });
on("room.invited", d => toast(`🎙 ${d.host.display} convidou você para a Mesa “${d.title}”`));
on("room.invite_speaker", d => {
  if (!ROOM || ROOM.id !== d.id) return;
  const m = openModal(`<h2>🎙 Subir ao palco?</h2><p>${esc(d.by)} convidou você para falar. Seu microfone será ligado só quando você tocar nele.</p><div class="row sp"><button class="btn ghost" id="n">Agora não</button><button class="btn" id="y">Subir ao palco</button></div>`);
  $("#n", m).onclick = () => { m.close(); api(`/rooms/${d.id}/decline_speaker`, "POST").catch(() => { }); ROOM && (ROOM.hand = false); ROOM?.draw(); };
  $("#y", m).onclick = async () => { m.close(); try { await api(`/rooms/${d.id}/accept_speaker`, "POST"); } catch (e) { toast(e.error || "Convite expirado.", "err"); } };
});

/* ---------- retomar estado após reconexão / recarga ---------- */
async function resync() {
  if (!me) return;
  let s; try { s = await api("/live-state"); } catch { return; }
  if (CALL && !s.call) endCall("ended", "A chamada terminou enquanto você estava sem conexão.");
  if (!CALL && s.call) api(`/calls/${s.call.id}/leave`, "POST").catch(() => { });      // recarregou a página: a mídia local se perdeu
  if (ROOM && !s.room) endRoom("Você não está mais na Mesa.");
  if (!ROOM && s.room) { try { const r = await api(`/rooms/${s.room.id}/join`, "POST"); openRoom(r, {audio: null}); } catch { } }
  if (!CALL && !ROOM && !INC && s.incoming?.length) emit("call.incoming", s.incoming[0]);
}
on("hello", d => { if (d.member) resync(); }); on("reconnected", resync);
addEventListener("pagehide", () => { if (CALL) navigator.sendBeacon(`/api/calls/${CALL.id}/leave`); if (ROOM) navigator.sendBeacon(`/api/rooms/${ROOM.id}/leave`); });
