/* Praça · páginas: feed, perfil, busca, mensagens, privacidade */
const POSTS = {};   // id -> mídia (para o visualizador)
const postHTML = p => {
  POSTS[p.id] = p.media;
  return `<article class="card post" data-pid="${p.id}"><a href="#/u/${p.username}">${av(p.display)}</a><div class="pb">
   <div class="ph"><a class="n" href="#/u/${p.username}">${esc(p.display)}${p.verified ? VER : ""}</a><span class="mut">@${p.username} · ${ago(p.created)}</span></div>
   ${p.place ? `<a class="place-tag" href="#/local/${p.place.id}">📍 ${esc(p.place.name)}</a>` : ""}
   ${p.text ? `<p>${esc(p.text)}</p>` : ""}${mediaBlock(p.media)}
   <div class="pa"><button data-act="like" class="${p.liked ? "on" : ""}" aria-label="Curtir">♥ <b>${p.likes}</b></button>
   ${p.mine ? '<button data-act="del">Excluir</button>' : '<button data-act="rep">Denunciar</button>'}</div></div></article>`;
};
const postList = (l, empty) => l.length ? l.map(postHTML).join("") : `<div class="empty">${empty || "Nada por aqui ainda."}</div>`;

/* lista paginada (rolagem infinita) */
function feedList(box, path, {empty, mode, live} = {}) {
  let last = null, done = false, busy = false;
  const base = () => (path.includes("?") ? path + "&" : path + "?") + (mode ? "mode=" + mode + "&" : "");
  box.innerHTML = '<div class="list"></div><div class="sent mut" style="text-align:center;padding:14px"></div>';
  const list = $(".list", box), sent = $(".sent", box);
  const load = async () => {
    if (busy || done) return; busy = true; sent.textContent = "Carregando…";
    try {
      const r = await (me ? api : (p) => fetch("/api" + p).then(x => x.json()))(base() + (last ? "before=" + last : ""));
      if (!Array.isArray(r)) throw r;
      if (!last && !r.length) list.innerHTML = `<div class="empty">${empty || "Nada por aqui ainda."}</div>`;
      else { list.insertAdjacentHTML("beforeend", r.map(postHTML).join("")); bindMedia(list, POSTS); }
      if (r.length) last = r[r.length - 1].id; if (r.length < 8) done = true;
      sent.textContent = done && last ? "Você chegou ao fim." : "";
    } catch (e) { sent.innerHTML = `<button class="btn ghost sm">Falha ao carregar. Tentar de novo</button>`; $("button", sent).onclick = load; }
    busy = false;
  };
  const io = new IntersectionObserver(es => es[0].isIntersecting && load(), {rootMargin: "600px"}); io.observe(sent);
  pageCleanups.push(() => io.disconnect());
  box.onclick = async e => {
    const b = e.target.closest("[data-act]"); if (!b) return; const pid = +b.closest("[data-pid]").dataset.pid;
    if (!needMember()) return;
    if (b.dataset.act === "like") { const r = await api(`/posts/${pid}/like`, "POST").catch(() => null); if (r) { b.classList.toggle("on", r.liked); $("b", b).textContent = r.likes; } }
    else if (b.dataset.act === "del") { if (confirm("Excluir este post?")) { await api("/posts/" + pid, "DELETE"); b.closest("article").remove(); } }
    else if (b.dataset.act === "rep") reportDlg("post", pid);
  };
  pageOn("post.likes", d => { const a = $(`[data-pid="${d.id}"] [data-act=like] b`, box); if (a) a.textContent = d.likes; });
  pageOn("post.deleted", d => $(`[data-pid="${d.id}"]`, box)?.remove());
  pageOn("media.ready", () => { });
  load(); return {reload: () => { last = null; done = false; load(); }};
}
function reportDlg(type, id) {
  const cats = [["odio", "Discurso de ódio"], ["assedio", "Assédio"], ["adulto", "Conteúdo adulto"], ["fake", "Fake news"], ["spam", "Spam ou golpe"], ["ameaca", "Ameaça"]];
  const m = openModal(`<h2>Denunciar</h2><div class="mut">A moderação vai analisar. Sua denúncia é confidencial.</div><div style="display:grid;gap:6px;margin:12px 0">${cats.map(([k, n], i) => `<label class="row card" style="margin:0;padding:10px"><input type="radio" name="rc" value="${k}" style="width:auto" ${i ? "" : "checked"}>${n}</label>`).join("")}</div>
    <input id="rr" maxlength="300" placeholder="Detalhes (opcional)"><div class="row sp" style="margin-top:12px"><button class="btn ghost" id="c">Cancelar</button><button class="btn danger" id="s">Enviar denúncia</button></div>`);
  $("#c", m).onclick = () => m.close();
  $("#s", m).onclick = async () => { try { await api("/report", "POST", {type, id, category: $("input[name=rc]:checked", m).value, reason: $("#rr", m).value}); m.close(); toast("Denúncia enviada. Obrigado!", "ok"); } catch (e) { toast(e.error || "Erro", "err"); } };
}

