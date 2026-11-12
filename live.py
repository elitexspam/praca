"""Praça · ao vivo: chamadas de voz/vídeo (1:1 e grupo até 20) e Mesas de Debate (salas de áudio).
O servidor só faz a sinalização (WebRTC) e as regras; o áudio/vídeo trafega entre os navegadores."""
import json, os, secrets, threading, time
from flask import Blueprint, g, jsonify, request
import social
from social import hub, member, J, allowed, rate

bp = Blueprint("live", __name__)
q = count = None
L = threading.RLock()

CFG = dict(
    call_max=20, ring_sec=45, call_max_sec=4 * 3600, grace_sec=20,
    room_max_speakers=int(os.environ.get("ROOM_MAX_SPEAKERS", 10)),
    room_max_listeners=int(os.environ.get("ROOM_MAX_LISTENERS", 200)),
    room_max_sec=8 * 3600, room_title=80, room_tags=5, chat_len=200,
)
CALLS, USER_CALL = {}, {}
ROOMS, USER_ROOM = {}, {}
EMOJIS = {"👏", "❤️", "😂", "🔥", "👍", "😮"}


def person(uid):
    r = q("select username,display from users where id=?", (uid,), one=True)
    return dict(username=r["username"], display=r["display"] or r["username"]) if r else dict(username="?", display="?")


def uid_of(name):
    r = q("select id from users where username=? and banned=0", ((name or "").lower(),), one=True)
    return r["id"] if r else None


@bp.get("/api/rtc-config")
def rtc_config():
    ice = [{"urls": os.environ.get("STUN_URL", "stun:stun.l.google.com:19302")}]
    if os.environ.get("TURN_URL"):
        ice.append({"urls": os.environ["TURN_URL"], "username": os.environ.get("TURN_USER", ""), "credential": os.environ.get("TURN_PASS", "")})
    return jsonify(iceServers=ice, call_max=CFG["call_max"], ring_sec=CFG["ring_sec"],
                   room_max_speakers=CFG["room_max_speakers"], room_max_listeners=CFG["room_max_listeners"], turn=bool(os.environ.get("TURN_URL")))


# =================================================================================== CHAMADAS
def csnap(c):
    return dict(id=c["id"], video=c["video"], host=c["host_name"], started=c["started"], ring_until=c["ring_until"], created=c["created"],
                parts=[dict(username=p["username"], display=p["display"], state=p["state"], muted=p["muted"], cam=p["cam"], screen=p["screen"])
                       for p in c["parts"].values()])


def cemit(c, ev, data=None, states=("joined",)):
    d = data if data is not None else csnap(c)
    for uid, p in c["parts"].items():
        if p["state"] in states:
            hub.emit(uid, ev, d)


def new_part(uid, state):
    pp = person(uid)
    return dict(username=pp["username"], display=pp["display"], state=state, muted=False, cam=False, screen=False, since=time.time())


def dm_log(a, b, text):
    now = time.time()
    mid = q("insert into messages(sender_id,receiver_id,text,created) values(?,?,?,?)", (a, b, text, now), write=True)
    for u in (a, b):
        hub.emit(u, "chat.msg", dict(id=mid, with_=person(b if u == a else a)["username"], mine=(u == a), text=text, created=now))


def end_call(c, reason):
    with L:
        if c["id"] not in CALLS:
            return
        CALLS.pop(c["id"], None)
        joined = [u for u, p in c["parts"].items() if p["state"] == "joined"]
        dur = int(time.time() - c["started"]) if c["started"] else 0
        for u in c["parts"]:
            if USER_CALL.get(u) == c["id"]:
                USER_CALL.pop(u, None)
        for u, p in c["parts"].items():
            if p["state"] == "ringing":
                hub.emit(u, "call.cancelled", dict(id=c["id"], reason=reason))
                p["state"] = "missed"
            elif u in joined or p["state"] in ("joined", "left"):
                hub.emit(u, "call.ended", dict(id=c["id"], reason=reason, duration=dur))
        q("insert into calls(id,host_id,video,created,started,ended,reason,peak) values(?,?,?,?,?,?,?,?)",
          (c["id"], c["host"], 1 if c["video"] else 0, c["created"], c["started"], time.time(), reason, c["peak"]), write=True)
        if len(c["parts"]) == 2:
            other = [u for u in c["parts"] if u != c["host"]][0]
            kind = "vídeo" if c["video"] else "voz"
            if c["started"]:
                m, s = divmod(dur, 60)
                dm_log(c["host"], other, f"📞 Chamada de {kind} · {m:02d}:{s:02d}")
            else:
                dm_log(c["host"], other, f"📞 Chamada de {kind} perdida")


