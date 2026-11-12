/* Praça · mídia rica: exibição no feed, visualizador, compositor com upload, GIFs */

/* ---------------- exibição no feed ---------------- */
const slow = () => { const c = navigator.connection; return !!c && (c.saveData || /(^|-)2g$|3g/.test(c.effectiveType || "")); };
const isExt = u => /^https?:/.test(u);
const gifSrc = m => isExt(m.url) ? m.thumb : m.url;     // GIF local: arquivo animado; externo: prévia animada leve
const imgTag = (m, i, extra = "") => {
  const src = m.kind === "gif" ? gifSrc(m) : m.thumb;
  const ss = m.kind === "image" && m.w ? ` srcset="${esc(m.thumb)} 640w, ${esc(m.url)} ${m.w}w" sizes="(max-width:760px) 100vw, 600px"` : "";
  return `<img class="mimg" loading="lazy" decoding="async" data-i="${i}" src="${esc(src)}"${ss} alt="${esc(m.alt)}" ${m.w && m.h ? `style="aspect-ratio:${m.w}/${m.h}"` : ""} ${extra}>`;
};
function mediaBlock(list) {
  if (!list || !list.length) return "";
  const m0 = list[0];
  if (m0.kind === "video") {
    const best = pickVariant(m0.variants);
    return `<div class="mwrap vwrap" data-vid='${esc(JSON.stringify(m0.variants))}' data-dur="${m0.dur || 0}"><video preload="metadata" playsinline loop muted controls poster="${esc(m0.thumb !== m0.url ? m0.thumb : "")}" src="${esc(best.url)}"></video>
      <div class="vbar"><span class="tag">${m0.dur ? mmss(m0.dur) : "vídeo"}${m0.status === "processing" ? " · gerando qualidades…" : ""}</span>${m0.variants.length > 1 ? `<select class="vq" aria-label="Qualidade"><option value="auto">Auto (${esc(best.label)})</option>${m0.variants.map(v => `<option>${esc(v.label)}</option>`).join("")}</select>` : ""}</div></div>`;
  }
  if (list.length === 1) return `<div class="mwrap">${imgTag(m0, 0)}${m0.kind === "gif" ? '<span class="gif-tag">GIF</span>' : ""}</div>`;
  return `<div class="mwrap" data-car><div class="car">${list.map((m, i) => `<div class="slide">${imgTag(m, i)}${m.kind === "gif" ? '<span class="gif-tag">GIF</span>' : ""}</div>`).join("")}</div>
    <span class="cnt">1/${list.length}</span><button class="carbtn l" aria-label="Anterior">‹</button><button class="carbtn r" aria-label="Próxima">›</button><div class="dots">${list.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div></div>`;
}
function pickVariant(vs) {
  const by = l => vs.find(v => v.label === l);
  if (slow()) return by("360p") || by("720p") || vs[0];
  return by("720p") || vs[0];
}
let vidIO;
function bindMedia(root, posts) {
  const mediaOf = el => posts[el.closest("[data-pid]")?.dataset.pid] || [];
  $$(".mimg", root).forEach(im => { im.onclick = () => { const l = mediaOf(im).filter(m => m.kind !== "video"); openViewer(l.map(m => ({url: m.kind === "gif" ? gifSrc(m) : m.url, alt: m.alt})), +im.dataset.i); }; });
  $$("[data-car]", root).forEach(w => {
    if (w._b) return; w._b = 1;
    const car = $(".car", w), n = $$(".slide", w).length;
    const upd = () => { const i = Math.round(car.scrollLeft / car.clientWidth); $(".cnt", w).textContent = (i + 1) + "/" + n; $$(".dots i", w).forEach((d, k) => d.classList.toggle("on", k === i)); };
    car.addEventListener("scroll", () => requestAnimationFrame(upd), {passive: true});
    $(".carbtn.l", w).onclick = () => car.scrollBy({left: -car.clientWidth, behavior: "smooth"});
    $(".carbtn.r", w).onclick = () => car.scrollBy({left: car.clientWidth, behavior: "smooth"});
  });
  vidIO ||= new IntersectionObserver(es => es.forEach(e => {
    const v = e.target; if (e.isIntersecting && !slow() && !v._user) v.play().catch(() => { }); else v.pause();
  }), {threshold: .6});
  $$(".vwrap", root).forEach(w => {
    if (w._b) return; w._b = 1;
    const v = $("video", w), vs = JSON.parse(w.dataset.vid), sel = $(".vq", w);
    vidIO.observe(v);
    v.addEventListener("play", () => $$("video").forEach(o => o !== v && !o.paused && o.pause()));
    v.addEventListener("click", () => { v._user = true; });
    v.addEventListener("error", () => { if (v.src !== vs[vs.length - 1].url && sel) { const f = vs.find(x => x.label === "Original"); if (f && v.src !== location.origin + f.url) { v.src = f.url; } } });
    if (sel) sel.onchange = () => {
      const want = sel.value === "auto" ? pickVariant(vs) : vs.find(x => x.label === sel.value), t = v.currentTime, was = !v.paused;
      v.src = want.url; v.addEventListener("loadedmetadata", () => { v.currentTime = t; was && v.play().catch(() => { }); }, {once: true});
    };
  });
}

