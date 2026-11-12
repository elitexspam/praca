/* Praça · Esquinas da Praça: mini mapa (tiles OSM), seletor de local, check-in e Feed do Local */
function tileMap(lat, lng, z = 16) {
  const n = 2 ** z, x = (lng + 180) / 360 * n, r = lat * Math.PI / 180, y = (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n;
  const tx = Math.floor(x), ty = Math.floor(y), fx = x - tx, fy = y - ty;
  let imgs = "";
  for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) imgs += `<img loading="lazy" alt="" src="https://tile.openstreetmap.org/${z}/${tx + i}/${ty + j}.png">`;
  return `<div class="tilemap" role="img" aria-label="Mapa do local"><div class="g" style="margin-left:${-(1 + fx) * 256}px;margin-top:${-(1 + fy) * 256}px">${imgs}</div><div class="pin">📍</div><a class="attr" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap</a></div>`;
}
const fmtDist = d => d == null ? "" : d < 1000 ? Math.round(d / 10) * 10 + " m" : (d / 1000).toFixed(1).replace(".", ",") + " km";

function placePicker(onPick) {
  let pos = null, sel = null, w;
  const m = openModal(`<h2>📍 Onde você está?</h2><div class="row"><input id="pq" type="search" placeholder="Buscar padaria, praça, bar…" autocomplete="off"><button class="btn soft" id="pg" title="Usar minha localização">◎</button></div>
    <div id="pm"></div><div class="plist" id="pl" style="margin-top:6px"></div><div class="err" id="pe"></div>
    <div class="row sp"><button class="btn ghost" id="pc">Cancelar</button><button class="btn" id="po" disabled>Marcar local</button></div>`, {full: isMobile()});
  const list = async () => {
    const s = $("#pq", m).value.trim();
    if (!s && !pos) { $("#pl", m).innerHTML = '<div class="mut" style="padding:10px">Digite um nome ou toque em ◎ para ver locais perto de você.</div>'; return; }
    $("#pl", m).innerHTML = '<div class="mut" style="padding:10px">Buscando…</div>';
    try {
      const d = await api(`/places/search?q=${encodeURIComponent(s)}${pos ? `&lat=${pos.lat}&lng=${pos.lng}` : ""}`);
      let items = d.items;
      if (!s && pos) { try { const r = await api(`/places/reverse?lat=${pos.lat}&lng=${pos.lng}`); items = [{name: r.name || "Minha localização", address: r.address, lat: pos.lat, lng: pos.lng, osm_id: r.osm_id, here: true}, ...items]; } catch { } }
      $("#pe", m).textContent = d.degraded ? "A busca de estabelecimentos está indisponível agora. Você ainda pode usar locais já conhecidos ou sua posição (◎)." : "";
      $("#pl", m).innerHTML = items.map((p, i) => `<div class="it" data-i="${i}" role="button" tabindex="0"><span style="font-size:1.4rem">${p.here ? "◎" : "📍"}</span><div class="grow"><b>${esc(p.name)}</b><div class="mut" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.address || "")}</div></div><span class="mut">${fmtDist(p.dist)}</span></div>`).join("") || '<div class="empty">Nada encontrado. Tente outro nome ou use ◎.</div>';
      $$(".it", m).forEach(e => { const pick = () => { sel = items[+e.dataset.i]; $$(".it", m).forEach(x => x.classList.toggle("sel", x === e)); $("#pm", m).innerHTML = tileMap(sel.lat, sel.lng); $("#po", m).disabled = false; }; e.onclick = pick; e.onkeydown = ev => ev.key === "Enter" && pick(); });
    } catch (e) { $("#pl", m).innerHTML = `<div class="err">${esc(e.error || "Não foi possível buscar.")}</div>`; }
  };
  $("#pq", m).oninput = () => { clearTimeout(w); w = setTimeout(list, 450); };
  $("#pg", m).onclick = () => {
    if (!navigator.geolocation) return toast("Seu navegador não oferece geolocalização.", "err");
    $("#pg", m).disabled = true;
    navigator.geolocation.getCurrentPosition(p => { pos = {lat: p.coords.latitude, lng: p.coords.longitude}; $("#pg", m).disabled = false; $("#pm", m).innerHTML = tileMap(pos.lat, pos.lng); list(); },
      e => { $("#pg", m).disabled = false; $("#pe", m).textContent = e.code === 1 ? "Permissão de localização negada. Você pode buscar o local pelo nome." : "Não foi possível obter sua posição. Busque pelo nome."; },
      {enableHighAccuracy: false, timeout: 8000, maximumAge: 60000});
  };
  $("#pc", m).onclick = () => m.close();
  $("#po", m).onclick = () => { m.close(); onPick(sel.id ? {id: sel.id, name: sel.name} : {name: sel.name, address: sel.address, lat: sel.lat, lng: sel.lng, osm_id: sel.osm_id}); };
  list(); $("#pq", m).focus();
}

views.local = async id => {
  let p;
  try { p = await fetch("/api/places/" + id).then(r => r.ok ? r.json() : Promise.reject()); }
  catch { return $("#main").innerHTML = '<div class="card empty">Local não encontrado.</div>'; }
  $("#main").innerHTML = `<div class="card"><div class="row sp wrap"><div><div class="head" style="margin:0">📍 ${esc(p.name)}</div><div class="mut">${esc(p.address)}</div></div>
    <button class="btn" id="ci">Fazer check-in</button></div>${tileMap(p.lat, p.lng)}
    <div class="row"><span class="chip">${p.posts} post(s)</span><span class="chip">${p.today} hoje</span><span class="chip">${p.people} pessoa(s)</span></div></div><div id="lst"></div>`;
  $("#ci").onclick = () => openComposer({place: {id: p.id, name: p.name}});
  feedList($("#lst"), `/places/${id}/feed`, {empty: "Ninguém passou por aqui ainda. Seja o primeiro a fazer check-in!"});
};
views.trending = async () => {
  const t = await fetch("/api/places/trending").then(r => r.json()).catch(() => []);
  $("#main").innerHTML = '<div class="head">📍 Esquinas em alta</div>' + (t.map(p => `<a class="card item" href="#/local/${p.id}"><div class="t"><b>${esc(p.name)}</b><span class="mut">${esc(p.address)} · ${p.n} post(s) esta semana</span></div></a>`).join("") || '<div class="card empty">Ainda não há locais marcados.</div>');
};