def leave_call(c, uid, state="left"):
    with L:
        p = c["parts"].get(uid)
        if not p:
            return
        p["state"] = state
        if USER_CALL.get(uid) == c["id"]:
            USER_CALL.pop(uid, None)
        alive = [u for u, x in c["parts"].items() if x["state"] == "joined"]
        ringing = [u for u, x in c["parts"].items() if x["state"] == "ringing"]
        if len(alive) <= 1 and not ringing:
            end_call(c, "ended" if c["started"] else state)
        else:
            cemit(c, "call.state")
            hub.emit(uid, "call.ended", dict(id=c["id"], reason="left", duration=0))


@bp.post("/api/calls")
@member
def call_create():
    me, d = g.user, J()
    names, seen = [], set()
    for n in d.get("to") or []:
        n = str(n).lower()
        if n != me["username"] and n not in seen:
            seen.add(n); names.append(n)
    if not names:
        return jsonify(error="Escolha quem você quer chamar."), 400
    if len(names) > CFG["call_max"] - 1:
        return jsonify(error=f"Uma chamada comporta no máximo {CFG['call_max']} pessoas."), 400
    if not rate(("call", me["id"]), 40, 3600):
        return jsonify(error="Muitas chamadas em pouco tempo."), 429
    with L:
        if USER_CALL.get(me["id"]) or USER_ROOM.get(me["id"]):
            return jsonify(error="Você já está em uma chamada ou Mesa. Saia antes de iniciar outra."), 409
        targets = []
        for n in names:
            u = q("select * from users where username=? and banned=0", (n,), one=True)
            if not u:
                return jsonify(error=f"@{n} não existe."), 404
            if not allowed(u["id"], me["id"], "calls"):
                return jsonify(error=f"@{n} não aceita chamadas de você."), 403
            targets.append(u)
        c = dict(id=secrets.token_urlsafe(8), host=me["id"], host_name=me["username"], video=bool(d.get("video")), created=time.time(),
                 started=None, ring_until=time.time() + CFG["ring_sec"], parts={me["id"]: new_part(me["id"], "joined")}, peak=1)
        for u in targets:
            st = "busy" if (USER_CALL.get(u["id"]) or USER_ROOM.get(u["id"])) else "ringing" if hub.online(u["id"]) else "offline"
            c["parts"][u["id"]] = new_part(u["id"], st)
        ringing = [u for u, p in c["parts"].items() if p["state"] == "ringing"]
        if not ringing:
            reasons = {"busy": "está em outra chamada", "offline": "está offline no momento"}
            msg = "; ".join(f"@{p['username']} {reasons[p['state']]}" for u, p in c["parts"].items() if u != me["id"])
            if len(c["parts"]) == 2:
                other = [u for u in c["parts"] if u != me["id"]][0]
                if c["parts"][other]["state"] == "offline":
                    dm_log(me["id"], other, f"📞 Chamada de {'vídeo' if c['video'] else 'voz'} perdida")
            return jsonify(error=msg.capitalize() + "."), 409
        CALLS[c["id"]] = c
        USER_CALL[me["id"]] = c["id"]
        who = person(me["id"])
        for u in ringing:
            hub.emit(u, "call.incoming", dict(id=c["id"], video=c["video"], **{"from": who}, group=len(c["parts"]) > 2,
                                              others=[p["display"] for x, p in c["parts"].items() if x not in (u, me["id"])][:6]))
        return jsonify(csnap(c))


def get_call(cid, uid, states=("joined", "ringing")):
    c = CALLS.get(cid)
    if not c or uid not in c["parts"] or c["parts"][uid]["state"] not in states:
        return None
    return c


