"""Painel de administração AO VIVO (blueprint do Flask). Interface em /admin."""
import hmac, json, os, random, re, secrets, sqlite3, time
from werkzeug.security import check_password_hash
from functools import wraps
from flask import Blueprint, Response, jsonify, request, send_from_directory, session, g

bp = Blueprint("adm", __name__)
q = count = None
BASE = os.path.dirname(os.path.abspath(__file__))
PASSFILE = os.path.join(os.environ.get("DATA_DIR", BASE), ".adminpass")
CATS = {"odio": "Discurso de ódio", "assedio": "Assédio", "adulto": "Conteúdo adulto",
        "fake": "Fake news", "spam": "Spam/golpe", "ameaca": "Ameaça", "outro": "Outro"}
LEVELS = {"warn", "susp24", "susp7", "ban", "ipban"}
MOD_LEVELS = {"warn", "susp24", "susp7", "ban"}      # moderador não aplica BAN por IP
RANK = {"Urgente": 0, "Alta": 1, "Média": 2, "Baixa": 3}
LINK = re.compile(r"https?://|www\.", re.I)
fails = {}


def setup(app, _q, _count):
    global q, count
    q, count = _q, _count
    app.register_blueprint(bp)


def init(path):
    c = sqlite3.connect(path)
    c.executescript("""
    create table if not exists reports(id integer primary key, target_type text, target_id integer,
      reporter_id integer, category text, reason text, status text default 'open', decision text, created real);
    create table if not exists punishments(id integer primary key, user_id integer, level text, term text,
      reason text, created real, until real);
    create table if not exists ip_bans(ip text primary key, until real, reason text);
    create table if not exists audit(id integer primary key, actor text, action text, target text, detail text, created real);
    """)
    cols = [r[1] for r in c.execute("pragma table_info(users)")]
    for col, typ in (("last_ip", "text"), ("created_ip", "text"), ("ua", "text"),
                     ("suspended_until", "real"), ("captcha", "integer default 0")):
        if col not in cols:
            c.execute(f"alter table users add column {col} {typ}")
    if not c.execute("select 1 from users where username='praca'").fetchone():
        c.execute("insert into users(username,pass,display,verified,is_bot,created) values('praca','!','Praça · Moderação',1,1,?)", (time.time(),))
    c.commit(); c.close()
    pw = os.environ.get("ADMIN_PASS")
    if not pw:
        if not os.path.exists(PASSFILE):
            open(PASSFILE, "w").write(secrets.token_urlsafe(9))
        pw = open(PASSFILE).read().strip()
    bp.password = pw
    print(f"  Painel admin ao vivo: /admin   senha: {pw}")


def staff():
    s = session.get("adm")
    return s if isinstance(s, dict) else None


def audit(action, target, detail=""):
    s = staff() or {}
    q("insert into audit(actor,action,target,detail,created) values(?,?,?,?,?)",
      (f"{s.get('name', '?')} ({s.get('role', '?')})", action, target, detail, time.time()), write=True)


def need_admin(f):
    """Qualquer integrante da equipe (moderador ou administrador)."""
    @wraps(f)
    def w(*a, **k):
        if not staff():
            return jsonify(error="auth"), 401
        return f(*a, **k)
    return w


def admin_only(f):
    @wraps(f)
    def w(*a, **k):
        if not staff():
            return jsonify(error="auth"), 401
        if staff()["role"] != "admin":
            return jsonify(error="Apenas administradores podem fazer isso."), 403
        return f(*a, **k)
    return w


@bp.before_app_request
def ip_guard():
    if request.path.startswith("/api/") and request.method != "GET" or request.path in ("/api/login", "/api/register"):
        b = q("select * from ip_bans where ip=? and until>?", (request.remote_addr, time.time()), one=True)
        if b:
            return jsonify(error="banned", message="Acesso bloqueado para este IP. Motivo: " + (b["reason"] or "violação dos termos")), 403


