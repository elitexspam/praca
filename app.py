"""
Rede social completa em um único servidor Flask (sem Firebase, sem serviços externos).
Banco: SQLite (arquivo rede.db). Painel de administração: este próprio terminal.

    pip install flask
    python app.py
"""
import html as _html, logging, os, re, secrets, shlex, sqlite3, sys, threading, time
from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", BASE)          # em produção: disco persistente (ex.: /var/data)
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "rede.db")
KEY = os.path.join(DATA, ".secret")
if not os.path.exists(KEY) and not os.environ.get("SECRET_KEY"):
    open(KEY, "w").write(secrets.token_hex(32))

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
app.secret_key = os.environ.get("SECRET_KEY") or open(KEY).read()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER") or os.environ.get("HTTPS_ONLY")))
if os.environ.get("RENDER") or os.environ.get("PROXIES"):      # atrás de proxy (Render/Vercel): IP real e https corretos
    from werkzeug.middleware.proxy_fix import ProxyFix
    n = int(os.environ.get("PROXIES", 1))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)
USER_RE = re.compile(r"^[a-z0-9_]{3,20}$")

SCHEMA = """
create table if not exists users(
  id integer primary key, username text unique not null, pass text not null,
  display text, bio text default '', verified integer default 0,
  banned integer default 0, ban_reason text, is_bot integer default 0, created real);
create table if not exists posts(
  id integer primary key, user_id integer not null references users(id) on delete cascade,
  text text not null, created real);
create table if not exists likes(
  user_id integer references users(id) on delete cascade,
  post_id integer references posts(id) on delete cascade, primary key(user_id, post_id));
create table if not exists follows(
  follower_id integer references users(id) on delete cascade,
  followed_id integer references users(id) on delete cascade, primary key(follower_id, followed_id));
create table if not exists messages(
  id integer primary key, sender_id integer references users(id) on delete cascade,
  receiver_id integer references users(id) on delete cascade,
  text text not null, created real, read integer default 0);
"""


def q(sql, args=(), one=False, write=False):
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("pragma foreign_keys=on")
    try:
        cur = c.execute(sql, args)
        if write:
            c.commit()
            return cur.lastrowid if cur.lastrowid else cur.rowcount
        return cur.fetchone() if one else cur.fetchall()
    finally:
        c.close()


def init_db():
    c = sqlite3.connect(DB)
    c.execute("pragma journal_mode=wal")
    c.executescript(SCHEMA)
    c.commit(); c.close()


def count(sql, *a):
    return q(sql, a, one=True)[0]


def pub(u):
    return dict(username=u["username"], display=u["display"] or u["username"], bio=u["bio"] or "",
                verified=bool(u["verified"]),
                followers=count("select count(*) from follows where followed_id=?", u["id"]),
                following=count("select count(*) from follows where follower_id=?", u["id"]))


# ---------------------------------------------------------------- autenticação
@app.before_request
def guard():
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    u = q("select * from users where id=?", (uid,), one=True)
    if not u:
        session.clear()
    elif u["banned"]:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify(error="banned", message="Conta banida. Motivo: " + (u["ban_reason"] or "não informado")), 403
    elif u["suspended_until"] and u["suspended_until"] > time.time():
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify(error="banned", message="Conta suspensa até " + time.strftime("%d/%m %H:%M", time.localtime(u["suspended_until"]))
                           + ". Motivo: " + (u["ban_reason"] or "não informado")), 403
    else:
        g.user = u
        ip = request.remote_addr
        if u["last_ip"] != ip:
            q("update users set last_ip=?, created_ip=coalesce(created_ip,?), ua=coalesce(ua,?) where id=?",
              (ip, ip, (request.user_agent.string or "")[:200], u["id"]), write=True)


def need_login(f):
    from functools import wraps
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            return jsonify(error="auth"), 401
        if request.method != "GET" and g.user["captcha"]:
            if request.headers.get("X-Captcha") == str(session.get("cap")):
                q("update users set captcha=0 where id=?", (g.user["id"],), write=True)
                session.pop("cap", None)
            else:
                x, y = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
                session["cap"] = x + y
                return jsonify(error="captcha", question=f"{x} + {y}"), 428
        return f(*a, **k)
    return w


def text_of(max_len):
    t = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    return t[:max_len] if t else None