@bp.post("/api/calls/<cid>/accept")
@member
def call_accept(cid):
    with L:
        c = get_call(cid, g.user["id"], ("ringing",))
        if not c:
            return jsonify(error="Esta chamada já terminou."), 404
        if USER_CALL.get(g.user["id"]) or USER_ROOM.get(g.user["id"]):
            return jsonify(error="Você já está em outra chamada ou Mesa."), 409
        p = c["parts"][g.user["id"]]
        p["state"] = "joined"
        c["started"] = c["started"] or time.time()
        USER_CALL[g.user["id"]] = cid
        c["peak"] = max(c["peak"], len([1 for x in c["parts"].values() if x["state"] == "joined"]))
        for u, x in c["parts"].items():           # outros aparelhos do mesmo usuário deixam de tocar
            pass
        hub.emit(g.user["id"], "call.answered_elsewhere", dict(id=cid))
        cemit(c, "call.state")
        return jsonify(csnap(c))


@bp.post("/api/calls/<cid>/decline")
@member
def call_decline(cid):
    with L:
        c = get_call(cid, g.user["id"], ("ringing",))
        if c:
            leave_call(c, g.user["id"], "declined")
    return jsonify(ok=True)


@bp.post("/api/calls/<cid>/leave")
@member
def call_leave(cid):
    with L:
        c = get_call(cid, g.user["id"], ("joined",))
        if c:
            leave_call(c, g.user["id"])
    return jsonify(ok=True)


@bp.post("/api/calls/<cid>/invite")
@member
def call_invite(cid):
    me = g.user
    with L:
        c = get_call(cid, me["id"], ("joined",))
        if not c:
            return jsonify(error="Chamada não encontrada."), 404
        added = 0
        for n in J().get("to") or []:
            u = q("select * from users where username=? and banned=0", (str(n).lower(),), one=True)
            if not u or u["id"] in c["parts"] and c["parts"][u["id"]]["state"] in ("joined", "ringing"):
                continue
            if len([1 for p in c["parts"].values() if p["state"] in ("joined", "ringing")]) >= CFG["call_max"]:
                return jsonify(error=f"A chamada já tem {CFG['call_max']} pessoas."), 400
            if not allowed(u["id"], me["id"], "calls"):
                return jsonify(error=f"@{u['username']} não aceita chamadas de você."), 403
            st = "busy" if (USER_CALL.get(u["id"]) or USER_ROOM.get(u["id"])) else "ringing" if hub.online(u["id"]) else "offline"
            c["parts"][u["id"]] = new_part(u["id"], st)
            if st == "ringing":
                added += 1
                c["parts"][u["id"]]["since"] = time.time()
                hub.emit(u["id"], "call.incoming", dict(id=cid, video=c["video"], **{"from": person(me["id"])}, group=True, others=[]))
        c["ring_until"] = max(c["ring_until"], time.time() + CFG["ring_sec"])
        cemit(c, "call.state")
        return jsonify(csnap(c))


@bp.post("/api/calls/<cid>/signal")
@member
def call_signal(cid):
    d = J()
    with L:
        c = get_call(cid, g.user["id"], ("joined",))
        to = uid_of(d.get("to"))
        if not c or not to or c["parts"].get(to, {}).get("state") != "joined":
            return jsonify(error="gone"), 404
    hub.emit(to, "call.signal", dict(id=cid, **{"from": g.user["username"]}, data=d.get("data")))
    return jsonify(ok=True)


@bp.post("/api/calls/<cid>/state")
@member
def call_state(cid):
    d = J()
    with L:
        c = get_call(cid, g.user["id"], ("joined",))
        if not c:
            return jsonify(error="gone"), 404
        p = c["parts"][g.user["id"]]
        for k in ("muted", "cam", "screen"):
            if k in d:
                p[k] = bool(d[k])
        cemit(c, "call.state")
    return jsonify(ok=True)