# ------------------------------------------------------------------ denúncias
@bp.post("/api/report")
def report():
    if not g.user:
        return jsonify(error="auth"), 401
    d = request.get_json(silent=True) or {}
    t, i, cat = d.get("type"), d.get("id"), d.get("category")
    if t not in ("post", "user", "room") or cat not in CATS or not isinstance(i, int):
        return jsonify(error="Denúncia inválida."), 400
    owner = owner_of(t, i)
    if not owner or not q("select 1 from users where id=?", (owner,), one=True):
        return jsonify(error="Conteúdo não existe."), 404
    if owner == g.user["id"]:
        return jsonify(error="Você não pode denunciar a si mesmo."), 400
    if q("select 1 from reports where reporter_id=? and target_type=? and target_id=? and status='open'", (g.user["id"], t, i), one=True):
        return jsonify(error="Você já denunciou isto."), 400
    if count("select count(*) from reports where reporter_id=? and created>?", g.user["id"], time.time() - 3600) >= 10:
        return jsonify(error="Muitas denúncias em pouco tempo."), 429
    q("insert into reports(target_type,target_id,reporter_id,category,reason,created) values(?,?,?,?,?,?)",
      (t, i, g.user["id"], cat, (d.get("reason") or "")[:300], time.time()), write=True)
    return jsonify(ok=True)


def prio(cats, n):
    c = set(cats.split(","))
    if "ameaca" in c or n > 10:
        return "Urgente"
    if c & {"odio", "assedio"} and n >= 3:
        return "Alta"
    if c & {"adulto", "spam", "odio", "assedio"}:
        return "Média"
    return "Baixa"


def owner_of(t, i):
    if t == "post":
        r = q("select user_id from posts where id=?", (i,), one=True)
        return r[0] if r else None
    if t == "room":
        r = q("select host_id from rooms where id=?", (i,), one=True)
        return r[0] if r else None
    return i


@bp.get("/admin/api/tickets")
@need_admin
def tickets():
    rows = q("""select target_type t,target_id i,min(created) created,count(*) n,
        group_concat(distinct category) cats from reports where status='open' group by target_type,target_id""")
    out = []
    for r in rows:
        own = owner_of(r["t"], r["i"])
        u = q("select username from users where id=?", (own,), one=True) if own else None
        p = q("select text from posts where id=?", (r["i"],), one=True) if r["t"] == "post" else None
        rm = q("select title from rooms where id=?", (r["i"],), one=True) if r["t"] == "room" else None
        nm = q("select count(*) from post_media where post_id=?", (r["i"],), one=True)[0] if r["t"] == "post" else 0
        out.append(dict(type=r["t"], id=r["i"], created=r["created"], n=r["n"], cats=[CATS[c] for c in r["cats"].split(",")],
                        priority=prio(r["cats"], r["n"]), author=u["username"] if u else "(removido)",
                        preview=(("🎙 Mesa: " + rm["title"][:80]) if rm else ((p["text"][:90] or "(post só com mídia)") + (" 📎" * min(nm, 1)) if p else ("Perfil @" + u["username"] if u else "conteúdo removido")))))
    out.sort(key=lambda x: (RANK[x["priority"]], x["created"]))
    return jsonify(out)


@bp.get("/admin/api/ticket")
@need_admin
def ticket():
    t, i = request.args.get("type"), request.args.get("id", type=int)
    own = owner_of(t, i)
    author = q("select id,username,display,bio from users where id=?", (own,), one=True) if own else None
    post, ctx, media, room = None, [], [], None
    if t == "room":
        ri = q("select * from rooms where id=?", (i,), one=True)
        room = dict(ri) if ri else None
    if t == "post" and author:
        post = dict(q("select id,text,created,place_id from posts where id=?", (i,), one=True))
        import social
        media = [social.media_out(m) for m in q("select * from post_media where post_id=? order by ord,id", (i,))]
        pl = q("select name,address from places where id=?", (post["place_id"],), one=True) if post["place_id"] else None
        post["place"] = dict(pl) if pl else None
        ctx = [dict(r) for r in q("""select * from (select p.id,p.text,u.username from posts p join users u on u.id=p.user_id where p.id<? order by p.id desc limit 2)
              union all select * from (select p.id,p.text,u.username from posts p join users u on u.id=p.user_id where p.id>? order by p.id limit 2)""", (i, i))]
        ctx.sort(key=lambda x: x["id"])
    elif author:
        ctx = [dict(r) for r in q("select id,text from posts where user_id=? order by id desc limit 4", (own,))]
    reps = [dict(r, category=CATS[r["category"]]) for r in q("""select r.category,r.reason,r.created,u.username from reports r
        join users u on u.id=r.reporter_id where r.status='open' and r.target_type=? and r.target_id=?""", (t, i))]
    return jsonify(author=dict(author) if author else None, post=post, media=media, room=room, context=ctx, reports=reps,
                   prev=count("select count(*) from punishments where user_id=?", own) if own else 0)