@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    name = (d.get("username") or "").strip().lower()
    pw = d.get("password") or ""
    if not USER_RE.match(name):
        return jsonify(error="Usuário: 3 a 20 caracteres, só letras, números e _."), 400
    if len(pw) < 6:
        return jsonify(error="A senha precisa ter pelo menos 6 caracteres."), 400
    if q("select 1 from users where username=?", (name,), one=True):
        return jsonify(error="Esse nome de usuário já existe."), 400
    uid = q("insert into users(username,pass,display,created) values(?,?,?,?)",
            (name, generate_password_hash(pw), (d.get("display") or name).strip()[:40], time.time()), write=True)
    session.permanent = True
    session["uid"] = uid
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = q("select * from users where username=?", ((d.get("username") or "").strip().lower(),), one=True)
    if not u or u["is_bot"] or not check_password_hash(u["pass"], d.get("password") or ""):
        return jsonify(error="Usuário ou senha incorretos."), 400
    if u["banned"]:
        return jsonify(error="banned", message="Conta banida. Motivo: " + (u["ban_reason"] or "não informado")), 403
    session.permanent = True
    session["uid"] = u["id"]
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@need_login
def me():
    return jsonify(dict(pub(g.user), role=g.user["role"] or "member", priv=social.priv(g.user["id"])))


@app.put("/api/me")
@need_login
def update_me():
    d = request.get_json(silent=True) or {}
    q("update users set display=?, bio=? where id=?",
      ((d.get("display") or g.user["username"]).strip()[:40], (d.get("bio") or "").strip()[:160], g.user["id"]), write=True)
    return jsonify(ok=True)


# ---------------------------------------------------------------------- posts
def feed_rows(extra, params, viewer, before=None, limit=30):
    sql = """select p.id,p.text,p.created,p.user_id uid,p.place_id,u.username,u.display,u.verified,
      (select count(*) from likes where post_id=p.id) likes,
      exists(select 1 from likes where post_id=p.id and user_id=:me) liked,
      (p.user_id=:me) mine
      from posts p join users u on u.id=p.user_id where u.banned=0 and p.id<:before %s order by p.id desc limit %d""" % (extra, limit)
    return q(sql, dict(params, me=viewer, before=before or 2 ** 62))


def posts_out(rows, viewer=0):
    rows = social.decorate(rows, viewer)
    return [dict(r, liked=bool(r["liked"]), mine=bool(r["mine"]), verified=bool(r["verified"]),
                 display=r["display"] or r["username"]) for r in rows]


@app.get("/api/feed")
@need_login
def feed():
    extra = ""
    if request.args.get("mode") == "following":
        extra = "and (p.user_id=:me or p.user_id in (select followed_id from follows where follower_id=:me))"
    me_ = g.user["id"]
    return jsonify(posts_out(feed_rows(extra, {}, me_, request.args.get("before", type=int)), me_))


@app.get("/api/public/feed")
def public_feed():
    """Feed aberto a visitantes (somente leitura)."""
    return jsonify(posts_out(feed_rows("", {}, 0, request.args.get("before", type=int), 20), 0))


@app.post("/api/posts")
@need_login
def new_post():
    prep, err = social.prepare_post(g.user, request.get_json(silent=True) or {})
    if err:
        return jsonify(error=err), 400
    pid = q("insert into posts(user_id,text,created,place_id) values(?,?,?,?)",
            (g.user["id"], prep["text"], time.time(), prep["place_id"]), write=True)
    social.finish_post(pid, prep, g.user)
    return jsonify(ok=True, id=pid)


@app.delete("/api/posts/<int:pid>")
@need_login
def del_post(pid):
    if q("select 1 from posts where id=? and user_id=?", (pid, g.user["id"]), one=True):
        social.purge_post_media(pid)
        q("delete from posts where id=? and user_id=?", (pid, g.user["id"]), write=True)
        social.hub.broadcast("post.deleted", dict(id=pid))
    return jsonify(ok=True)


@app.post("/api/posts/<int:pid>/like")
@need_login
def like(pid):
    if not q("select 1 from posts where id=?", (pid,), one=True):
        return jsonify(error="Post não existe."), 404
    if q("select 1 from likes where user_id=? and post_id=?", (g.user["id"], pid), one=True):
        q("delete from likes where user_id=? and post_id=?", (g.user["id"], pid), write=True)
    else:
        q("insert into likes values(?,?)", (g.user["id"], pid), write=True)
    n = count("select count(*) from likes where post_id=?", pid)
    social.hub.broadcast("post.likes", dict(id=pid, likes=n), skip=g.user["id"])
    return jsonify(liked=bool(q("select 1 from likes where user_id=? and post_id=?", (g.user["id"], pid), one=True)), likes=n)