/* ---------------- feed ---------------- */
views.feed = () => {
  let mode = "all", L;
  const guest = !me;
  const banner = guest ? '<div class="banner"><span><b>Você está como visitante.</b> Dá para ler o feed, perfis, locais e as Mesas. Para postar, curtir e conversar, crie sua conta.</span><span class="row"><button class="btn sm" id="bi">Criar conta</button><button class="btn sm ghost" id="be">Entrar</button></span></div>' : "";
  const comp = me ? (isMobile() ? '<div class="card" id="fakec" style="color:var(--mut);cursor:text">O que está acontecendo na Praça?</div>' : '<div class="card" id="composer-host"></div>') : "";
  const tabs = me ? '<div class="tabsf"><button id="fa" class="on">Para você</button><button id="ff">Seguindo</button></div>' : "";
  $("#main").innerHTML = banner + comp + tabs + '<div id="newp" class="newpill" hidden><button class="btn">↑ <span></span> novo(s) post(s)</button></div><div id="list"></div>';
  if (guest) { $("#bi").onclick = () => authModal(null, "reg"); $("#be").onclick = () => authModal(); }
  if (me && !isMobile()) { const c = buildComposer($("#composer-host"), {onPosted: () => load()}); }
  if (me && isMobile()) $("#fakec").onclick = () => openComposer();
  const load = () => { $("#newp").hidden = true; newN = 0; L = feedList($("#list"), me ? "/feed" : "/public/feed", {mode: me ? mode : "", empty: mode === "following" ? "Siga pessoas para ver os posts delas aqui." : "A Praça está vazia. Publique o primeiro post!"}); };
  let newN = 0;
  if (me) { $("#fa").onclick = () => { mode = "all"; $("#fa").className = "on"; $("#ff").className = ""; load(); }; $("#ff").onclick = () => { mode = "following"; $("#ff").className = "on"; $("#fa").className = ""; load(); }; }
  pageOn("feed.new", d => { if (me && d.author === me.username) return; newN++; $("#newp").hidden = false; $("#newp span").textContent = newN; });
  $("#newp button").onclick = () => { load(); scrollTo({top: 0, behavior: "smooth"}); };
  load();
};