# ============================================================================ MESAS DE DEBATE
def rsnap(r, viewer=None):
    ms = r["members"]
    sp = [dict(username=m["username"], display=m["display"], role=m["role"], muted=m["muted"], ver=m["ver"]) for m in ms.values() if m["role"] != "listener"]
    ls = [m for m in ms.values() if m["role"] == "listener"]
    return dict(id=r["id"], title=r["title"], tags=r["tags"], privacy=r["privacy"], host=r["host_name"], created=r["created"],
                speakers=sp, listeners=[dict(username=m["username"], display=m["display"], hand=m["hand"], ver=m["ver"]) for m in ls],
                listener_count=len(ls), hands=[m["username"] for m in sorted(ls, key=lambda x: x["hand_at"]) if m["hand"]],
                max_speakers=CFG["room_max_speakers"])


def remit(r, ev, data=None, skip=None):
    d = data if data is not None else rsnap(r)
    for uid in list(r["members"]):
        if uid != skip:
            hub.emit(uid, ev, d)


_rb = {"t": 0, "timer": None}


def rooms_changed():
    """Avisa todo mundo (inclusive visitantes) que a lista de Mesas mudou — no máx. 1 aviso por 1,5 s."""
    now = time.time()
    if now - _rb["t"] > 1.5:
        _rb["t"] = now
        hub.broadcast("rooms.changed", {})
    elif not _rb["timer"]:
        def later():
            _rb["timer"] = None; _rb["t"] = time.time(); hub.broadcast("rooms.changed", {})
        _rb["timer"] = threading.Timer(1.5, later); _rb["timer"].daemon = True; _rb["timer"].start()


def can_see_room(r, uid):
    if r["privacy"] == "public":
        return True
    if not uid:
        return False
    if uid == r["host"] or uid in r["members"]:
        return True
    if r["privacy"] == "followers":
        return bool(q("select 1 from follows where follower_id=? and followed_id=?", (uid, r["host"]), one=True))
    return uid in r["invited"]


def end_room(r, reason, by=None):
    with L:
        if r["id"] not in ROOMS:
            return
        ROOMS.pop(r["id"], None)
        remit(r, "room.ended", dict(id=r["id"], reason=reason))
        for u in list(r["members"]):
            if USER_ROOM.get(u) == r["id"]:
                USER_ROOM.pop(u, None)
        q("update rooms set ended=?, peak=?, reason=? where id=?", (time.time(), r["peak"], reason, r["id"]), write=True)
    rooms_changed()


def drop_member(r, uid, reason="left"):
    with L:
        m = r["members"].pop(uid, None)
        if not m:
            return
        if USER_ROOM.get(uid) == r["id"]:
            USER_ROOM.pop(uid, None)
        if reason != "left":
            hub.emit(uid, "room.kicked", dict(id=r["id"], reason=reason))
        if uid == r["host"]:
            nxt = next((u for u, x in r["members"].items() if x["role"] == "cohost"), None) or next((u for u, x in r["members"].items() if x["role"] == "speaker"), None)
            if not nxt:
                return end_room(r, "O anfitrião saiu e não havia ninguém para assumir a Mesa.")
            r["host"], r["host_name"] = nxt, r["members"][nxt]["username"]
            r["members"][nxt]["role"] = "host"; r["members"][nxt]["ver"] += 1
            q("update rooms set host_id=? where id=?", (nxt, r["id"]), write=True)
        if not r["members"]:
            return end_room(r, "A Mesa ficou vazia.")
        remit(r, "room.roster")
    rooms_changed()


def room_list(uid):
    out = []
    for r in ROOMS.values():
        if can_see_room(r, uid):
            sp = [m["display"] for m in r["members"].values() if m["role"] != "listener"]
            out.append(dict(id=r["id"], title=r["title"], tags=r["tags"], privacy=r["privacy"], host=person(r["host"]), created=r["created"],
                            speakers=sp[:6], speaker_count=len(sp), listeners=len(r["members"]) - len(sp), joined=bool(uid and uid in r["members"])))
    out.sort(key=lambda x: -(x["listeners"] + x["speaker_count"]))
    return out


@bp.get("/api/rooms")
def rooms_get():
    return jsonify(room_list(g.user["id"] if g.user else 0))