# -------------------------------------------------------------------- perfis
def get_user(name):
    return q("select * from users where username=? and banned=0", (name.lower(),), one=True)


@app.get("/api/users/<name>")
def profile(name):
    """Perfil público: visitantes podem ver; o cadeado de localização vale para quem vê."""
    u = get_user(name)
    if not u:
        return jsonify(error="Perfil não encontrado."), 404
    me_ = g.user["id"] if g.user else 0
    rows = feed_rows("and p.user_id=:uid", {"uid": u["id"]}, me_, request.args.get("before", type=int))
    return jsonify(user=pub(u), me=u["id"] == me_, posts=posts_out(rows, me_), guest=not me_,
                   perm=social.perms(u["id"], me_),
                   close=bool(me_ and q("select 1 from close_friends where user_id=? and friend_id=?", (me_, u["id"]), one=True)),
                   following=bool(me_ and q("select 1 from follows where follower_id=? and followed_id=?", (me_, u["id"]), one=True)))


@app.post("/api/users/<name>/follow")
@need_login
def follow(name):
    u = get_user(name)
    if not u or u["id"] == g.user["id"]:
        return jsonify(error="Não dá para seguir este perfil."), 400
    k = (g.user["id"], u["id"])
    if q("select 1 from follows where follower_id=? and followed_id=?", k, one=True):
        q("delete from follows where follower_id=? and followed_id=?", k, write=True)
    else:
        q("insert into follows values(?,?)", k, write=True)
    return jsonify(ok=True)


@app.get("/api/search")
@need_login
def search():
    s = re.sub(r"[%_]", "", (request.args.get("q") or "").strip().lower())
    rows = q("select * from users where banned=0 and (username like ? or lower(display) like ?) limit 30",
             (s + "%", "%" + s + "%")) if s else q("select * from users where banned=0 order by id desc limit 30")
    return jsonify([pub(u) for u in rows])


# ------------------------------------------------------------------ mensagens
@app.get("/api/chats")
@need_login
def chats():
    me_ = g.user["id"]
    rows = q("""select u.username,u.display,m.text,m.media_id,m.created,
      (select count(*) from messages where sender_id=u.id and receiver_id=? and read=0) unread
      from users u join messages m on m.id=(select max(id) from messages
        where (sender_id=u.id and receiver_id=?) or (sender_id=? and receiver_id=u.id))
      where u.banned=0 and u.id!=? order by m.id desc""", (me_, me_, me_, me_))
    return jsonify([dict(username=r["username"], display=r["display"] or r["username"], text=r["text"] or "📷 Foto",
                         created=r["created"], unread=r["unread"]) for r in rows])


@app.get("/api/messages/<name>")
@need_login
def messages(name):
    u = get_user(name)
    if not u:
        return jsonify(error="Usuário não encontrado."), 404
    me_, after = g.user["id"], request.args.get("after", 0, type=int)
    rows = q("""select id,sender_id=? mine,text,created,media_id from messages
      where id>? and ((sender_id=? and receiver_id=?) or (sender_id=? and receiver_id=?)) order by id""",
             (me_, after, me_, u["id"], u["id"], me_))
    q("update messages set read=1 where sender_id=? and receiver_id=?", (u["id"], me_), write=True)
    out = []
    for r in rows:
        m = q("select * from post_media where id=?", (r["media_id"],), one=True) if r["media_id"] else None
        out.append(dict(id=r["id"], mine=bool(r["mine"]), text=r["text"], created=r["created"], media=social.media_out(m) if m else None))
    return jsonify(out)


@app.post("/api/messages/<name>")
@need_login
def send(name):
    u = get_user(name)
    d = request.get_json(silent=True) or {}
    t = (d.get("text") or "").strip()[:1000]
    mid = d.get("media_id")
    if not u or u["id"] == g.user["id"] or (not t and not mid):
        return jsonify(error="Mensagem inválida."), 400
    if mid:
        if not social.allowed(u["id"], g.user["id"], "photos"):
            return jsonify(error=f"@{u['username']} não aceita fotos de você."), 403
        m = q("select * from post_media where id=? and owner_id=? and post_id is null and kind in ('image','gif')", (mid, g.user["id"]), one=True)
        if not m:
            return jsonify(error="Foto inválida ou expirada."), 400
        q("update post_media set ord=-1 where id=?", (mid,), write=True)     # marca como anexo de DM (não expira)
    q("insert into messages(sender_id,receiver_id,text,created,media_id) values(?,?,?,?,?)",
      (g.user["id"], u["id"], t, time.time(), mid or None), write=True)
    social.hub.emit(u["id"], "chat.msg", dict(with_=g.user["username"]))
    return jsonify(ok=True)