@bp.post("/admin/api/ticket")
@need_admin
def ticket_action():
    d = request.get_json(silent=True) or {}
    t, i, act = d.get("type"), d.get("id"), d.get("action")
    if act == "delete" and t == "post":
        import social
        social.purge_post_media(i)
        q("delete from posts where id=?", (i,), write=True)
        social.hub.broadcast("post.deleted", dict(id=i))
    elif act == "delete" and t == "room":
        import live
        live.admin_end_room(i, "A Mesa foi encerrada pela moderação.")
    elif act not in ("keep", "delete"):
        return jsonify(error="Ação inválida"), 400
    q("update reports set status='resolved', decision=? where status='open' and target_type=? and target_id=?",
      ("manter" if act == "keep" else "deletar", t, i), write=True)
    audit("ticket." + act, f"{t}#{i}")
    return jsonify(ok=True)


# ------------------------------------------------------------ banimentos
def status_of(u):
    if u["banned"]:
        return "banido"
    if u["suspended_until"] and u["suspended_until"] > time.time():
        return "suspenso"
    return "ativo"


@bp.get("/admin/api/users")
@need_admin
def users():
    s, f = (request.args.get("q") or "").lower().strip(), request.args.get("f", "reported")
    rows = q("""select u.*,(select count(*) from reports r where r.status='open' and ((r.target_type='user' and r.target_id=u.id)
        or (r.target_type='post' and r.target_id in (select id from posts where user_id=u.id)))) rep,
        (select count(*) from punishments where user_id=u.id) prev from users u where u.is_bot=0 and u.username like ?
        order by rep desc, u.id desc limit 300""", ("%" + s + "%",))
    out = []
    for u in rows:
        st = status_of(u)
        if f == "reported" and not u["rep"] or f == "banned" and st == "ativo":
            continue
        out.append(dict(id=u["id"], username=u["username"], status=st, rep=u["rep"], prev=u["prev"], ip=u["last_ip"]))
    return jsonify(out[:100])


def score(u):
    s, sig = 0, []
    ps = q("select text,created from posts where user_id=? order by id desc limit 50", (u["id"],))
    ts = sorted(p["created"] for p in ps)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    if gaps and min(gaps) < 1:
        s += 30; sig.append(["Posts em milissegundos", 30])
    elif gaps and min(gaps) < 5:
        s += 12; sig.append(["Posts muito rápidos", 12])
    links = sum(1 for p in ps if LINK.search(p["text"]))
    if links >= 3 and links / len(ps) >= .5:
        s += 20; sig.append(["Spam de links", 20])
    if u["created_ip"]:
        n = count("select count(*) from users where created_ip=? and abs(created-?)<3600", u["created_ip"], u["created"])
        if n > 5:
            s += 20; sig.append([f"{n} contas no mesmo IP em 1 h", 20])
        elif n > 2:
            s += 8; sig.append([f"{n} contas no mesmo IP", 8])
    if time.time() - u["created"] < 86400 and len(ps) >= 20:
        s += 10; sig.append(["Conta nova com volume alto", 10])
    if re.search(r"bot|curl|python|headless|requests|selenium|puppeteer", u["ua"] or "", re.I):
        s += 10; sig.append(["Fingerprint de automação", 10])
    tx = [p["text"] for p in ps]
    if tx and max(tx.count(t) for t in set(tx)) >= 3:
        s += 10; sig.append(["Conteúdo duplicado", 10])
    return min(100, s), sig


