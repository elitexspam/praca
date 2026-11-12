"""Praça · núcleo social: eventos em tempo real (SSE), privacidade, mídia rica, GIFs e locais.
Chamadas e Mesas de Debate ficam em live.py."""
import json, math, os, queue, re, secrets, shutil, subprocess, threading, time, urllib.parse, urllib.request
from functools import wraps
from flask import Blueprint, Response, g, jsonify, request, send_from_directory

bp = Blueprint("social", __name__)
q = count = NEED = None
BASE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(os.environ.get("DATA_DIR", BASE), "uploads")
os.makedirs(UP, exist_ok=True)

# ----------------------------------------------------------------- limites (configuráveis por variável de ambiente)
LIM = dict(
    max_image_mb=int(os.environ.get("MAX_IMAGE_MB", 25)),
    max_gif_mb=int(os.environ.get("MAX_GIF_MB", 15)),
    max_video_mb=int(os.environ.get("MAX_VIDEO_MB", 700)),
    max_video_sec=int(os.environ.get("MAX_VIDEO_SEC", 1800)),   # 30 minutos
    max_carousel=10,
    text=280,
    uploads_per_hour=80,
    pending_ttl_h=24,
)
GIF_HOSTS = ("media.giphy.com", "i.giphy.com", "media0.giphy.com", "media1.giphy.com", "media2.giphy.com",
             "media3.giphy.com", "media4.giphy.com", "media.tenor.com", "c.tenor.com")
LEVELS = ("all", "close", "none")
_rate = {}


def rate(key, n, per):
    now = time.time()
    v = [t for t in _rate.get(key, []) if now - t < per]
    if len(v) >= n:
        _rate[key] = v
        return False
    _rate[key] = v + [now]
    return True


def J():
    return request.get_json(silent=True) or {}


def member(f):
    @wraps(f)
    def w(*a, **k):
        return NEED(f)(*a, **k)
    return w


# ---------------------------------------------------------------------- hub de eventos (tempo real)
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = {}      # uid (0 = visitante) -> {queues}
        self.offline = {}   # uid -> instante em que a última conexão caiu

    def sub(self, uid):
        qu = queue.Queue(maxsize=400)
        with self.lock:
            self.subs.setdefault(uid, set()).add(qu)
            self.offline.pop(uid, None)
        return qu

    def unsub(self, uid, qu):
        with self.lock:
            s = self.subs.get(uid)
            if s:
                s.discard(qu)
                if not s:
                    self.subs.pop(uid, None)
                    if uid:
                        self.offline[uid] = time.time()

    def emit(self, uid, ev, data):
        with self.lock:
            targets = list(self.subs.get(uid, ()))
        for t in targets:
            try:
                t.put_nowait((ev, data))
            except queue.Full:
                pass

    def broadcast(self, ev, data, visitors=True, skip=None):
        with self.lock:
            snap = [(u, list(s)) for u, s in self.subs.items()]
        for u, qs in snap:
            if u == skip or (u == 0 and not visitors):
                continue
            for t in qs:
                try:
                    t.put_nowait((ev, data))
                except queue.Full:
                    pass

    def online(self, uid):
        return uid in self.subs

    def offline_for(self, uid):
        return time.time() - self.offline[uid] if uid in self.offline else 0

    def stats(self):
        with self.lock:
            return dict(members=len([u for u in self.subs if u]), visitors=len(self.subs.get(0, ())))


hub = Hub()


@bp.get("/api/events")
def events():
    uid = g.user["id"] if g.user else 0
    qu = hub.sub(uid)

    def gen():
        try:
            yield "retry: 2000\n\nevent: hello\ndata: " + json.dumps(dict(member=bool(uid))) + "\n\n"
            while True:
                try:
                    ev, data = qu.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"event: {ev}\ndata: {json.dumps(data)}\n\n"
        finally:
            hub.unsub(uid, qu)
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------- privacidade
DEFAULT_PRIV = dict(photos="all", calls="all", locations="all", hide_search=0)


def priv(uid):
    r = q("select * from privacy where user_id=?", (uid,), one=True)
    return dict(photos=r["photos"], calls=r["calls"], locations=r["locations"], hide_search=int(r["hide_search"])) if r else dict(DEFAULT_PRIV)