import admin, social, live
admin.setup(app, q, count)
social.setup(app, q, count, need_login, feed_rows, posts_out)
live.setup(app, q, count)
import ai
ai.setup(q, count)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.after_request
def headers(r):
    r.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self), display-capture=(self)")
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    return r


@app.get("/u/<name>")
def public_profile(name):
    """Página pública do perfil (para visitantes e buscadores). 'Ocultar dos buscadores' => noindex."""
    u = get_user(name)
    if not u:
        return Response("Perfil não encontrado", 404)
    hide = social.priv(u["id"])["hide_search"]
    e = _html.escape
    p = pub(u)
    posts = [] if hide else q("select text from posts where user_id=? and text!='' order by id desc limit 10", (u["id"],))
    body = (f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{e(p['display'])} (@{e(u['username'])}) · Praça</title>"
            + ("<meta name='robots' content='noindex,noarchive,nosnippet,noimageindex'>" if hide else f"<meta name='description' content='{e(p['bio'] or 'Perfil na Praça')}'>")
            + f"</head><body style='font-family:system-ui;max-width:560px;margin:40px auto;padding:0 16px'><h1>{e(p['display'])}</h1><p>@{e(u['username'])}</p><p>{e(p['bio'])}</p>"
            + "".join(f"<p>{e(x['text'])}</p>" for x in posts)
            + f"<p><a href='/#/u/{e(u['username'])}'>Abrir na Praça</a></p></body></html>")
    r = Response(body, mimetype="text/html")
    if hide:
        r.headers["X-Robots-Tag"] = "noindex, noarchive, nosnippet, noimageindex"
    return r


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /api/\nDisallow: /admin\nDisallow: /media/\nSitemap: " + request.host_url + "sitemap.xml\n", mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    rows = q("""select u.username from users u left join privacy v on v.user_id=u.id where u.banned=0 and u.is_bot=0
                and coalesce(v.hide_search,0)=0 order by u.id desc limit 5000""")
    return Response("<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                    + "".join(f"<url><loc>{request.host_url}u/{_html.escape(r['username'])}</loc></url>" for r in rows) + "</urlset>", mimetype="application/xml")


# ============================================================ PAINEL DO TERMINAL
HELP = """
  usuarios [busca]          lista contas
  info <user>               detalhes de uma conta
  ban <user> [motivo]       bane (a pessoa é derrubada na hora)
  unban <user>              desbane
  seguidores <user> <n>     dá n seguidores (contas-robô)
  remover <user> <n>        tira n seguidores (robôs primeiro)
  verificar <user>          liga/desliga selo de verificado
  excluir <user>            apaga conta e tudo dela
  senha <user> <nova>       redefine senha
  posts [user]              lista últimos posts
  delpost <id>              apaga um post
  postar <user> <texto>     publica como o usuário
  stats                     números gerais
  sair                      encerra o servidor