@bp.get("/admin/api/user/<int:uid>")
@need_admin
def user_file(uid):
    u = q("select * from users where id=?", (uid,), one=True)
    if not u:
        return jsonify(error="Usuário não encontrado"), 404
    sc, sig = score(u)
    return jsonify(id=u["id"], username=u["username"], display=u["display"], bio=u["bio"], status=status_of(u),
                   reason=u["ban_reason"], until=u["suspended_until"], ip=u["last_ip"], created=u["created"],
                   score=sc, signals=sig, captcha=bool(u["captcha"]),
                   followers=count("select count(*) from follows where followed_id=?", uid),
                   history=[dict(r) for r in q("select * from punishments where user_id=? order by id desc", (uid,))],
                   posts=[dict(r) for r in q("select id,text,created from posts where user_id=? order by id desc limit 8", (uid,))],
                   open_reports=count("select count(*) from reports where status='open' and target_type='user' and target_id=?", uid),
                   same_ip=[r[0] for r in q("select username from users where created_ip=? and id!=? limit 12", (u["created_ip"], uid))] if u["created_ip"] else [])


@bp.post("/admin/api/punish")
@need_admin
def punish():
    d = request.get_json(silent=True) or {}
    u = q("select * from users where id=?", (d.get("user_id"),), one=True)
    lv, term, why = d.get("level"), d.get("term"), (d.get("reason") or "").strip()
    if not u or lv not in LEVELS or not term:
        return jsonify(error="Escolha o nível e o termo violado."), 400
    me = staff()
    if me["role"] != "admin" and lv not in MOD_LEVELS:
        return jsonify(error="BAN por IP/hardware é exclusivo de administradores."), 403
    if (u["role"] or "member") in ("moderator", "admin") and me["role"] != "admin":
        return jsonify(error="Moderadores não podem punir a equipe."), 403
    if len(why) < 20:
        return jsonify(error="A justificativa precisa ter pelo menos 20 caracteres."), 400
    now, msg, extra = time.time(), f"{term}: {why}", {}
    if lv == "warn":
        bot = q("select id from users where username='praca'", one=True)[0]
        q("insert into messages(sender_id,receiver_id,text,created) values(?,?,?,?)",
          (bot, u["id"], f"⚠️ Advertência da moderação. {msg}", now), write=True)
    elif lv in ("susp24", "susp7"):
        q("update users set suspended_until=?, ban_reason=? where id=?", (now + (86400 if lv == "susp24" else 604800), msg, u["id"]), write=True)
    else:
        q("update users set banned=1, ban_reason=? where id=?", (msg, u["id"]), write=True)
        if lv == "ipban":
            ip = u["created_ip"] or u["last_ip"]
            if ip:
                q("insert or replace into ip_bans values(?,?,?)", (ip, now + 30 * 86400, term), write=True)
                extra["accounts_on_ip"] = count("select count(*) from users where (created_ip=? or last_ip=?) and is_bot=0", ip, ip)
    q("insert into punishments(user_id,level,term,reason,created) values(?,?,?,?,?)", (u["id"], lv, term, why, now), write=True)
    if d.get("resolve"):
        q("update reports set status='resolved', decision='punir' where status='open' and target_type=? and target_id=?",
          (d["resolve"][0], d["resolve"][1]), write=True)
    audit("punir." + lv, "@" + u["username"], msg)
    if lv != "warn":
        import live
        live.kick_user(u["id"])
    return jsonify(ok=True, **extra)


@bp.post("/admin/api/revert")
@need_admin
def revert():
    u = q("select * from users where id=?", ((request.get_json(silent=True) or {}).get("user_id"),), one=True)
    if not u:
        return jsonify(error="Usuário não encontrado"), 404
    q("update users set banned=0, suspended_until=null, ban_reason=null, captcha=0 where id=?", (u["id"],), write=True)
    for ip in {u["created_ip"], u["last_ip"]} - {None}:
        q("delete from ip_bans where ip=?", (ip,), write=True)
    audit("reverter", "@" + u["username"])
    return jsonify(ok=True)


@bp.post("/admin/api/post/<int:pid>/delete")
@need_admin
def del_post(pid):
    import social
    social.purge_post_media(pid)
    q("delete from posts where id=?", (pid,), write=True)
    social.hub.broadcast("post.deleted", dict(id=pid))
    audit("post.apagar", f"post#{pid}")
    return jsonify(ok=True)