@bp.post("/api/rooms")
@member
def room_create():
    me, d = g.user, J()
    title = (d.get("title") or "").strip()[:CFG["room_title"]]
    if len(title) < 3:
        return jsonify(error="Dê um título à Mesa (mín. 3 caracteres)."), 400
    tags = []
    for t in d.get("tags") or []:
        t = "".join(ch for ch in str(t).lower() if ch.isalnum() or ch in "-_")[:20]
        if t and t not in tags:
            tags.append(t)
    tags = tags[:CFG["room_tags"]]
    pv = d.get("privacy") if d.get("privacy") in ("public", "followers", "invite") else "public"
    invited = {uid_of(n) for n in d.get("invited") or []} - {None}
    if not rate(("room", me["id"]), 10, 3600):
        return jsonify(error="Muitas Mesas criadas em pouco tempo."), 429
    with L:
        if USER_CALL.get(me["id"]) or USER_ROOM.get(me["id"]):
            return jsonify(error="Você já está em uma chamada ou Mesa."), 409
        rid = q("insert into rooms(host_id,title,tags,privacy,created) values(?,?,?,?,?)", (me["id"], title, ",".join(tags), pv, time.time()), write=True)
        pp = person(me["id"])
        r = dict(id=rid, host=me["id"], host_name=me["username"], title=title, tags=tags, privacy=pv, invited=invited, created=time.time(),
                 members={}, chat=[], banned=set(), peak=1, last_react={})
        r["members"][me["id"]] = dict(username=pp["username"], display=pp["display"], role="host", muted=False, hand=False, hand_at=0, ver=1, since=time.time(), invited_speak=False)
        ROOMS[rid] = r
        USER_ROOM[me["id"]] = rid
        for u in invited:
            hub.emit(u, "room.invited", dict(id=rid, title=title, host=pp))
    rooms_changed()
    return jsonify(rsnap(r))


def room_of(rid, uid):
    r = ROOMS.get(rid)
    return (r, r["members"].get(uid)) if r else (None, None)


@bp.post("/api/rooms/<int:rid>/join")
@member
def room_join(rid):
    me = g.user
    with L:
        r = ROOMS.get(rid)
        if not r:
            return jsonify(error="Esta Mesa já foi encerrada."), 404
        if me["id"] in r["banned"]:
            return jsonify(error="Você foi removido desta Mesa."), 403
        if not can_see_room(r, me["id"]):
            return jsonify(error="Esta Mesa é restrita (somente seguidores ou convidados)."), 403
        if USER_CALL.get(me["id"]):
            return jsonify(error="Você está em uma chamada. Encerre-a antes de entrar na Mesa."), 409
        if USER_ROOM.get(me["id"]) and USER_ROOM[me["id"]] != rid:
            return jsonify(error="Você já está em outra Mesa."), 409
        if me["id"] not in r["members"]:
            if len(r["members"]) >= CFG["room_max_speakers"] + CFG["room_max_listeners"]:
                return jsonify(error="Mesa lotada."), 409
            pp = person(me["id"])
            r["members"][me["id"]] = dict(username=pp["username"], display=pp["display"], role="listener", muted=True, hand=False, hand_at=0, ver=1, since=time.time(), invited_speak=False)
            r["peak"] = max(r["peak"], len(r["members"]))
            USER_ROOM[me["id"]] = rid
            remit(r, "room.roster", skip=me["id"])
            rooms_changed()
        return jsonify(dict(rsnap(r), you=r["members"][me["id"]]["role"], chat=r["chat"][-60:]))


@bp.post("/api/rooms/<int:rid>/leave")
@member
def room_leave(rid):
    with L:
        r = ROOMS.get(rid)
        if r and g.user["id"] in r["members"]:
            drop_member(r, g.user["id"])
    return jsonify(ok=True)


def host_like(m):
    return m and m["role"] in ("host", "cohost")