"""


def find(name):
    u = q("select * from users where username=?", (name.lower(),), one=True)
    if not u:
        print("  ! usuário não encontrado")
    return u


def cmd(line):
    a = shlex.split(line)
    if not a:
        return
    c, a = a[0].lower(), a[1:]
    need = {"info": 1, "ban": 1, "unban": 1, "seguidores": 2, "remover": 2, "verificar": 1,
            "excluir": 1, "senha": 2, "delpost": 1, "postar": 2}
    if c in need and len(a) < need[c]:
        return print("  ! faltam argumentos (digite 'ajuda')")
    if c in ("ajuda", "help", "?"):
        print(HELP)
    elif c == "usuarios":
        s = "%" + (a[0].lower() if a else "") + "%"
        for u in q("select * from users where username like ? order by id", (s,)):
            fl = count("select count(*) from follows where followed_id=?", u["id"])
            tag = ("[BANIDO] " if u["banned"] else "") + ("[robô] " if u["is_bot"] else "") + ("[✔] " if u["verified"] else "")
            print(f"  #{u['id']:<4} @{u['username']:<20} {fl:>6} seguidores  {tag}")
    elif c == "info":
        if u := find(a[0]):
            p = pub(u)
            print(f"  @{u['username']} ({p['display']}) | seguidores {p['followers']} | seguindo {p['following']}"
                  f" | posts {count('select count(*) from posts where user_id=?', u['id'])}"
                  f"\n  bio: {p['bio'] or '-'}\n  banido: {'sim - ' + str(u['ban_reason']) if u['banned'] else 'não'}"
                  f" | verificado: {'sim' if u['verified'] else 'não'}")
    elif c == "ban":
        if u := find(a[0]):
            q("update users set banned=1, ban_reason=? where id=?", (" ".join(a[1:]) or None, u["id"]), write=True)
            print(f"  ✔ @{u['username']} banido")
    elif c == "unban":
        if u := find(a[0]):
            q("update users set banned=0, ban_reason=null where id=?", (u["id"],), write=True)
            print(f"  ✔ @{u['username']} desbanido")
    elif c in ("seguidores", "remover"):
        u = find(a[0])
        if u and a[1].isdigit():
            n = int(a[1])
            if c == "seguidores":
                for _ in range(n):
                    b = q("insert into users(username,pass,display,is_bot,created) values(?,?,?,1,?)",
                          ("bot_" + secrets.token_hex(4), "!", "Robô", time.time()), write=True)
                    q("insert into follows values(?,?)", (b, u["id"]), write=True)
                print(f"  ✔ +{n} seguidores para @{u['username']}")
            else:
                r = q("""select f.follower_id from follows f join users x on x.id=f.follower_id
                      where f.followed_id=? order by x.is_bot desc, f.rowid desc limit ?""", (u["id"], n))
                for x in r:
                    q("delete from follows where follower_id=? and followed_id=?", (x[0], u["id"]), write=True)
                print(f"  ✔ {len(r)} seguidores removidos de @{u['username']}")
        elif u:
            print("  ! n precisa ser um número")
    elif c == "verificar":
        if u := find(a[0]):
            q("update users set verified=1-verified where id=?", (u["id"],), write=True)
            print("  ✔ selo alternado")
    elif c == "excluir":
        if u := find(a[0]):
            q("delete from users where id=?", (u["id"],), write=True)
            print(f"  ✔ @{u['username']} excluído")
    elif c == "senha":
        if u := find(a[0]):
            q("update users set pass=? where id=?", (generate_password_hash(a[1]), u["id"]), write=True)
            print("  ✔ senha redefinida")
    elif c == "posts":
        rows = q("select p.id,u.username,p.text from posts p join users u on u.id=p.user_id "
                 + ("where u.username=? " if a else "") + "order by p.id desc limit 20", (a[0].lower(),) if a else ())
        for r in rows:
            print(f"  #{r['id']:<5} @{r['username']}: {r['text'][:80]}")
    elif c == "delpost":
        print("  ✔ apagado" if q("delete from posts where id=?", (a[0],), write=True) else "  ! post não existe")
    elif c == "postar":
        if u := find(a[0]):
            q("insert into posts(user_id,text,created) values(?,?,?)", (u["id"], " ".join(a[1:])[:280], time.time()), write=True)
            print("  ✔ publicado")
    elif c == "stats":
        print(f"  contas: {count('select count(*) from users where is_bot=0')} | robôs: {count('select count(*) from users where is_bot=1')}"
              f" | banidas: {count('select count(*) from users where banned=1')} | posts: {count('select count(*) from posts')}"
              f" | mensagens: {count('select count(*) from messages')}")
    elif c in ("sair", "exit", "quit"):
        os._exit(0)
    else:
        print("  ! comando desconhecido (digite 'ajuda')")


def panel():
    time.sleep(0.8)
    print("\n  Painel admin pronto. Digite 'ajuda' para ver os comandos.\n")
    while True:
        try:
            cmd(input("admin> "))
        except EOFError:
            return
        except Exception as e:
            print("  ! erro:", e)


def bootstrap():
    init_db()
    admin.init(DB)
    social.init(DB)


bootstrap()          # roda também sob gunicorn (import), de forma idempotente


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Rede no ar: http://localhost:{port}  (outros aparelhos: http://IP-DO-PC:{port})")
    if sys.stdin.isatty():
        threading.Thread(target=panel, daemon=True).start()
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