/* ---------------- perfil ---------------- */
views.u = async name => {
  let d;
  try { d = await (me ? api : p => fetch("/api" + p).then(r => r.ok ? r.json() : Promise.reject({error: "Perfil não encontrado."})))("/users/" + encodeURIComponent(name)); }
  catch (e) { return $("#main").innerHTML = `<div class="card empty">${esc(e.error || "Perfil não encontrado.")}</div>`; }
  const u = d.user, pm = d.perm;
  $("#main").innerHTML = `<div class="card"><div class="row sp wrap"><div class="row">${av(u.display, "big")}<div><div class="head" style="margin:0">${esc(u.display)}${u.verified ? VER : ""}</div><div class="mut">@${u.username}</div></div></div><div id="act" class="row wrap"></div></div>
   <p>${esc(u.bio) || '<span class="mut">Sem bio ainda.</span>'}</p><div class="row"><span><b>${u.followers}</b> seguidores</span><span><b>${u.following}</b> seguindo</span></div><div id="edit"></div></div>
   <div class="card" id="vis"></div><div id="lst"></div>`;
  const act = $("#act");
  if (d.me) {
    act.innerHTML = '<button class="btn ghost" id="ed">Editar perfil</button><a class="btn soft" href="#/privacy">🔒 Privacidade</a>';
    $("#ed").onclick = () => { $("#edit").innerHTML = `<input id="dn" value="${esc(u.display)}" maxlength="40" style="margin:8px 0"><textarea id="bi" maxlength="160" placeholder="Sua bio">${esc(u.bio)}</textarea><button class="btn" id="sv">Salvar</button>`; $("#sv").onclick = async () => { await api("/me", "PUT", {display: $("#dn").value, bio: $("#bi").value}); me = await api("/me"); route(); }; };
  } else {
    const dis = (ok, why) => ok ? "" : `disabled title="${why}"`;
    act.innerHTML = `<button class="btn ghost" id="msg">✉ Mensagem</button>
     <button class="btn soft" id="c1" ${dis(pm.call, "Esta pessoa limitou quem pode ligar")} aria-label="Ligar">📞</button><button class="btn soft" id="c2" ${dis(pm.call, "Esta pessoa limitou quem pode ligar")} aria-label="Videochamada">🎥</button>
     <button class="btn ${d.following ? "ghost" : ""}" id="fl">${d.following ? "Seguindo" : "Seguir"}</button>
     ${me ? `<button class="btn soft" id="cf" title="Amigos próximos veem o que você restringiu nos Cadeados">${d.close ? "★ Amigo próximo" : "☆ Amigo próximo"}</button>` : ""}`;
    $("#msg").onclick = () => needMember() && (location.hash = "#/chat/" + u.username);
    $("#c1").onclick = () => needMember() && startCall([u.username], false); $("#c2").onclick = () => needMember() && startCall([u.username], true);
    $("#fl").onclick = async () => { if (!needMember()) return; await api(`/users/${u.username}/follow`, "POST"); route(); };
    if ($("#cf")) $("#cf").onclick = async () => { await api(`/close-friends/${u.username}`, d.close ? "DELETE" : "POST"); toast(d.close ? "Removido dos amigos próximos" : "Adicionado aos amigos próximos", "ok"); route(); };
  }
  const pl = await fetch("/api/users/" + u.username + "/places", {credentials: "same-origin"}).then(r => r.json()).catch(() => ({items: []}));
  $("#vis").innerHTML = pl.hidden ? '<span class="mut">🔒 Esta pessoa mantém os locais visitados privados.</span>' : (pl.items.length ? `<b>📍 Locais visitados</b><div class="row wrap" style="margin-top:8px">${pl.items.map(p => `<a class="chip" href="#/local/${p.id}">${esc(p.name)} · ${p.n}</a>`).join("")}</div>` : '<span class="mut">📍 Nenhum local visitado ainda.</span>');
  feedList($("#lst"), `/users/${encodeURIComponent(u.username)}`, {empty: "Nenhum post ainda."}); // reutiliza o mesmo endpoint (posts vêm em .posts)
};
/* o endpoint de perfil devolve objeto; adaptamos feedList para ele */
const _fl = feedList;
feedList = function (box, path, o) { if (path.startsWith("/users/") && !path.endsWith("/places")) return profilePosts(box, path); return _fl(box, path, o); };
function profilePosts(box, path) {
  let last = null, done = false, busy = false;
  box.innerHTML = '<div class="list"></div><div class="sent mut" style="text-align:center;padding:14px"></div>';
  const list = $(".list", box), sent = $(".sent", box);
  const load = async () => {
    if (busy || done) return; busy = true;
    try { const r = await (me ? api : p => fetch("/api" + p).then(x => x.json()))(path + (last ? "?before=" + last : "")); const ps = r.posts || [];
      if (!last && !ps.length) list.innerHTML = '<div class="empty">Nenhum post ainda.</div>'; else { list.insertAdjacentHTML("beforeend", ps.map(postHTML).join("")); bindMedia(list, POSTS); }
      if (ps.length) last = ps[ps.length - 1].id; if (ps.length < 8) done = true; } catch { }
    busy = false;
  };
  const io = new IntersectionObserver(es => es[0].isIntersecting && load(), {rootMargin: "600px"}); io.observe(sent); pageCleanups.push(() => io.disconnect());
  box.onclick = async e => { const b = e.target.closest("[data-act]"); if (!b || !needMember()) return; const pid = +b.closest("[data-pid]").dataset.pid;
    if (b.dataset.act === "like") { const r = await api(`/posts/${pid}/like`, "POST").catch(() => null); if (r) { b.classList.toggle("on", r.liked); $("b", b).textContent = r.likes; } }
    else if (b.dataset.act === "del") { if (confirm("Excluir este post?")) { await api("/posts/" + pid, "DELETE"); b.closest("article").remove(); } } else reportDlg("post", pid); };
  load();
}