/* ---------------- visualizador (swipe, pinch-zoom, teclado) ---------------- */
function openViewer(items, start = 0) {
  if (!items.length) return;
  const el = h(`<div id="viewer" role="dialog" aria-label="Visualizador de imagens"><div class="stage"><img alt=""></div><button class="vb l" aria-label="Anterior">‹</button><button class="vb r" aria-label="Próxima">›</button><button class="vb x" aria-label="Fechar">✕</button><div class="info"></div></div>`);
  document.body.append(el);
  const img = $("img", el); let i = start, sc = 1, tx = 0, ty = 0;
  const apply = () => { img.style.transform = `translate(${tx}px,${ty}px) scale(${sc})`; };
  const reset = () => { sc = 1; tx = ty = 0; apply(); };
  const show = n => { i = (n + items.length) % items.length; reset(); img.src = items[i].url; img.alt = items[i].alt || ""; $(".info", el).textContent = (items.length > 1 ? `${i + 1} / ${items.length}  ` : "") + (items[i].alt || ""); };
  const close = () => { el.remove(); removeEventListener("keydown", key); };
  const key = e => {
    if (e.key === "Escape") close(); else if (e.key === "ArrowRight") show(i + 1); else if (e.key === "ArrowLeft") show(i - 1);
    else if (e.key === "+" || e.key === "=") { sc = Math.min(6, sc * 1.3); apply(); } else if (e.key === "-") { sc = Math.max(1, sc / 1.3); if (sc === 1) { tx = ty = 0; } apply(); } else if (e.key === "0") reset();
  };
  addEventListener("keydown", key);
  $(".x", el).onclick = close; $(".l", el).onclick = () => show(i - 1); $(".r", el).onclick = () => show(i + 1);
  const pts = new Map(); let d0 = 0, s0 = 1, st = null, lastTap = 0;
  const dist = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
  el.addEventListener("pointerdown", e => {
    if (e.target.closest(".vb")) return;
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY}); el.setPointerCapture(e.pointerId); img.classList.add("drag");
    if (pts.size === 2) { d0 = dist(); s0 = sc; } st = {x: e.clientX, y: e.clientY, tx, ty, t: Date.now()};
  });
  el.addEventListener("pointermove", e => {
    if (!pts.has(e.pointerId)) return; pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    if (pts.size === 2) { sc = Math.min(6, Math.max(1, s0 * dist() / d0)); apply(); }
    else if (sc > 1 && st) { tx = st.tx + e.clientX - st.x; ty = st.ty + e.clientY - st.y; apply(); }
  });
  const up = e => {
    if (!pts.has(e.pointerId)) return; const was = pts.size; pts.delete(e.pointerId); img.classList.remove("drag");
    if (was === 1 && st) {
      const dx = e.clientX - st.x, dy = e.clientY - st.y;
      if (Math.hypot(dx, dy) < 8 && Date.now() - st.t < 300) {          // toque / duplo toque
        if (Date.now() - lastTap < 320) { sc = sc > 1 ? 1 : 2.5; if (sc === 1) tx = ty = 0; apply(); lastTap = 0; } else lastTap = Date.now();
        if (e.target === el || e.target.classList.contains("stage")) { if (sc === 1) setTimeout(() => lastTap && Date.now() - lastTap > 300 && close(), 330); }
      } else if (sc === 1 && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) show(i + (dx < 0 ? 1 : -1));
      else if (sc === 1 && dy > 110 && Math.abs(dy) > Math.abs(dx)) close();
    }
    if (sc < 1.05) reset(); if (pts.size === 1) { const [p] = [...pts.values()]; st = {x: p.x, y: p.y, tx, ty, t: 0}; }
  };
  el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
  el.addEventListener("wheel", e => { e.preventDefault(); sc = Math.min(6, Math.max(1, sc * (e.deltaY < 0 ? 1.15 : .87))); if (sc === 1) tx = ty = 0; apply(); }, {passive: false});
  show(start);
}