@bp.post("/api/rooms/<int:rid>/<action>")
@member
def room_action(rid, action):
    me, d = g.user, J()
    with L:
        r, m = room_of(rid, me["id"])
        if not r or not m:
            return jsonify(error="Você não está nesta Mesa."), 404
        who = d.get("user") or d.get("to")
        tid = uid_of(who) if who else None
        t = r["members"].get(tid) if tid else None
        if action == "hand":
            if m["role"] != "listener":
                return jsonify(ok=True)
            up = bool(d.get("up", True))
            m["hand"], m["hand_at"] = up, time.time()
            remit(r, "room.roster")
            if up:
                for u, x in r["members"].items():
                    if host_like(x):
                        hub.emit(u, "room.hand", dict(username=m["username"], display=m["display"]))
        elif action == "invite_speaker":
            if not host_like(m) or not t or t["role"] != "listener":
                return jsonify(error="Ação não permitida."), 403
            if len([1 for x in r["members"].values() if x["role"] != "listener"]) >= CFG["room_max_speakers"]:
                return jsonify(error=f"O palco comporta {CFG['room_max_speakers']} oradores."), 409
            t["invited_speak"] = True
            hub.emit(tid, "room.invite_speaker", dict(id=rid, by=m["display"]))
        elif action == "accept_speaker":
            if m["role"] != "listener" or not m["invited_speak"]:
                return jsonify(error="Convite expirado."), 409
            if len([1 for x in r["members"].values() if x["role"] != "listener"]) >= CFG["room_max_speakers"]:
                return jsonify(error="O palco já está lotado."), 409
            m.update(role="speaker", hand=False, invited_speak=False, ver=m["ver"] + 1, muted=True)
            remit(r, "room.roster")
        elif action == "decline_speaker":
            m["invited_speak"] = False; m["hand"] = False
            remit(r, "room.roster")
        elif action == "demote":
            tt = t or (m if d.get("user") in (None, "", me["username"]) else None)
            if not tt or tt["role"] in ("host",) or (tt is not m and not host_like(m)):
                return jsonify(error="Ação não permitida."), 403
            if tt["role"] == "cohost" and m["role"] != "host":
                return jsonify(error="Só o anfitrião pode mexer em co-anfitriões."), 403
            tt.update(role="listener", muted=True, ver=tt["ver"] + 1, hand=False)
            remit(r, "room.roster")
        elif action == "mute":
            if not host_like(m) or not t or t["role"] == "listener":
                return jsonify(error="Ação não permitida."), 403
            t["muted"] = True
            hub.emit(tid, "room.forced_mute", dict(id=rid))
            remit(r, "room.roster")
        elif action == "state":
            if m["role"] == "listener":
                return jsonify(ok=True)
            m["muted"] = bool(d.get("muted"))
            remit(r, "room.roster")
        elif action == "cohost":
            if m["role"] != "host" or not t or t["role"] == "host":
                return jsonify(error="Só o anfitrião pode fazer isso."), 403
            on = bool(d.get("on", True))
            t["role"] = "cohost" if on else "speaker"
            if on and t["role"] == "cohost":
                pass
            t["ver"] += 1
            remit(r, "room.roster")
        elif action == "kick":
            if not host_like(m) or not t or t["role"] in ("host",) or (t["role"] == "cohost" and m["role"] != "host"):
                return jsonify(error="Ação não permitida."), 403
            r["banned"].add(tid)
            drop_member(r, tid, "Você foi removido da Mesa pelo anfitrião.")
        elif action == "end":
            if m["role"] != "host":
                return jsonify(error="Só o anfitrião encerra a Mesa."), 403
            end_room(r, "O anfitrião encerrou a Mesa.")
        elif action == "react":
            e = d.get("emoji")
            if e not in EMOJIS or time.time() - r["last_react"].get(me["id"], 0) < 0.4:
                return jsonify(ok=True)
            r["last_react"][me["id"]] = time.time()
            remit(r, "room.react", dict(emoji=e, from_=m["display"]))
        elif action == "chat":
            txt = (d.get("text") or "").strip()[:CFG["chat_len"]]
            if not txt or not rate(("rc", me["id"]), 12, 10):
                return jsonify(ok=True)
            msg = dict(username=m["username"], display=m["display"], text=txt, t=time.time())
            r["chat"] = (r["chat"] + [msg])[-100:]
            remit(r, "room.chat", msg)
        elif action == "signal":
            if tid and tid in r["members"]:
                hub.emit(tid, "room.signal", dict(id=rid, **{"from": me["username"]}, data=d.get("data")))
        else:
            return jsonify(error="Ação desconhecida."), 400
        return jsonify(ok=True)