/* ---------------- busca ---------------- */
views.search = () => {
  if (!needMember()) return location.hash = "#/feed";
  $("#main").innerHTML = '<div class="head">Buscar pessoas</div><input id="s" placeholder="Nome ou @usuário" autofocus><div id="res" style="margin-top:12px"></div>';
  let w; const go = async () => {
    const r = await api("/search?q=" + encodeURIComponent($("#s").value));
    $("#res").innerHTML = r.length ? r.map(u => `<a class="card item" href="#/u/${u.username}">${av(u.display)}<div class="t"><b>${esc(u.display)}${u.verified ? VER : ""}</b><span class="mut">@${u.username} · ${u.followers} seguidores</span></div></a>`).join("") : '<div class="empty">Ninguém encontrado.</div>';
  };
  $("#s").oninput = () => { clearTimeout(w); w = setTimeout(go, 250); }; go();
};

/* ---------------- mensagens ---------------- */
views.chats = async () => {
  if (!needMember()) return location.hash = "#/feed";
  const draw = async () => {
    const c = await api("/chats");
    $("#main").innerHTML = `<div class="row sp"><div class="head">Mensagens</div><button class="btn soft sm" id="gc">👥 Chamada em grupo</button></div>` + (c.length ? c.map(x => `<a class="card item" href="#/chat/${x.username}">${av(x.display)}<div class="t"><b>${esc(x.display)}</b><span class="mut">${esc(x.text)}</span></div>${x.unread ? `<span class="badge">${x.unread}</span>` : ""}</a>`).join("") : '<div class="card empty">Nenhuma conversa. Abra um perfil e toque em Mensagem.</div>');
    $("#gc").onclick = () => groupCallDlg();
  };
  pageOn("chat.msg", draw); draw();
};
function groupCallDlg(preset = []) {
  const picked = new Set(preset); const cap = (rtc.call_max || 20) - 1;
  const m = openModal(`<h2>👥 Chamada em grupo</h2><div class="mut">Até ${cap + 1} pessoas no “banco de praça”. Só aparecem pessoas que aceitam suas chamadas.</div><input id="gs" placeholder="Buscar pessoas…" style="margin:10px 0"><div id="gr" style="max-height:34vh;overflow:auto"></div>
   <div id="gp" class="row wrap" style="margin:8px 0"></div><div class="row sp"><button class="btn soft" id="gv" disabled>📞 Voz</button><button class="btn" id="gx" disabled>🎥 Vídeo</button></div>`);
  const chips = () => { $("#gp", m).innerHTML = [...picked].map(n => `<span class="chip">@${esc(n)} <a data-x="${esc(n)}" style="cursor:pointer">✕</a></span>`).join(""); $$("[data-x]", m).forEach(a => a.onclick = () => { picked.delete(a.dataset.x); chips(); }); $("#gv", m).disabled = $("#gx", m).disabled = !picked.size; };
  let w; const go = async () => { const r = await api("/search?q=" + encodeURIComponent($("#gs", m).value)); $("#gr", m).innerHTML = r.filter(u => u.username !== me.username).map(u => `<div class="item card" data-n="${u.username}" style="padding:8px;margin-bottom:4px">${av(u.display, "sm")}<div class="t"><b>${esc(u.display)}</b><span class="mut">@${u.username}</span></div></div>`).join("");
    $$("[data-n]", m).forEach(e => e.onclick = () => { if (picked.size < cap) picked.add(e.dataset.n); chips(); }); };
  $("#gs", m).oninput = () => { clearTimeout(w); w = setTimeout(go, 250); };
  $("#gv", m).onclick = () => { m.close(); startCall([...picked], false); }; $("#gx", m).onclick = () => { m.close(); startCall([...picked], true); };
  chips(); go();
}
views.chat = async name => {
  if (!needMember()) return location.hash = "#/feed";
  $("#main").innerHTML = `<div class="chat"><div class="head row sp"><span><a href="#/chats">‹</a> <a href="#/u/${esc(name)}">@${esc(name)}</a></span><span class="row"><button class="btn soft sm" id="v1" aria-label="Ligar">📞</button><button class="btn soft sm" id="v2" aria-label="Videochamada">🎥</button><button class="btn soft sm" id="v3" aria-label="Adicionar pessoas">👥</button></span></div>
   <div id="msgs"></div><div class="err" id="ce"></div><div class="row" style="margin-top:6px"><button class="btn soft" id="ph" aria-label="Enviar foto">📷</button><input id="mt" maxlength="1000" placeholder="Escreva uma mensagem" autocomplete="off"><button class="btn" id="sd">Enviar</button></div><input type="file" id="pf" accept="image/*" hidden></div>`;
  $("#v1").onclick = () => startCall([name], false); $("#v2").onclick = () => startCall([name], true); $("#v3").onclick = () => groupCallDlg([name]);
  let last = 0;
  const pull = async () => {
    let m; try { m = await api(`/messages/${encodeURIComponent(name)}?after=${last}`); } catch { return; }
    if (!m.length) return; const box = $("#msgs"); if (!box) return;
    const bottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80 || !last;
    box.insertAdjacentHTML("beforeend", m.map(x => x.text.startsWith("📞") ? `<div class="m sys">${esc(x.text)} · ${ago(x.created)}</div>` : `<div class="m ${x.mine ? "me" : ""}">${x.media ? `<img src="${esc(x.media.thumb)}" data-full="${esc(x.media.url)}" alt="Foto">` : ""}${x.text ? esc(x.text) : ""}</div>`).join(""));
    $$("#msgs img[data-full]").forEach(i => i.onclick = () => openViewer([{url: i.dataset.full}]));
    last = m[m.length - 1].id; if (bottom) box.scrollTop = box.scrollHeight; unread();
  };
  const send = async (extra = {}) => {
    const t = $("#mt").value.trim(); if (!t && !extra.media_id) return; $("#mt").value = ""; $("#ce").textContent = "";
    try { await api("/messages/" + encodeURIComponent(name), "POST", {text: t, ...extra}); pull(); } catch (e) { $("#ce").textContent = e.error || "Erro ao enviar."; }
  };
  $("#sd").onclick = () => send(); $("#mt").onkeydown = e => { if (e.key === "Enter") send(); };
  $("#ph").onclick = () => $("#pf").click();
  $("#pf").onchange = async e => { const f = e.target.files[0]; e.target.value = ""; if (!f) return; $("#ce").textContent = "Enviando foto…";
    try { const r = await uploadFile(f).promise; await send({media_id: r.id}); $("#ce").textContent = ""; } catch (er) { $("#ce").textContent = er.error || "Falha ao enviar a foto."; } };
  pageOn("chat.msg", pull); await pull(); $("#mt").focus();
};