# ------------------------------------------------------------------ anti-bot
@bp.get("/admin/api/antibot")
@need_admin
def antibot():
    now, rows, sus = time.time(), [], set()
    for u in q("select * from users where is_bot=0 and banned=0 order by id desc limit 600"):
        sc, sig = score(u)
        if sc >= 20:
            n = count("select count(*) from posts where user_id=?", u["id"])
            rows.append(dict(id=u["id"], username=u["username"], score=sc, signals=[s[0] for s in sig], ip=u["created_ip"] or u["last_ip"],
                             age=int(now - u["created"]), posts=n, status=status_of(u), captcha=bool(u["captcha"])))
            if sc >= 40:
                sus.add(u["id"])
    rows.sort(key=lambda r: -r["score"])
    tot, bad = [0] * 60, [0] * 60
    for p in q("select user_id,created from posts where created>?", (now - 3600,)):
        b = min(59, int((p["created"] - (now - 3600)) / 60))
        tot[b] += 1
        if p["user_id"] in sus:
            bad[b] += 1
    return jsonify(accounts=rows[:200], total=tot, suspicious=bad)


@bp.post("/admin/api/bulk")
@need_admin
def bulk():
    d = request.get_json(silent=True) or {}
    ids, act, done, skipped = [i for i in (d.get("ids") or []) if isinstance(i, int)][:500], d.get("action"), 0, 0
    if act == "purge" and staff()["role"] != "admin":
        return jsonify(error="O expurgo em massa é exclusivo de administradores."), 403
    pre = {}
    if act == "purge":  # calcula todos os scores antes de apagar, para um expurgo não alterar o score dos demais
        for i in ids:
            u = q("select * from users where id=? and is_bot=0", (i,), one=True)
            pre[i] = score(u)[0] if u else 0
    for i in ids:
        u = q("select * from users where id=? and is_bot=0", (i,), one=True)
        if not u:
            skipped += 1
        elif act == "captcha":
            q("update users set captcha=1 where id=?", (i,), write=True); done += 1
        elif act == "suspend":
            q("update users set suspended_until=?, ban_reason='Suspensa para análise humana (anti-bot)' where id=?", (time.time() + 259200, i), write=True)
            q("insert into punishments(user_id,level,term,reason,created) values(?,?,?,?,?)", (i, "analise", "4.4 Spam/bots", "Suspensão em massa para análise humana", time.time()), write=True)
            done += 1
        elif act == "purge":
            if pre.get(i, 0) > 95:
                q("delete from users where id=?", (i,), write=True); done += 1
            else:
                skipped += 1
        else:
            return jsonify(error="Ação inválida"), 400
    audit("bulk." + str(act), f"{len(ids)} contas", f"aplicadas {done}, ignoradas {skipped}")
    return jsonify(done=done, skipped=skipped)


@bp.post("/admin/api/simulate")
@admin_only
def simulate():
    """Cria um ataque de bots fictício para você testar o painel."""
    now, ip = time.time(), "203.0.113." + str(random.randint(2, 250))
    for k in range(25):
        t0 = now - random.uniform(30, 1500)
        uid = q("insert into users(username,pass,display,created,created_ip,last_ip,ua) values(?,?,?,?,?,?,?)",
                ("spam_" + secrets.token_hex(3), "!", "Promo", t0, ip, ip, "python-requests/2.31"), write=True)
        for j in range(20):
            q("insert into posts(user_id,text,created) values(?,?,?)",
              (uid, "GANHE DINHEIRO RÁPIDO http://promo-falsa.example/x", t0 + j * 0.03), write=True)
    audit("simulação", "25 contas-bot", ip)
    return jsonify(ok=True)


# ------------------------------------------------------- stats, auditoria, live
@bp.get("/admin/api/audit")
@need_admin
def audit_log():
    return jsonify([dict(r) for r in q("select * from audit order by id desc limit 100")])


def signature():
    import live
    return dict(live=live.live_counts(), rep=count("select count(*) from reports where status='open'"), repmax=count("select coalesce(max(id),0) from reports"),
                users=count("select count(*) from users where is_bot=0"), banned=count("select count(*) from users where banned=1"),
                posts=count("select count(*) from posts"), pun=count("select count(*) from punishments"),
                msgs=count("select count(*) from messages"), aud=count("select coalesce(max(id),0) from audit"))


@bp.get("/admin/api/stats")
@need_admin
def stats():
    s = signature()
    s["suspended"] = count("select count(*) from users where suspended_until>?", time.time())
    return jsonify(s)