/* ---------------- upload com progresso ---------------- */
function uploadFile(file, {onProgress, library, duration, alt} = {}, cap) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("POST", "/api/media"); if (cap) xhr.setRequestHeader("X-Captcha", cap);
    xhr.upload.onprogress = e => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let d = {}; try { d = JSON.parse(xhr.responseText); } catch { }
      if (xhr.status >= 200 && xhr.status < 300) resolve(d);
      else if (xhr.status === 428) { const a = prompt("Verificação anti-bot: quanto é " + d.question + "?"); a ? resolve(uploadFile(file, {onProgress, library, duration, alt}, a.trim()).promise) : reject({error: "Verificação cancelada."}); }
      else if (xhr.status === 401) { reject({error: "Sua sessão expirou. Entre de novo."}); }
      else reject({error: d.error || "Falha no envio (" + xhr.status + ")."});
    };
    xhr.onerror = () => reject({error: "Sem conexão. Toque em ↻ para tentar de novo.", network: true});
    xhr.onabort = () => reject({error: "Envio cancelado.", aborted: true});
    const fd = new FormData(); fd.append("file", file); if (library) fd.append("library", "1"); if (duration) fd.append("duration", duration); xhr.send(fd);
  });
  return {promise, abort: () => xhr.abort()};
}
const videoDuration = f => new Promise(res => { const v = document.createElement("video"); v.preload = "metadata"; v.onloadedmetadata = () => { URL.revokeObjectURL(v.src); res(v.duration); }; v.onerror = () => res(null); v.src = URL.createObjectURL(f); });

/* ---------------- compositor ---------------- */
function openComposer(opts = {}) {
  const inline = !isMobile() && $("#composer-host") && !opts.place;
  if (!needMember()) return;
  if (inline) { $("#composer-host textarea")?.focus(); return; }
  const m = openModal('<h2>Novo post</h2><div id="cm"></div>', {full: isMobile()});
  buildComposer($("#cm"), {place: opts.place, onPosted: () => { m.close(); if (location.hash.startsWith("#/feed") || location.hash.startsWith("#/local") || location.hash === "") route(); }});
}