def allowed(owner_id, viewer_id, field):
    """Pode o visitante/membro `viewer_id` interagir com `owner_id` segundo o cadeado `field`?"""
    if viewer_id and viewer_id == owner_id:
        return True
    lvl = priv(owner_id).get(field, "all")
    if lvl == "all":
        return True
    if lvl == "none" or not viewer_id:
        return False
    return bool(q("select 1 from close_friends where user_id=? and friend_id=?", (owner_id, viewer_id), one=True))


def perms(owner_id, viewer_id):
    return dict(photo=allowed(owner_id, viewer_id, "photos"), call=allowed(owner_id, viewer_id, "calls"),
                locations=allowed(owner_id, viewer_id, "locations"))


@bp.get("/api/privacy")
@member
def privacy_get():
    cf = q("select u.username,u.display from close_friends c join users u on u.id=c.friend_id where c.user_id=? order by u.username", (g.user["id"],))
    return jsonify(dict(priv(g.user["id"]), close_friends=[dict(r) for r in cf]))


@bp.put("/api/privacy")
@member
def privacy_put():
    d, cur = J(), priv(g.user["id"])
    for k in ("photos", "calls", "locations"):
        if k in d:
            if d[k] not in LEVELS:
                return jsonify(error="Valor inválido para " + k), 400
            cur[k] = d[k]
    if "hide_search" in d:
        cur["hide_search"] = 1 if d["hide_search"] else 0
    q("insert or replace into privacy(user_id,photos,calls,locations,hide_search) values(?,?,?,?,?)",
      (g.user["id"], cur["photos"], cur["calls"], cur["locations"], cur["hide_search"]), write=True)
    return jsonify(cur)


@bp.post("/api/close-friends/<name>")
@member
def cf_add(name):
    u = q("select id from users where username=? and banned=0", (name.lower(),), one=True)
    if not u or u["id"] == g.user["id"]:
        return jsonify(error="Usuário inválido."), 400
    if count("select count(*) from close_friends where user_id=?", g.user["id"]) >= 150:
        return jsonify(error="Limite de 150 amigos próximos."), 400
    q("insert or ignore into close_friends values(?,?)", (g.user["id"], u["id"]), write=True)
    return jsonify(ok=True)


@bp.delete("/api/close-friends/<name>")
@member
def cf_del(name):
    q("delete from close_friends where user_id=? and friend_id=(select id from users where username=?)", (g.user["id"], name.lower()), write=True)
    return jsonify(ok=True)


# ------------------------------------------------------------------------------------ mídia
def sniff(h):
    if h[:3] == b"\xff\xd8\xff":
        return "image", "jpg"
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "png"
    if h[:4] == b"GIF8":
        return "gif", "gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image", "webp"
    if h[4:8] == b"ftyp":
        return "video", "mp4"
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return "video", "webm"
    return None, None


def murl(f):
    return f if f and f.startswith("http") else "/media/" + f


def media_out(r):
    var = json.loads(r["variants"] or "[]")
    return dict(id=r["id"], kind=r["kind"], url=murl(r["file"]), thumb=murl(r["thumb"] or r["file"]), w=r["w"], h=r["h"],
                dur=r["duration"], alt=r["alt"] or "", status=r["status"], variants=[dict(label=v["label"], url=murl(v["file"])) for v in var])


def process_image(src, name, kind):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 80_000_000
    im = Image.open(src)
    w0, h0 = im.size
    if kind == "gif":
        fr = im.convert("RGB"); fr.thumbnail((640, 640))
        thumb = name + "_t.jpg"; fr.save(os.path.join(UP, thumb), "JPEG", quality=78)
        return name + ".gif", thumb, w0, h0
    im = ImageOps.exif_transpose(im)     # respeita a rotação e descarta EXIF (inclui GPS) ao regravar
    im.thumbnail((4096, 4096))
    alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    full = name + (".png" if alpha else ".jpg")
    (im.convert("RGBA") if alpha else im.convert("RGB")).save(os.path.join(UP, full), "PNG" if alpha else "JPEG", **({} if alpha else dict(quality=88, optimize=True)))
    th = im.convert("RGB"); th.thumbnail((640, 640))
    thumb = name + "_t.jpg"; th.save(os.path.join(UP, thumb), "JPEG", quality=76)
    return full, thumb, im.size[0], im.size[1]


def ffprobe(path):
    try:
        o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                            "-of", "json", path], capture_output=True, text=True, timeout=40)
        j = json.loads(o.stdout or "{}")
        st = (j.get("streams") or [{}])[0]
        return float(j.get("format", {}).get("duration") or 0), st.get("width"), st.get("height")
    except Exception:
        return None