@bp.get("/admin/api/ai")
@admin_only
def ai_status():
    import ai
    rows = q("select id,text,created from posts where user_id=(select id from users where username='praca') order by id desc limit 15")
    return jsonify(enabled=ai.enabled(), model=ai.MODEL, every_min=int(os.environ.get("AI_POST_MIN", 120)), items=[dict(r) for r in rows])


@bp.post("/admin/api/ai/post")
@admin_only
def ai_post():
    import ai
    topic = (request.get_json(silent=True) or {}).get("topic") or None
    try:
        pid, t = ai.post_now(topic and str(topic)[:120])
    except RuntimeError as e:
        return jsonify(error=str(e)), 400
    except Exception:
        return jsonify(error="A API da Groq não respondeu. Verifique a chave e a conexão."), 502
    audit("ia.post", f"post#{pid}")
    return jsonify(ok=True, text=t)


@bp.get("/admin/api/live")
@need_admin
def live_now():
    import live
    return jsonify(rooms=live.admin_rooms(), counts=live.live_counts())


@bp.post("/admin/api/room/<int:rid>/end")
@need_admin
def room_end(rid):
    import live
    ok = live.admin_end_room(rid, "A Mesa foi encerrada pela moderação.")
    audit("mesa.encerrar", f"mesa#{rid}")
    return jsonify(ok=ok)


@bp.post("/admin/api/room/<int:rid>/kick")
@need_admin
def room_kick(rid):
    import live
    n = (request.get_json(silent=True) or {}).get("user", "")
    ok = live.admin_kick_from_room(rid, n)
    audit("mesa.remover", f"mesa#{rid}", "@" + n)
    return jsonify(ok=ok)


@bp.get("/admin/api/staff")
@admin_only
def staff_list():
    return jsonify([dict(id=r["id"], username=r["username"], role=r["role"]) for r in q("select id,username,role from users where role in ('moderator','admin') order by role,username")])


@bp.post("/admin/api/staff")
@admin_only
def staff_set():
    d = request.get_json(silent=True) or {}
    role = d.get("role")
    u = q("select * from users where username=? and is_bot=0", ((d.get("username") or "").lower().strip(),), one=True)
    if role not in ("moderator", "admin", "member") or not u:
        return jsonify(error="Usuário não encontrado ou papel inválido."), 400
    q("update users set role=? where id=?", (role, u["id"]), write=True)
    audit("equipe.papel", "@" + u["username"], role)
    return jsonify(ok=True)


@bp.get("/admin/api/stream")
@need_admin
def stream():
    def gen():
        last, tick = None, 0
        while True:
            sig = signature()
            if sig != last:
                last = sig
                yield "event: change\ndata: " + json.dumps(sig) + "\n\n"
            elif tick % 15 == 0:
                yield ": ping\n\n"
            tick += 1
            time.sleep(1)
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ login/UI
@bp.get("/admin")
def page():
    return send_from_directory(os.path.join(BASE, "static"), "admin.html")


@bp.post("/admin/api/login")
def login():
    ip, now = request.remote_addr, time.time()
    fl = [t for t in fails.get(ip, []) if now - t < 300]
    if len(fl) >= 5:
        return jsonify(error="Muitas tentativas. Aguarde 5 minutos."), 429
    d = request.get_json(silent=True) or {}
    pw, name = d.get("password", ""), (d.get("username") or "").strip().lower()
    if not name:      # senha-mestra do servidor => administrador
        if hmac.compare_digest(pw.encode(), bp.password.encode()):
            session["adm"] = dict(role="admin", name="mestre")
            return jsonify(ok=True, role="admin")
    else:             # conta da equipe (moderador/administrador), com a senha da própria conta
        u = q("select * from users where username=? and banned=0 and role in ('moderator','admin')", (name,), one=True)
        if u and check_password_hash(u["pass"], pw):
            session["adm"] = dict(role=u["role"], name="@" + u["username"])
            return jsonify(ok=True, role=u["role"])
    fails[ip] = fl + [now]
    return jsonify(error="Credenciais incorretas."), 400


@bp.post("/admin/api/logout")
def logout():
    session.pop("adm", None)
    return jsonify(ok=True)


@bp.get("/admin/api/me")
@need_admin
def me():
    return jsonify(ok=True, **staff())