function buildComposer(host, {onPosted, place = null} = {}) {
  const MAXC = cfg.max_carousel || 10;
  const S = {items: [], place, busy: false}; let uid = 0, dragFrom = null;
  host.innerHTML = `<div class="comp"><textarea maxlength="${cfg.text || 280}" placeholder="O que está acontecendo na Praça?" aria-label="Texto do post"></textarea>
   <div class="prev" hidden></div>
   <div class="placebar" hidden></div>
   <div class="row sp wrap"><div class="tools">
     <button type="button" data-t="photo" title="Fotos (até ${MAXC})" aria-label="Adicionar fotos">🖼</button>
     <button type="button" data-t="video" title="Vídeo (até ${Math.round((cfg.max_video_sec || 1800) / 60)} min)" aria-label="Adicionar vídeo">🎬</button>
     <button type="button" data-t="cam" title="Câmera" aria-label="Usar a câmera" class="cam">📸</button>
     <button type="button" data-t="gif" title="GIF" aria-label="Adicionar GIF">GIF</button>
     <button type="button" data-t="place" title="Local" aria-label="Marcar local">📍</button></div>
    <div class="row"><span class="mut ccount">0/${cfg.text || 280}</span><button type="button" class="btn pub">Publicar</button></div></div>
   <div class="err" role="alert"></div>
   <input type="file" class="f-photo" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden>
   <input type="file" class="f-video" accept="video/mp4,video/quicktime,video/webm" hidden>
   <input type="file" class="f-cam" accept="image/*,video/*" capture="environment" hidden></div>`;
  const root = $(".comp", host), ta = $("textarea", root), prev = $(".prev", root), err = $(".err", root), pub = $(".pub", root);
  if (!isMobile() || !("ontouchstart" in window)) $(".cam", root).hidden = !("ontouchstart" in window);
  const say = t => err.textContent = t || "";
  const nVid = () => S.items.filter(i => i.kind === "video").length;
  const refresh = () => {
    $(".ccount", root).textContent = ta.value.length + "/" + (cfg.text || 280);
    const up = S.items.some(i => i.status === "up");
    pub.disabled = S.busy || up || (!ta.value.trim() && !S.items.length && !S.place);
    pub.textContent = up ? "Enviando…" : S.busy ? "Publicando…" : "Publicar";
  };
  const placeBar = () => {
    const pb = $(".placebar", root); pb.hidden = !S.place;
    if (S.place) { pb.innerHTML = `<span class="chip">📍 ${esc(S.place.name)}</span><button type="button" class="btn sm ghost">Remover</button>`; $("button", pb).onclick = () => { S.place = null; placeBar(); refresh(); }; }
    refresh();
  };
  function render() {
    prev.hidden = !S.items.length;
    prev.innerHTML = S.items.map((it, k) => `<div class="pv" draggable="true" data-k="${it.id}">
      ${it.kind === "video" ? `<video src="${esc(it.local)}" muted preload="metadata"></video><span class="tag">vídeo${it.dur ? " · " + mmss(it.dur) : ""}</span>` : `<img src="${esc(it.local || it.preview)}" alt="">`}
      <span class="n">${k + 1}</span><button class="ed" title="Editar" aria-label="Editar mídia ${k + 1}">✎</button><button class="x" title="Excluir" aria-label="Excluir mídia ${k + 1}">✕</button>
      ${S.items.length > 1 ? `<div class="mv"><button data-d="-1" aria-label="Mover para antes">◀</button><button data-d="1" aria-label="Mover para depois">▶</button></div>` : ""}
      ${it.status === "up" ? `<div class="pg"><i style="width:${Math.round((it.pg || 0) * 100)}%"></i></div>` : ""}
      ${it.status === "err" ? `<div class="fail"><span>${esc(it.error)}</span><button data-r="1">↻ Tentar de novo</button></div>` : ""}</div>`).join("");
    $$(".pv", prev).forEach(el => {
      const it = S.items.find(x => x.id == el.dataset.k), k = S.items.indexOf(it); it.el = el;
      $(".x", el).onclick = () => remove(it);
      $(".ed", el).onclick = () => edit(it);
      $$(".mv button", el).forEach(b => b.onclick = () => move(k, k + +b.dataset.d));
      const rb = $("[data-r]", el); if (rb) rb.onclick = () => start(it);
      el.ondragstart = e => { dragFrom = k; e.dataTransfer.effectAllowed = "move"; };
      el.ondragover = e => { e.preventDefault(); el.classList.add("over"); }; el.ondragleave = () => el.classList.remove("over");
      el.ondrop = e => { e.preventDefault(); e.stopPropagation(); el.classList.remove("over"); if (dragFrom !== null) move(dragFrom, k); dragFrom = null; };
    });
    refresh();
  }
  const move = (a, b) => { if (b < 0 || b >= S.items.length || a === b) return; const [x] = S.items.splice(a, 1); S.items.splice(b, 0, x); render(); };
  async function remove(it) {
    it.up?.abort(); S.items = S.items.filter(x => x !== it); render();
    if (it.media) api(`/media/${it.media.id}`, "DELETE").catch(() => { });
    if (it.local && it.local.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(it.local), 500);
  }
  async function start(it) {
    it.status = "up"; it.pg = 0; it.error = ""; render();
    it.up = uploadFile(it.file, {duration: it.dur, onProgress: p => { it.pg = p; const b = it.el && $(".pg i", it.el); if (b) b.style.width = Math.round(p * 100) + "%"; }});
    try { it.media = await it.up.promise; it.status = "ok"; if (it.media.alt) it.alt = it.media.alt; }
    catch (e) { if (e.aborted) return; it.status = "err"; it.error = e.error || "Falha no envio."; }
    render();
  }
  async function addFiles(files) {
    say(""); files = [...files];
    for (const f of files) {
      const isV = f.type.startsWith("video/") || /\.(mp4|mov|webm)$/i.test(f.name), isI = f.type.startsWith("image/");
      if (!isV && !isI) { say(`“${f.name}”: formato não suportado.`); continue; }
      if (isV && (S.items.length || nVid())) { say("Um post aceita 1 vídeo OU até " + MAXC + " imagens, não os dois."); continue; }
      if (isI && nVid()) { say("Remova o vídeo antes de adicionar imagens."); continue; }
      if (S.items.length >= MAXC) { say(`Máximo de ${MAXC} imagens por post.`); break; }
      const cap = (isV ? cfg.max_video_mb : f.type === "image/gif" ? cfg.max_gif_mb : cfg.max_image_mb) * 1048576;
      if (f.size > cap) { say(`“${f.name}” passa de ${Math.round(cap / 1048576)} MB.`); continue; }
      let dur = null;
      if (isV) { dur = await videoDuration(f); if (dur && dur > cfg.max_video_sec) { say(`O vídeo tem ${Math.round(dur / 60)} min. O limite é ${Math.round(cfg.max_video_sec / 60)} min.`); continue; } }
      const it = {id: ++uid, kind: isV ? "video" : f.type === "image/gif" ? "gif" : "image", file: f, local: URL.createObjectURL(f), dur, status: "up", alt: ""};
      S.items.push(it); start(it);
    }
    render();
  }
  /* editar: girar imagem + texto alternativo */
  function edit(it) {
    const m = openModal(`<h2>Editar mídia</h2>${it.kind === "video" ? `<video src="${esc(it.local)}" controls style="width:100%;border-radius:12px;max-height:50vh"></video>` : `<div style="text-align:center"><img id="ei" src="${esc(it.local || it.preview)}" style="max-width:100%;max-height:48vh;border-radius:12px;transition:transform .2s"></div>`}
      ${it.kind === "image" ? '<div class="row" style="justify-content:center;margin:10px 0"><button class="btn soft sm" id="rl">⟲ Girar</button><button class="btn soft sm" id="rr">⟳ Girar</button><button class="btn soft sm" id="fl">⇋ Espelhar</button></div>' : ""}
      <label class="mut">Descrição para acessibilidade (opcional)</label><input id="al" maxlength="200" value="${esc(it.alt || "")}" placeholder="Ex.: Pôr do sol na praça central">
      <div class="row sp" style="margin-top:12px"><button class="btn ghost" id="cn">Cancelar</button><button class="btn" id="ok">Aplicar</button></div>`);
    let rot = 0, flip = false; const im = $("#ei", m), tf = () => { if (im) im.style.transform = `rotate(${rot}deg) scaleX(${flip ? -1 : 1})`; };
    if (im) { $("#rl", m).onclick = () => { rot -= 90; tf(); }; $("#rr", m).onclick = () => { rot += 90; tf(); }; $("#fl", m).onclick = () => { flip = !flip; tf(); }; }
    $("#cn", m).onclick = () => m.close();
    $("#ok", m).onclick = async () => {
      it.alt = $("#al", m).value;
      if (it.media) api(`/media/${it.media.id}`, "PUT", {alt: it.alt}).catch(() => { });
      if (it.kind === "image" && (rot % 360 !== 0 || flip) && it.file) {
        $("#ok", m).disabled = true; $("#ok", m).textContent = "Processando…";
        const bmp = await createImageBitmap(it.file, {imageOrientation: "from-image"}), q = ((rot % 360) + 360) % 360, sw = q % 180 ? bmp.height : bmp.width, sh = q % 180 ? bmp.width : bmp.height;
        const cv = document.createElement("canvas"); cv.width = sw; cv.height = sh; const c = cv.getContext("2d");
        c.translate(sw / 2, sh / 2); c.rotate(q * Math.PI / 180); if (flip) c.scale(-1, 1); c.drawImage(bmp, -bmp.width / 2, -bmp.height / 2);
        const blob = await new Promise(r => cv.toBlob(r, "image/jpeg", .92));
        it.up?.abort(); if (it.media) api(`/media/${it.media.id}`, "DELETE").catch(() => { });
        it.file = new File([blob], "editada.jpg", {type: "image/jpeg"}); it.local = URL.createObjectURL(blob); it.media = null; it.kind = "image"; start(it);
      }
      m.close(); render();
    };
  }
  /* GIF */
  function gifPicker() {
    if (S.items.length >= MAXC) return say(`Máximo de ${MAXC} itens.`);
    if (nVid()) return say("Um vídeo não pode ser combinado com GIFs.");
    const m = openModal(`<h2>GIFs</h2><input id="gq" type="search" placeholder="Buscar GIFs…" autofocus><div class="mut" id="gp" style="margin-top:6px"></div><div class="gifs" id="gg"></div>`, {full: isMobile()});
    let w; const load = async () => {
      const d = await api("/gifs?q=" + encodeURIComponent($("#gq", m).value)).catch(() => ({items: [], provider: "local"}));
      $("#gp", m).innerHTML = d.provider === "local" ? 'Biblioteca da Praça. Para buscar milhões de GIFs, o administrador define <code>GIPHY_KEY</code> ou <code>TENOR_KEY</code>. <button class="btn sm soft" id="gu">Enviar GIF à biblioteca</button>' : "Powered by " + (d.provider === "tenor" ? "Tenor" : "GIPHY");
      $("#gg", m).innerHTML = d.items.map((g, i) => `<img data-i="${i}" loading="lazy" src="${esc(g.preview)}" alt="${esc(g.title)}">`).join("") || '<div class="empty" style="column-span:all">Nenhum GIF encontrado.</div>';
      $$("#gg img", m).forEach(im => im.onclick = () => {
        const g = d.items[+im.dataset.i]; m.close();
        if (g.local) S.items.push({id: ++uid, kind: "gif", preview: g.preview, status: "ok", media: {id: g.local}, alt: g.title, lib: true, ref: g.local});
        else S.items.push({id: ++uid, kind: "gif", preview: g.preview, status: "ok", gif: {url: g.url, preview: g.preview, w: g.w, h: g.h}, alt: g.title});
        render();
      });
      const gu = $("#gu", m); if (gu) gu.onclick = () => { const f = document.createElement("input"); f.type = "file"; f.accept = "image/gif"; f.onchange = async () => {
        const alt = prompt("Palavras-chave deste GIF (para busca):", "") || ""; try { const r = await uploadFile(f.files[0], {library: true}).promise; if (alt) await api(`/media/${r.id}`, "PUT", {alt}); toast("GIF adicionado à biblioteca", "ok"); load(); } catch (e) { toast(e.error || "Falha", "err"); } }; f.click(); };
    };
    $("#gq", m).oninput = () => { clearTimeout(w); w = setTimeout(load, 350); }; load();
  }
  /* eventos */
  ta.oninput = refresh;
  ta.onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !pub.disabled) pub.click(); };
  ta.onpaste = e => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); addFiles(fs); } };
  $(".f-photo", root).onchange = e => { addFiles(e.target.files); e.target.value = ""; };
  $(".f-video", root).onchange = e => { addFiles(e.target.files); e.target.value = ""; };
  $(".f-cam", root).onchange = e => { addFiles(e.target.files); e.target.value = ""; };
  $$(".tools button", root).forEach(b => b.onclick = () => ({photo: () => $(".f-photo", root).click(), video: () => $(".f-video", root).click(), cam: () => $(".f-cam", root).click(), gif: gifPicker,
    place: () => placePicker(p => { S.place = p; placeBar(); })}[b.dataset.t])());
  ["dragenter", "dragover"].forEach(ev => root.addEventListener(ev, e => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); root.classList.add("drag"); } }));
  ["dragleave", "drop"].forEach(ev => root.addEventListener(ev, e => { if (ev === "drop" || e.target === root) root.classList.remove("drag"); }));
  root.addEventListener("drop", e => { if (e.dataTransfer?.files?.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } });
  pub.onclick = async () => {
    say(""); S.busy = true; refresh();
    const media = S.items.map(i => i.gif ? {gif: i.gif} : i.media.id);
    const body = {text: ta.value, media};
    if (S.place) S.place.id ? body.place_id = S.place.id : body.place = S.place;
    try { await api("/posts", "POST", body); ta.value = ""; S.items = []; S.place = null; render(); placeBar(); toast("Publicado!", "ok"); onPosted && onPosted(); }
    catch (e) { say(e.error || "Não foi possível publicar. Verifique sua conexão e tente de novo."); }
    S.busy = false; refresh();
  };
  placeBar(); render();
  return {focus: () => ta.focus(), addFiles};
}