# ================================================================ estado para retomar após reconexão
@bp.get("/api/live-state")
@member
def live_state():
    uid = g.user["id"]
    with L:
        cid, rid = USER_CALL.get(uid), USER_ROOM.get(uid)
        c, r = CALLS.get(cid), ROOMS.get(rid)
        pend = [dict(id=c2["id"], video=c2["video"], **{"from": person(c2["host"])}, group=len(c2["parts"]) > 2, others=[])
                for c2 in CALLS.values() if c2["parts"].get(uid, {}).get("state") == "ringing"]
        return jsonify(call=csnap(c) if c else None, room=dict(rsnap(r), you=r["members"][uid]["role"]) if r and uid in r["members"] else None, incoming=pend)


# ===================================================================== varredor (timeouts)
def tick():
    now = time.time()
    with L:
        for c in list(CALLS.values()):
            for uid, p in list(c["parts"].items()):
                if p["state"] == "ringing" and now > p["since"] + CFG["ring_sec"] + 1:
                    p["state"] = "missed"
                    hub.emit(uid, "call.cancelled", dict(id=c["id"], reason="timeout"))
                    cemit(c, "call.state")
                elif p["state"] == "joined" and hub.offline_for(uid) > CFG["grace_sec"]:
                    leave_call(c, uid)
                    if c["id"] not in CALLS:
                        break
            else:
                alive = [u for u, x in c["parts"].items() if x["state"] == "joined"]
                ringing = [u for u, x in c["parts"].items() if x["state"] == "ringing"]
                if c["id"] in CALLS:
                    if len(alive) <= 1 and not ringing:
                        end_call(c, "no_answer" if not c["started"] else "ended")
                    elif c["started"] and now - c["started"] > CFG["call_max_sec"]:
                        end_call(c, "max_duration")
        for r in list(ROOMS.values()):
            for uid in list(r["members"]):
                if hub.offline_for(uid) > CFG["grace_sec"] and r["id"] in ROOMS:
                    drop_member(r, uid)
            if r["id"] in ROOMS and now - r["created"] > CFG["room_max_sec"]:
                end_room(r, "A Mesa atingiu o limite de 8 horas.")


def sweeper():
    while True:
        time.sleep(2)
        try:
            tick()
        except Exception as e:
            print("  ! varredor:", e)


# ===================================================================== ganchos para o painel admin
def live_counts():
    with L:
        return dict(calls=len(CALLS), rooms=len(ROOMS), in_rooms=sum(len(r["members"]) for r in ROOMS.values()), **hub.stats())


def admin_rooms():
    with L:
        return [dict(id=r["id"], title=r["title"], host=r["host_name"], privacy=r["privacy"], tags=r["tags"], created=r["created"],
                     speakers=[m["username"] for m in r["members"].values() if m["role"] != "listener"],
                     listeners=len([1 for m in r["members"].values() if m["role"] == "listener"])) for r in ROOMS.values()]


def admin_end_room(rid, reason):
    with L:
        r = ROOMS.get(rid)
        if r:
            end_room(r, reason)
        return bool(r)


def admin_kick_from_room(rid, name):
    with L:
        r = ROOMS.get(rid)
        uid = uid_of(name)
        if r and uid in r["members"] and uid != r["host"]:
            r["banned"].add(uid)
            drop_member(r, uid, "Removido pela moderação.")
            return True
    return False


def kick_user(uid):
    """Derruba sessão ao vivo, chamada e Mesa de quem foi punido."""
    hub.emit(uid, "auth.revoked", {})
    with L:
        c = CALLS.get(USER_CALL.get(uid))
        if c:
            leave_call(c, uid)
        r = ROOMS.get(USER_ROOM.get(uid))
        if r:
            drop_member(r, uid, "Conta suspensa ou banida.")


def room_info(rid):
    r = q("select * from rooms where id=?", (rid,), one=True)
    return dict(r) if r else None


def setup(app, _q, _count):
    global q, count
    q, count = _q, _count
    app.register_blueprint(bp)
    threading.Thread(target=sweeper, daemon=True).start()
    threading.Thread(target=social.gc_loop, daemon=True).start()