/* ---------------- Cadeados da Praça ---------------- */
views.privacy = async () => {
  if (!needMember()) return location.hash = "#/feed";
  const d = await api("/privacy");
  const LV = [["all", "Todos"], ["close", "Amigos próximos"], ["none", "Ninguém"]];
  const rows = [["photos", "📷 Quem pode me enviar fotos", "Controla fotos nas mensagens diretas."], ["calls", "📞 Quem pode iniciar chamadas de vídeo e voz comigo", "Inclui chamadas em grupo."], ["locations", "📍 Quem pode ver minhas localizações passadas", "Vale para o selo nos posts, seu perfil e o Feed do Local."]];
  $("#main").innerHTML = `<div class="head">🔒 Cadeados da Praça</div><div class="card">${rows.map(([k, t, s]) => `<div class="prow"><div><b>${t}</b><div class="mut">${s}</div></div><div class="seg" role="radiogroup" aria-label="${t}" style="margin-top:8px" data-k="${k}">${LV.map(([v, n]) => `<button role="radio" aria-checked="${d[k] === v}" data-v="${v}">${n}</button>`).join("")}</div></div>`).join("")}
    <div class="prow row sp"><div><b>🔎 Ocultar meu perfil dos buscadores (Google etc.)</b><div class="mut">Adiciona “noindex” à sua página pública e tira você do sitemap.</div></div><button class="sw" role="switch" aria-checked="${!!d.hide_search}" id="hs" aria-label="Ocultar perfil dos buscadores"></button></div></div>
   <div class="card"><b>★ Amigos próximos</b><div class="mut">Quem está aqui passa nos cadeados marcados como “Amigos próximos”. Adicione pessoas pelo perfil delas.</div><div id="cfl" class="row wrap" style="margin-top:8px"></div></div>
   <div class="card"><b>🔔 Avisos de chamadas</b><div class="mut">Para tocar e avisar quando a aba estiver em segundo plano.</div><button class="btn soft" id="nt" style="margin-top:8px">Ativar notificações</button> <button class="btn soft" id="mc" style="margin-top:8px">Testar microfone e câmera</button></div>`;
  const cf = () => { $("#cfl").innerHTML = d.close_friends.map(f => `<span class="chip"><a href="#/u/${f.username}">@${f.username}</a> <a data-r="${f.username}" style="cursor:pointer" aria-label="Remover">✕</a></span>`).join("") || '<span class="mut">Ninguém ainda.</span>'; $$("[data-r]").forEach(a => a.onclick = async () => { await api("/close-friends/" + a.dataset.r, "DELETE"); d.close_friends = d.close_friends.filter(x => x.username !== a.dataset.r); cf(); }); }; cf();
  const save = async b => { try { await api("/privacy", "PUT", b); toast("Salvo ✓", "ok"); } catch { toast("Não foi possível salvar. Tente de novo.", "err"); route(); } };
  $$(".seg").forEach(s => $$("button", s).forEach(b => b.onclick = () => { $$("button", s).forEach(x => x.setAttribute("aria-checked", x === b)); save({[s.dataset.k]: b.dataset.v}); }));
  $("#hs").onclick = e => { const v = e.target.getAttribute("aria-checked") !== "true"; e.target.setAttribute("aria-checked", v); save({hide_search: v}); };
  $("#nt").onclick = async () => { if (!("Notification" in window)) return toast("Seu navegador não suporta notificações.", "err"); const r = await Notification.requestPermission(); toast(r === "granted" ? "Notificações ativadas" : "Permissão negada no navegador", r === "granted" ? "ok" : "err"); };
  $("#mc").onclick = async () => { try { const s = await navigator.mediaDevices.getUserMedia({audio: true, video: true}); s.getTracks().forEach(t => t.stop()); toast("Microfone e câmera funcionando ✓", "ok"); } catch (e) { toast("Sem acesso ao microfone/câmera: libere a permissão no navegador.", "err"); } };
};