def transcode(mid, owner, name, src, height):
    """Gera pôster e versões 720p/360p em segundo plano (se houver ffmpeg)."""
    try:
        poster = name + "_t.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", src, "-frames:v", "1", "-vf", "scale=640:-2", os.path.join(UP, poster)], timeout=60)
        if os.path.exists(os.path.join(UP, poster)):
            q("update post_media set thumb=? where id=?", (poster, mid), write=True)
        var = [dict(label="Original", file=os.path.basename(src))]
        for label, hh in (("720p", 720), ("360p", 360)):
            if height and height < hh + 40 and label != "360p":
                continue
            if height and height <= 380 and label == "360p":
                continue
            out = f"{name}_{hh}.mp4"
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", f"scale=-2:{hh}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", os.path.join(UP, out)], timeout=3600)
            if r.returncode == 0:
                var.insert(0, dict(label=label, file=out))
                q("update post_media set variants=? where id=?", (json.dumps(var), mid), write=True)
        order = {"720p": 0, "360p": 1, "Original": 2}
        var.sort(key=lambda v: order[v["label"]])
        q("update post_media set variants=?, status='ready' where id=?", (json.dumps(var), mid), write=True)
    except Exception:
        q("update post_media set status='ready' where id=?", (mid,), write=True)
    hub.emit(owner, "media.ready", dict(id=mid))


@bp.get("/api/limits")
def limits():
    return jsonify(dict(LIM, ffmpeg=bool(shutil.which("ffmpeg")), gif_provider=gif_provider(), maps=True))


