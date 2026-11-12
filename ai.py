"""Praça · IA (Groq) postando na conta oficial @praca ("Praça · Moderação").
É uma conta de sistema: não tem senha e não faz login; o servidor publica por ela."""
import json, os, random, re, threading, time, urllib.request

q = count = None
BASE = os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
ACCOUNT = "praca"
TOPICS = ["uma dica de boa convivência na comunidade", "uma curiosidade sobre praças e vida de bairro", "uma enquete simples para os moradores responderem",
          "um bom dia ou boa tarde acolhedor", "um lembrete para usar as Mesas de Debate com respeito", "uma pergunta leve para puxar conversa",
          "uma sugestão de passeio ou atividade ao ar livre", "uma dica de segurança digital (golpes, senhas)", "um incentivo para marcar locais e compartilhar momentos",
          "uma lembrança de que dá para denunciar conteúdo abusivo", "uma curiosidade sobre cultura popular brasileira", "uma dica de leitura, música ou filme"]
SYS = ("Você escreve posts para a conta oficial da rede social Praça, que simula uma praça pública. Português do Brasil, tom simpático e curto. "
       "Sem política partidária, saúde, religião, links ou menções (@). No máximo 2 emojis. Responda SOMENTE com o texto do post.")
URL = re.compile(r"https?://\S+|www\.\S+", re.I)


def enabled():
    return bool(os.environ.get("GROQ_KEY"))


def groq(messages, max_tokens=140, temperature=.95):
    body = json.dumps(dict(model=MODEL, messages=messages, max_tokens=max_tokens, temperature=temperature)).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body, headers={
        "Authorization": "Bearer " + os.environ["GROQ_KEY"], "Content-Type": "application/json", "User-Agent": "PracaApp/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()


def account_id():
    r = q("select id from users where username=?", (ACCOUNT,), one=True)
    return r["id"] if r else None


def post_now(topic=None):
    if not enabled():
        raise RuntimeError("GROQ_KEY não configurada no servidor.")
    uid = account_id()
    if not uid:
        raise RuntimeError("Conta @praca não encontrada.")
    recent = [r["text"] for r in q("select text from posts where user_id=? order by id desc limit 6", (uid,))]
    topic = topic or random.choice(TOPICS)
    ask = f"Escreva UM post sobre: {topic}. Máx. 250 caracteres."
    if recent:
        ask += " Não repita nem se pareça com estes posts recentes:\n- " + "\n- ".join(t[:120] for t in recent)
    t = URL.sub("", groq([{"role": "system", "content": SYS}, {"role": "user", "content": ask}])).replace("@", "").strip().strip('"“”')[:280]
    if len(t) < 5:
        raise RuntimeError("A IA não devolveu texto.")
    pid = q("insert into posts(user_id,text,created) values(?,?,?)", (uid, t, time.time()), write=True)
    import social
    social.hub.broadcast("feed.new", dict(id=pid, author=ACCOUNT))
    return pid, t


def loop():
    mins = int(os.environ.get("AI_POST_MIN", 120))          # 0 desliga; padrão: 1 post a cada 2 h
    while enabled() and mins > 0:
        time.sleep(mins * 60)
        try:
            post_now()
        except Exception as e:
            print("  ! IA:", e)


def setup(_q, _count):
    global q, count
    q, count = _q, _count
    threading.Thread(target=loop, daemon=True).start()