@bp.post("/api/media")
@member
def upload():
    f = request.files.get("file")
    uid = g.user["id"]
    if not f:
        return jsonify(error="Nenhum arquivo recebido."), 400
    if not rate(("up", uid), LIM["uploads_per_hour"], 3600):
        return jsonify(error="Muitos envios em pouco tempo. Tente de novo em alguns minutos."), 429
    head = f.stream.read(16); f.stream.seek(0)
    kind, ext = sniff(head)
    if not kind:
        return jsonify(error="Formato não suportado. Use JPG, PNG, WebP, GIF, MP4, MOV ou WebM."), 415
    name = secrets.token_hex(12)
    tmp = os.path.join(UP, name + ".part")
    f.save(tmp)
    size = os.path.getsize(tmp)
    cap = {"image": LIM["max_image_mb"], "gif": LIM["max_gif_mb"], "video": LIM["max_video_mb"]}[kind] * 1024 * 1024
    if size > cap:
        os.remove(tmp)
        return jsonify(error=f"Arquivo grande demais (máx. {cap // 1048576} MB para este tipo)."), 413
    try:
        if kind in ("image", "gif"):
            full, thumb, w, h = process_image(tmp, name, kind)
            if kind == "gif":
                os.replace(tmp, os.path.join(UP, full))      # mantém o GIF animado original
            else:
                os.remove(tmp)
            mid = q("insert into post_media(owner_id,kind,file,thumb,w,h,size,status,created,library) values(?,?,?,?,?,?,?,?,?,?)",
                    (uid, kind, full, thumb, w, h, size, "ready", time.time(), 1 if (kind == "gif" and request.form.get("library")) else 0), write=True)
        else:
            dur = h = w = None
            pr = ffprobe(tmp) if shutil.which("ffprobe") else None
            if pr:
                dur, w, h = pr
                if not w:
                    raise ValueError("sem faixa de vídeo")
            else:
                dur = float(request.form.get("duration") or 0)
            if dur and dur > LIM["max_video_sec"] + 1:
                os.remove(tmp)
                return jsonify(error=f"Vídeo muito longo ({int(dur // 60)} min). O limite é {LIM['max_video_sec'] // 60} minutos."), 422
            final = name + "." + ext
            os.replace(tmp, os.path.join(UP, final))
            can = bool(shutil.which("ffmpeg")) and bool(pr)
            mid = q("insert into post_media(owner_id,kind,file,thumb,w,h,duration,size,variants,status,created) values(?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, "video", final, None, w, h, dur, size, json.dumps([dict(label="Original", file=final)]), "processing" if can else "ready", time.time()), write=True)
            if can:
                threading.Thread(target=transcode, args=(mid, uid, name, os.path.join(UP, final), h), daemon=True).start()
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        return jsonify(error="Não foi possível ler este arquivo. Ele pode estar corrompido."), 422
    return jsonify(media_out(q("select * from post_media where id=?", (mid,), one=True)))


@bp.get("/api/media/<int:mid>")
@member
def media_get(mid):
    r = q("select * from post_media where id=? and owner_id=?", (mid, g.user["id"]), one=True)
    return jsonify(media_out(r)) if r else (jsonify(error="não encontrado"), 404)


@bp.put("/api/media/<int:mid>")
@member
def media_alt(mid):
    q("update post_media set alt=? where id=? and owner_id=?", ((J().get("alt") or "")[:200], mid, g.user["id"]), write=True)
    return jsonify(ok=True)


@bp.delete("/api/media/<int:mid>")
@member
def media_del(mid):
    r = q("select * from post_media where id=? and owner_id=? and post_id is null", (mid, g.user["id"]), one=True)
    if r:
        drop_files(r)
        q("delete from post_media where id=?", (mid,), write=True)
    return jsonify(ok=True)


def drop_files(r):
    names = {r["file"], r["thumb"]} | {v["file"] for v in json.loads(r["variants"] or "[]")}
    for n in names:
        if n and not n.startswith("http"):
            try:
                os.remove(os.path.join(UP, n))
            except OSError:
                pass


def purge_post_media(pid):
    for r in q("select * from post_media where post_id=?", (pid,)):
        drop_files(r)
    q("delete from post_media where post_id=?", (pid,), write=True)


@bp.get("/media/<path:name>")
def serve_media(name):
    r = send_from_directory(UP, name, conditional=True, max_age=31536000)   # conditional => suporta Range (vídeo)
    r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return r


# -------------------------------------------------------------------------------------- GIFs
def gif_provider():
    return "tenor" if os.environ.get("TENOR_KEY") else "giphy" if os.environ.get("GIPHY_KEY") else "local"


_gc = {}


def fetch_json(url, timeout=5):
    req = urllib.request.Request(url, headers={"User-Agent": "PracaApp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@bp.get("/api/gifs")
@member
def gifs():
    s = (request.args.get("q") or "").strip()[:60]
    prov, out = gif_provider(), []
    key = (prov, s)
    if key in _gc and time.time() - _gc[key][0] < 300:
        return jsonify(_gc[key][1])
    try:
        if prov == "tenor":
            k = urllib.parse.quote(os.environ["TENOR_KEY"])
            ep = "search" if s else "featured"
            d = fetch_json(f"https://tenor.googleapis.com/v2/{ep}?q={urllib.parse.quote(s)}&key={k}&client_key=praca&limit=24&media_filter=gif,tinygif&contentfilter=medium&locale=pt_BR")
            for r in d.get("results", []):
                m, t = r["media_formats"]["gif"], r["media_formats"].get("tinygif", r["media_formats"]["gif"])
                out.append(dict(id=r["id"], title=r.get("content_description", ""), url=m["url"], preview=t["url"], w=m["dims"][0], h=m["dims"][1]))
        elif prov == "giphy":
            k = urllib.parse.quote(os.environ["GIPHY_KEY"])
            ep = "search" if s else "trending"
            d = fetch_json(f"https://api.giphy.com/v1/gifs/{ep}?api_key={k}&q={urllib.parse.quote(s)}&limit=24&rating=pg-13&lang=pt")
            for r in d.get("data", []):
                im = r["images"]
                out.append(dict(id=r["id"], title=r.get("title", ""), url=im["downsized"]["url"], preview=im["fixed_width_small"]["url"],
                                w=int(im["downsized"]["width"]), h=int(im["downsized"]["height"])))
    except Exception:
        prov, out = "local", []
    if prov == "local":
        like = "%" + re.sub(r"[%_]", "", s.lower()) + "%"
        for r in q("select id,file,thumb,w,h,alt from post_media where library=1 and kind='gif' and lower(coalesce(alt,'')) like ? order by id desc limit 24", (like,)):
            out.append(dict(id="l%d" % r["id"], title=r["alt"] or "", url=murl(r["file"]), preview=murl(r["thumb"]), w=r["w"], h=r["h"], local=r["id"]))
    res = dict(provider=prov, items=out)
    _gc[key] = (time.time(), res)
    return jsonify(res)


# -------------------------------------------------------------------------------------- locais
_last_nom = [0.0]
_nc = {}


def nominatim(path, params):
    key = path + json.dumps(params, sort_keys=True)
    if key in _nc and time.time() - _nc[key][0] < 600:
        return _nc[key][1]
    wait = 1.05 - (time.time() - _last_nom[0])
    if wait > 0:
        time.sleep(wait)
    _last_nom[0] = time.time()
    url = "https://nominatim.openstreetmap.org/" + path + "?" + urllib.parse.urlencode(dict(params, format="jsonv2", **{"accept-language": "pt-BR"}))
    req = urllib.request.Request(url, headers={"User-Agent": os.environ.get("NOMINATIM_UA", "PracaApp/1.0 (rede social local)")})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read().decode())
    _nc[key] = (time.time(), d)
    return d


def hav(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(x))


def place_out(r, extra=None):
    return dict(dict(id=r["id"], name=r["name"], address=r["address"] or "", lat=r["lat"], lng=r["lng"]), **(extra or {}))


@bp.get("/api/places/search")
@member
def places_search():
    s = (request.args.get("q") or "").strip()[:80]
    lat, lng = request.args.get("lat", type=float), request.args.get("lng", type=float)
    out, degraded = [], False
    like = "%" + re.sub(r"[%_]", "", s.lower()) + "%"
    for r in q("select * from places where lower(name) like ? limit 12", (like,)) if s else []:
        d = hav(lat, lng, r["lat"], r["lng"]) if lat is not None and lng is not None else None
        out.append(place_out(r, dict(dist=d, known=True)))
    if lat is not None and lng is not None and not s:
        for r in q("select p.*,(select count(*) from posts where place_id=p.id) n from places p where abs(lat-?)<.05 and abs(lng-?)<.05", (lat, lng)):
            d = hav(lat, lng, r["lat"], r["lng"])
            if d < 4000:
                out.append(place_out(r, dict(dist=d, known=True, n=r["n"])))
        out.sort(key=lambda x: x["dist"])
    if s and len(s) >= 3:
        try:
            prm = dict(q=s, limit=8)
            if lat is not None and lng is not None:
                prm["viewbox"] = f"{lng - .25},{lat + .25},{lng + .25},{lat - .25}"
            for r in nominatim("search", prm):
                oid = r.get("osm_type", "x")[:1] + str(r.get("osm_id"))
                if any(o.get("osm_id") == oid for o in out):
                    continue
                if q("select 1 from places where osm_id=?", (oid,), one=True):
                    continue
                nm = r.get("name") or r["display_name"].split(",")[0]
                out.append(dict(osm_id=oid, name=nm, address=r["display_name"], lat=float(r["lat"]), lng=float(r["lon"]),
                                dist=hav(lat, lng, float(r["lat"]), float(r["lon"])) if lat is not None and lng is not None else None))
        except Exception:
            degraded = True
    return jsonify(items=out[:20], degraded=degraded)


@bp.get("/api/places/reverse")
@member
def places_reverse():
    lat, lng = request.args.get("lat", type=float), request.args.get("lng", type=float)
    if lat is None or lng is None:
        return jsonify(error="coordenadas inválidas"), 400
    try:
        r = nominatim("reverse", dict(lat=round(lat, 4), lon=round(lng, 4), zoom=18))
        return jsonify(name=r.get("name") or r.get("display_name", "").split(",")[0] or "Minha localização", address=r.get("display_name", ""),
                       lat=lat, lng=lng, osm_id=(r.get("osm_type", "x")[:1] + str(r.get("osm_id"))) if r.get("osm_id") else None)
    except Exception:
        return jsonify(name="Minha localização", address="", lat=lat, lng=lng, osm_id=None, degraded=True)


def upsert_place(d, uid):
    try:
        name, lat, lng = (d.get("name") or "").strip()[:100], float(d.get("lat")), float(d.get("lng"))
    except (TypeError, ValueError):
        return None
    if not name or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    osm = (d.get("osm_id") or "")[:40] or None
    r = q("select id from places where osm_id=?", (osm,), one=True) if osm else q(
        "select id from places where name=? and abs(lat-?)<.0006 and abs(lng-?)<.0006", (name, lat, lng), one=True)
    if r:
        return r["id"]
    return q("insert into places(name,address,lat,lng,osm_id,creator_id,created) values(?,?,?,?,?,?,?)",
             (name, (d.get("address") or "")[:200], lat, lng, osm, uid, time.time()), write=True)


@bp.get("/api/places/<int:pid>")
def place_get(pid):
    r = q("select * from places where id=?", (pid,), one=True)
    if not r:
        return jsonify(error="Local não encontrado."), 404
    me = g.user["id"] if g.user else 0
    rows = q("select user_id,created from posts where place_id=? order by id desc limit 500", (pid,))
    vis = [x for x in rows if allowed(x["user_id"], me, "locations")]
    return jsonify(place_out(r, dict(posts=len(vis), today=len([x for x in vis if time.time() - x["created"] < 86400]),
                                     people=len({x["user_id"] for x in vis}))))


@bp.get("/api/places/<int:pid>/feed")
def place_feed(pid):
    me = g.user["id"] if g.user else 0
    fn = FEED["rows"]
    rows = fn("and p.place_id=:pid", dict(pid=pid), me, request.args.get("before", type=int), 80)
    rows = [r for r in rows if allowed(r["uid"], me, "locations")][:30]
    return jsonify(FEED["out"](rows, me))


@bp.get("/api/places/trending")
def places_trending():
    me = g.user["id"] if g.user else 0
    rows = q("""select pl.id,pl.name,pl.address,pl.lat,pl.lng,count(*) n,max(p.created) last from posts p join places pl on pl.id=p.place_id
              where p.created>? group by pl.id order by n desc limit 6""", (time.time() - 7 * 86400,))
    return jsonify([dict(id=r["id"], name=r["name"], address=r["address"], n=r["n"]) for r in rows])


@bp.get("/api/users/<name>/places")
def user_places(name):
    u = q("select id from users where username=? and banned=0", (name.lower(),), one=True)
    me = g.user["id"] if g.user else 0
    if not u:
        return jsonify(error="Perfil não encontrado."), 404
    if not allowed(u["id"], me, "locations"):
        return jsonify(hidden=True, items=[])
    rows = q("""select pl.id,pl.name,count(*) n,max(p.created) last from posts p join places pl on pl.id=p.place_id
              where p.user_id=? group by pl.id order by last desc limit 20""", (u["id"],))
    return jsonify(hidden=False, items=[dict(r) for r in rows])


# ----------------------------------------------------------- posts: anexos, local e decoração
FEED = {}


def prepare_post(u, d):
    """Valida o conteúdo do post. Devolve (preparado, erro)."""
    text = (d.get("text") or "").strip()[:LIM["text"]]
    items = d.get("media") or []
    if not isinstance(items, list) or len(items) > LIM["max_carousel"]:
        return None, f"Um post aceita no máximo {LIM['max_carousel']} imagens."
    rows, gifs = [], []
    for it in items:
        if isinstance(it, int):
            r = q("select * from post_media where id=? and owner_id=? and post_id is null and ord>=0", (it, u["id"]), one=True)
            if not r:
                return None, "Uma das mídias expirou ou não é sua. Envie de novo."
            rows.append(("db", r))
        elif isinstance(it, dict) and isinstance(it.get("gif"), dict):
            gf = it["gif"]
            host = urllib.parse.urlparse(str(gf.get("url"))).hostname or ""
            if host not in GIF_HOSTS or not str(gf.get("url")).startswith("https://"):
                return None, "GIF de origem não permitida."
            rows.append(("gif", gf))
        else:
            return None, "Mídia inválida."
    vids = [1 for k, r in rows if k == "db" and r["kind"] == "video"]
    if vids and len(rows) > 1:
        return None, "Um post pode ter 1 vídeo OU até 10 imagens, não os dois."
    for k, r in rows:
        if k == "db" and r["status"] == "failed":
            return None, "Uma das mídias falhou no processamento. Remova e envie de novo."
    pid = None
    if d.get("place_id"):
        r = q("select id from places where id=?", (d.get("place_id"),), one=True)
        if not r:
            return None, "Local inválido."
        pid = r["id"]
    elif isinstance(d.get("place"), dict):
        pid = upsert_place(d["place"], u["id"])
        if not pid:
            return None, "Local inválido."
    if not text and not rows and not pid:
        return None, f"Escreva algo, anexe uma mídia ou marque um local (texto até {LIM['text']} caracteres)."
    return dict(text=text, rows=rows, place_id=pid), None


def finish_post(pid, prep, u):
    for i, (k, r) in enumerate(prep["rows"]):
        if k == "db":
            q("update post_media set post_id=?, ord=? where id=?", (pid, i, r["id"]), write=True)
        else:
            q("insert into post_media(post_id,owner_id,kind,file,thumb,w,h,ord,status,created,ext) values(?,?,?,?,?,?,?,?,?,?,1)",
              (pid, u["id"], "gif", r["url"], r.get("preview") or r["url"], r.get("w"), r.get("h"), i, "ready", time.time()), write=True)
    hub.broadcast("feed.new", dict(id=pid, author=u["username"]))


def decorate(rows, viewer):
    """Anexa mídia e local (respeitando o cadeado de localização) a linhas de post."""
    rows = [dict(r) for r in rows]
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    mm = {}
    for m in q(f"select * from post_media where post_id in ({','.join('?' * len(ids))}) order by ord,id", ids):
        mm.setdefault(m["post_id"], []).append(media_out(m))
    pl = {}
    pids = {r["place_id"] for r in rows if r.get("place_id")}
    if pids:
        for p in q(f"select * from places where id in ({','.join('?' * len(pids))})", list(pids)):
            pl[p["id"]] = place_out(p)
    for r in rows:
        r["media"] = mm.get(r["id"], [])
        r["place"] = pl.get(r["place_id"]) if r.get("place_id") and allowed(r["uid"], viewer, "locations") else None
        r.pop("place_id", None); r.pop("uid", None)
    return rows


# ------------------------------------------------------------------ instalação / limpeza
def setup(app, _q, _count, need_login, feed_rows, feed_out):
    global q, count, NEED
    q, count, NEED = _q, _count, need_login
    FEED["rows"], FEED["out"] = feed_rows, feed_out
    app.config["MAX_CONTENT_LENGTH"] = (LIM["max_video_mb"] + 5) * 1024 * 1024
    app.register_blueprint(bp)

    @app.errorhandler(413)
    def too_big(_):
        return jsonify(error=f"Arquivo grande demais (limite do servidor: {LIM['max_video_mb']} MB)."), 413


def init(path):
    import sqlite3
    c = sqlite3.connect(path)
    c.executescript("""
    create table if not exists close_friends(user_id integer references users(id) on delete cascade,
      friend_id integer references users(id) on delete cascade, primary key(user_id, friend_id));
    create table if not exists privacy(user_id integer primary key references users(id) on delete cascade,
      photos text default 'all', calls text default 'all', locations text default 'all', hide_search integer default 0);
    create table if not exists places(id integer primary key, name text not null, address text, lat real, lng real,
      osm_id text unique, creator_id integer, created real);
    create table if not exists post_media(id integer primary key, post_id integer references posts(id) on delete cascade,
      owner_id integer, kind text, file text, thumb text, w integer, h integer, duration real, size integer, variants text,
      ord integer default 0, alt text, status text default 'ready', ext integer default 0, library integer default 0, created real);
    create index if not exists ix_pm_post on post_media(post_id);
    create table if not exists calls(id text primary key, host_id integer, video integer, created real, started real, ended real, reason text, peak integer);
    create table if not exists rooms(id integer primary key, host_id integer, title text, tags text, privacy text, created real, ended real, peak integer, reason text);
    """)

    def cols(t):
        return [r[1] for r in c.execute(f"pragma table_info({t})")]
    for t, col, typ in (("users", "role", "text default 'member'"), ("posts", "place_id", "integer"), ("messages", "media_id", "integer")):
        if col not in cols(t):
            c.execute(f"alter table {t} add column {col} {typ}")
    c.execute("create index if not exists ix_posts_place on posts(place_id)")
    c.commit(); c.close()


def gc_loop():
    while True:
        time.sleep(3600)
        try:
            lim = time.time() - LIM["pending_ttl_h"] * 3600
            for r in q("select * from post_media where post_id is null and created<? and library=0 and ord>=0", (lim,)):
                drop_files(r)
                q("delete from post_media where id=?", (r["id"],), write=True)
            known = set()
            for r in q("select file,thumb,variants from post_media"):
                known |= {r["file"], r["thumb"]} | {v["file"] for v in json.loads(r["variants"] or "[]")}
            for fn in os.listdir(UP):
                p = os.path.join(UP, fn)
                if fn not in known and time.time() - os.path.getmtime(p) > LIM["pending_ttl_h"] * 3600:
                    os.remove(p)
        except Exception:
            pass
