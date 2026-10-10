"""Бот управления репостами. Работает через Bot API, за один запуск обрабатывает все накопившиеся нажатия.

Что лежит в секретах GitHub: только токены (BOT_TOKEN, API_ID, API_HASH, TG_SESSION, GEMINI_API_KEY и т.д.).
Все остальные настройки (каналы, подписи, режимы) хранятся в config.json и меняются кнопками этого бота.
"""
import os, re, json, time, hashlib, requests

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
LIVE = int(os.environ.get("LIVE_SECONDS") or 0)   # сколько секунд бот «живёт» за один запуск (0 = один опрос и выход)
CONFIG_FILE = "config.json"
CS_FILE = "control_state.json"
API = f"https://api.telegram.org/bot{TOKEN}/"
VOICES = ["Puck", "Fenrir", "Charon", "Kore", "Leda", "Sadachbia"]
PRIVACY = ["public", "unlisted", "private"]
PRIVACY_RU = {"public": "для всех", "unlisted": "по ссылке", "private": "только я"}
MODE_RU = {"new": "новые посты", "sequential": "по порядку с начала (сериал)"}


# ---------- хранение ----------

def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def save(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def new_profile(**kw):
    p = dict(title="", target="", footer="", topic="", mode="new", rewrite="ai", per_run=10,
             first_run_hours=999, state_file="", enabled=True, sources=[])
    p.update(kw)
    return p


# ---------- Telegram Bot API ----------

def api(method, **params):
    try:
        r = requests.post(API + method, json=params, timeout=40)
        return r.json()
    except Exception as e:
        print("  Bot API:", method, e)
        return {"ok": False}


def kb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send(chat, text, rows=None):
    p = dict(chat_id=chat, text=text[:4000], disable_web_page_preview=True)
    if rows:
        p["reply_markup"] = kb(rows)
    return api("sendMessage", **p)


def edit(chat, mid, text, rows=None):
    if not mid:
        return send(chat, text, rows)
    p = dict(chat_id=chat, message_id=mid, text=text[:4000], disable_web_page_preview=True)
    if rows:
        p["reply_markup"] = kb(rows)
    r = api("editMessageText", **p)
    if not r.get("ok") and "not modified" not in json.dumps(r).lower():
        return send(chat, text, rows)
    return r


# ---------- разбор ввода ----------

def parse_channel(text):
    """-> (канал, id_поста). Канал: '@имя' или '-100...'. None, если не распознали."""
    t = (text or "").strip()
    m = re.search(r"(?:t|telegram)\.me/(?:c/(\d+)|([A-Za-z]\w{3,}))(?:/\d+)?(?:/(\d+))?", t)
    if m:
        mid = int(m.group(3) or 0)
        if not mid:
            mm = re.search(r"/(\d+)\s*$", t)
            mid = int(mm.group(1)) if mm else 0
        return ("-100" + m.group(1) if m.group(1) else "@" + m.group(2)), mid
    m = re.fullmatch(r"@?([A-Za-z]\w{3,})", t)
    if m:
        return "@" + m.group(1), 0
    if re.fullmatch(r"-100\d+", t):
        return t, 0
    return None, 0


def sref(src):
    return src if len(src) <= 30 else hashlib.md5(src.encode()).hexdigest()[:10]


def find_src(prof, ref):
    for sc in prof["sources"]:
        if sc["id"] == ref or hashlib.md5(sc["id"].encode()).hexdigest()[:10] == ref:
            return sc
    return None


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())[:12] or "ch"


def chat_title(ref):
    r = api("getChat", chat_id=ref)
    return (r.get("result") or {}).get("title") if r.get("ok") else None


# ---------- экраны ----------

def v_main(cfg):
    n = len(cfg["profiles"])
    on = sum(1 for p in cfg["profiles"].values() if p.get("enabled", True))
    return (f"🤖 Управление репостами\nКаналов: {n} (включено: {on})\n\n"
            "Все настройки меняются здесь, в секреты GitHub заходить не нужно.",
            [[("📺 Мои каналы", "l"), ("➕ Новый канал", "pn")],
             [("🎬 Shorts", "sh"), ("ℹ️ Статус", "st")],
             [("▶️ Запустить репост", "run"), ("▶️ Запустить Shorts", "runs")]])


def v_list(cfg):
    rows = [[(("✅ " if p.get("enabled", True) else "⏸ ") + (p.get("title") or pid), f"p:{pid}")]
            for pid, p in cfg["profiles"].items()]
    rows.append([("➕ Новый канал", "pn"), ("⬅️ Меню", "m")])
    return ("📺 Твои каналы. Нажми, чтобы настроить:" if cfg["profiles"] else
            "Каналов пока нет. Нажми «Новый канал»."), rows


def v_profile(cfg, pid):
    p = cfg["profiles"][pid]
    srcs = p["sources"]
    text = (f"📺 {p.get('title') or pid}\n"
            f"Куда публикуем: {p.get('target') or 'не задано'}\n"
            f"Источников: {len(srcs)}\n"
            f"Режим: {MODE_RU.get(p.get('mode', 'new'))}\n"
            f"Переписывать ИИ: {'да' if p.get('rewrite', 'ai') == 'ai' else 'нет, текст как есть'}\n"
            f"Постов за запуск: {p.get('per_run', 10)}\n"
            f"Тема: {p.get('topic') or 'Minecraft Bedrock'}\n"
            f"Подпись внизу: {p.get('footer') or 'нет'}\n"
            f"Статус: {'включён' if p.get('enabled', True) else 'выключен'}")
    return text, [
        [("📥 Источники", f"sl:{pid}"), ("🎯 Куда", f"pe:{pid}:target")],
        [("🔁 Режим", f"pm:{pid}"), ("✍️ ИИ вкл/выкл", f"pr:{pid}")],
        [("🔢 Постов за запуск", f"pe:{pid}:per_run"), ("🏷 Тема", f"pe:{pid}:topic")],
        [("🧾 Подпись внизу", f"pe:{pid}:footer"), ("📝 Название", f"pe:{pid}:title")],
        [("⏯ Вкл/выкл", f"pt:{pid}"), ("🗑 Удалить канал", f"pd:{pid}")],
        [("⬅️ К списку", "l")]]


def v_sources(cfg, pid):
    p = cfg["profiles"][pid]
    lines = []
    for i, sc in enumerate(p["sources"], 1):
        flags = " 🎬" if sc.get("only_video") else ""
        sig = f"\n   подпись: «{sc['sig']}»" if sc.get("sig") else ""
        frm = f"\n   начало с поста #{sc['from_id']}" if sc.get("from_id") else ""
        lines.append(f"{i}. {sc['id']}{flags}{sig}{frm}")
    text = f"📥 Источники канала «{p.get('title') or pid}»:\n\n" + ("\n".join(lines) or "пока пусто")
    rows = [[(f"{i}. {sc['id']}", f"s:{pid}:{sref(sc['id'])}")] for i, sc in enumerate(p["sources"], 1)]
    rows.append([("➕ Добавить источник", f"sa:{pid}")])
    rows.append([("⬅️ Назад", f"p:{pid}")])
    return text, rows


def v_source(cfg, pid, sc):
    ref = sref(sc["id"])
    text = (f"📥 {sc['id']}\n"
            f"Подпись в начале постов: {('«' + sc['sig'] + '»') if sc.get('sig') else 'нет'}\n"
            f"Только видео: {'да' if sc.get('only_video') else 'нет'}\n"
            f"Начинать с поста: {('#' + str(sc['from_id'])) if sc.get('from_id') else 'с самого первого'}")
    return text, [
        [("✏️ Подпись", f"se:{pid}:{ref}:sig"), (f"🎬 Только видео: {'да' if sc.get('only_video') else 'нет'}", f"sv:{pid}:{ref}")],
        [("▶️ Начать с поста", f"se:{pid}:{ref}:from_id"), ("🗑 Удалить", f"sd:{pid}:{ref}")],
        [("⬅️ К источникам", f"sl:{pid}")]]


def v_shorts(cfg):
    s = cfg.setdefault("shorts", {})
    text = ("🎬 Shorts (ролики на YouTube)\n"
            f"Брать посты из канала: {s.get('channel') or 'как в секрете TARGET_CHANNEL'}\n"
            f"Название канала в призыве: {s.get('handle') or 'авто'}\n"
            f"Доступ роликов: {PRIVACY_RU.get(s.get('privacy', 'public'))}\n"
            f"Голос: {s.get('voice') or 'Puck'}\n"
            f"Копию ролика слать в: {s.get('chat') or 'Избранное'}")
    return text, [
        [("📥 Откуда брать посты", "she:channel"), ("🔖 Название в призыве", "she:handle")],
        [(f"🔒 Доступ: {PRIVACY_RU.get(s.get('privacy', 'public'))}", "shp"), (f"🗣 Голос: {s.get('voice') or 'Puck'}", "shv")],
        [("💬 Куда слать копию", "she:chat"), ("⬅️ Меню", "m")]]


def v_status(cfg):
    lines = []
    for pid, p in cfg["profiles"].items():
        sf = p.get("state_file") or f"state_{pid}.json"
        st = load(sf, {})
        prog = ", ".join(f"{sc['id']}: пост #{st.get(sc['id'], 0)}" for sc in p["sources"]) or "нет источников"
        lines.append(f"{'✅' if p.get('enabled', True) else '⏸'} {p.get('title') or pid} -> {p.get('target')}\n"
                     f"   опубликовано ключей: {len(st.get('_seen', []))}\n   дошли до: {prog}")
    return "ℹ️ Статус\n\n" + ("\n\n".join(lines) or "каналов нет"), [[("⬅️ Меню", "m")]]


PROMPTS = {
    "target": "Пришли канал, КУДА публиковать: @имя или ссылку. Аккаунт бота должен быть админом этого канала.",
    "topic": "Напиши тему канала одной фразой, например: сериалы и летсплеи. «-» вернёт Minecraft Bedrock.",
    "footer": "Пришли подпись, которая будет внизу каждого поста, например @мой_канал. «-» уберёт её.",
    "title": "Пришли название этого канала для списка.",
    "per_run": "Сколько постов публиковать за один запуск? Пришли число от 1 до 30.",
    "sig": "Пришли подпись, которая будет В НАЧАЛЕ каждого поста из этого источника, например: Художник Лололошка пишет. «-» уберёт её.",
    "from_id": "С какого поста начинать (режим «по порядку»)? Пришли ссылку на пост или его номер. «-» чтобы с самого первого.",
}


# ---------- обработка ----------

class Ctx:
    def __init__(self, cfg, cs):
        self.cfg, self.cs, self.changed = cfg, cs, False


def show(ctx, chat, mid, view):
    text, rows = view
    edit(chat, mid, text, rows)


def ask(ctx, chat, mid, aw, text):
    ctx.cs["await"][str(chat)] = aw
    ctx.changed = True
    edit(chat, mid, text + "\n\n(отмена: /cancel)", None)


def dispatch(workflow):
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GH_TOKEN")
    if not (repo and tok):
        return False, "нет доступа к GitHub (запусти бота через workflow control)"
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                               "User-Agent": "control-bot"},
                      json={"ref": os.environ.get("GITHUB_REF_NAME") or "main"}, timeout=30)
    return r.status_code == 204, f"HTTP {r.status_code}"


def apply_input(ctx, chat, text):
    """Обрабатывает текст, который бот ждал (название, канал, подпись и т.д.)."""
    cfg, cs = ctx.cfg, ctx.cs
    aw = cs["await"].pop(str(chat))
    ctx.changed = True
    t = text.strip()
    kind, pid = aw["kind"], aw.get("pid")
    prof = cfg["profiles"].get(pid) if pid else None
    clear = t in ("-", "—")

    if kind == "new_profile":
        ch, _ = parse_channel(t)
        if not ch:
            send(chat, "Не разобрал канал. Пришли @имя или ссылку вида t.me/имя.")
            cs["await"][str(chat)] = aw
            return
        base, n = "ch" + slug(ch), 1
        npid = base
        while npid in cfg["profiles"]:
            n += 1
            npid = f"{base}{n}"
        title = chat_title(ch) or ch
        cfg["profiles"][npid] = new_profile(title=title, target=ch, state_file=f"state_{npid}.json", sources=[])
        send(chat, f"Создал канал «{title}». Теперь добавим первый источник.")
        ask(ctx, chat, None, {"kind": "add_source", "pid": npid},
            "Пришли канал-источник: @имя или ссылку. Можно сразу с подписью через «|», например:\n@канал | Художник пишет")
        return
    if prof is None and kind not in ("shorts",):
        send(chat, "Этот канал уже удалён.", v_main(cfg)[1])
        return

    if kind == "add_source":
        part, _, sig = t.partition("|")
        ch, mid = parse_channel(part)
        if not ch:
            send(chat, "Не разобрал канал. Пришли @имя или ссылку вида t.me/имя.")
            cs["await"][str(chat)] = aw
            return
        if any(sc["id"] == ch for sc in prof["sources"]):
            send(chat, f"{ch} уже есть в источниках.")
            show(ctx, chat, None, v_sources(cfg, pid))
            return
        note = "" if chat_title(ch) else "\n⚠️ Бот не видит этот канал (возможно закрытый). Добавил всё равно, проверь по логам репоста."
        sc = dict(id=ch, sig=sig.strip(), only_video=False, from_id=mid)
        prof["sources"].append(sc)
        if sig.strip():
            send(chat, f"Добавил {ch}.{note}")
            show(ctx, chat, None, v_source(cfg, pid, sc))
        else:
            send(chat, f"Добавил {ch}.{note}")
            ask(ctx, chat, None, {"kind": "edit", "pid": pid, "field": "sig", "src": ch}, PROMPTS["sig"])
        return

    if kind == "edit":
        field = aw["field"]
        if field in ("sig", "from_id"):
            sc = find_src(prof, aw["src"])
            if not sc:
                send(chat, "Этого источника уже нет.")
                return
            if field == "sig":
                sc["sig"] = "" if clear else t
            else:
                if clear:
                    sc["from_id"] = 0
                else:
                    ch, mid = parse_channel(t)
                    num = mid or (int(t) if t.isdigit() else 0)
                    if not num:
                        send(chat, "Нужна ссылка на пост или число.")
                        cs["await"][str(chat)] = aw
                        return
                    sc["from_id"] = num
            send(chat, "Сохранил ✅")
            show(ctx, chat, None, v_source(cfg, pid, sc))
            return
        if field == "target":
            ch, _ = parse_channel(t)
            if not ch:
                send(chat, "Не разобрал канал. Пришли @имя или ссылку вида t.me/имя.")
                cs["await"][str(chat)] = aw
                return
            prof["target"] = ch
        elif field == "per_run":
            if not t.isdigit() or not 1 <= int(t) <= 30:
                send(chat, "Нужно число от 1 до 30.")
                cs["await"][str(chat)] = aw
                return
            prof["per_run"] = int(t)
        elif field in ("topic", "footer"):
            prof[field] = "" if clear else t
        elif field == "title":
            prof["title"] = t[:60]
        send(chat, "Сохранил ✅")
        show(ctx, chat, None, v_profile(cfg, pid))
        return

    if kind == "shorts":
        s = cfg.setdefault("shorts", {})
        field = aw["field"]
        if clear:
            s[field] = ""
        elif field in ("channel", "chat"):
            ch, _ = parse_channel(t)
            s[field] = ch or t
        else:
            s[field] = t
        send(chat, "Сохранил ✅")
        show(ctx, chat, None, v_shorts(cfg))


def on_callback(ctx, chat, mid, data):
    cfg, cs = ctx.cfg, ctx.cs
    parts = data.split(":")
    c = parts[0]
    if c == "m":
        return show(ctx, chat, mid, v_main(cfg))
    if c == "l":
        return show(ctx, chat, mid, v_list(cfg))
    if c == "st":
        return show(ctx, chat, mid, v_status(cfg))
    if c == "pn":
        return ask(ctx, chat, mid, {"kind": "new_profile"}, "Новый канал. Пришли канал, КУДА будешь публиковать: @имя или ссылку.")
    if c in ("run", "runs"):
        ok, info = dispatch("repost.yml" if c == "run" else "shorts.yml")
        return edit(chat, mid, ("▶️ Запустил. Результат смотри в логах GitHub Actions." if ok else f"Не получилось запустить: {info}"),
                    [[("⬅️ Меню", "m")]])
    if c == "sh":
        return show(ctx, chat, mid, v_shorts(cfg))
    if c == "shp":
        s = cfg.setdefault("shorts", {})
        s["privacy"] = PRIVACY[(PRIVACY.index(s.get("privacy", "public")) + 1) % len(PRIVACY)]
        ctx.changed = True
        return show(ctx, chat, mid, v_shorts(cfg))
    if c == "shv":
        s = cfg.setdefault("shorts", {})
        cur = s.get("voice") or "Puck"
        s["voice"] = VOICES[(VOICES.index(cur) + 1) % len(VOICES)] if cur in VOICES else VOICES[0]
        ctx.changed = True
        return show(ctx, chat, mid, v_shorts(cfg))
    if c == "she":
        field = parts[1]
        q = {"channel": "Пришли канал, ИЗ КОТОРОГО Shorts берёт посты (@имя). «-» чтобы вернуть значение из секрета.",
             "handle": "Как называть твой Telegram-канал в призыве на ролике, например @мой_канал. «-» для авто.",
             "chat": "Куда присылать копию готового ролика: @имя, число-id или «me» (Избранное). «-» для Избранного."}[field]
        return ask(ctx, chat, mid, {"kind": "shorts", "field": field}, q)

    pid = parts[1] if len(parts) > 1 else None
    prof = cfg["profiles"].get(pid)
    if prof is None:
        return edit(chat, mid, "Это меню устарело.", v_main(cfg)[1])
    if c == "p":
        return show(ctx, chat, mid, v_profile(cfg, pid))
    if c == "pe":
        return ask(ctx, chat, mid, {"kind": "edit", "pid": pid, "field": parts[2]}, PROMPTS[parts[2]])
    if c == "pm":
        prof["mode"] = "sequential" if prof.get("mode", "new") == "new" else "new"
        ctx.changed = True
        return show(ctx, chat, mid, v_profile(cfg, pid))
    if c == "pr":
        prof["rewrite"] = "keep" if prof.get("rewrite", "ai") == "ai" else "ai"
        ctx.changed = True
        return show(ctx, chat, mid, v_profile(cfg, pid))
    if c == "pt":
        prof["enabled"] = not prof.get("enabled", True)
        ctx.changed = True
        return show(ctx, chat, mid, v_profile(cfg, pid))
    if c == "pd":
        return edit(chat, mid, f"Удалить канал «{prof.get('title') or pid}» из бота? Уже опубликованные посты в Telegram не пропадут.",
                    [[("Да, удалить", f"pdy:{pid}"), ("Отмена", f"p:{pid}")]])
    if c == "pdy":
        del cfg["profiles"][pid]
        ctx.changed = True
        return show(ctx, chat, mid, v_list(cfg))
    if c == "sl":
        return show(ctx, chat, mid, v_sources(cfg, pid))
    if c == "sa":
        return ask(ctx, chat, mid, {"kind": "add_source", "pid": pid},
                   "Пришли канал-источник: @имя или ссылку. Можно сразу с подписью через «|», например:\n@канал | Художник пишет")
    sc = find_src(prof, parts[2]) if len(parts) > 2 else None
    if len(parts) > 2 and sc is None:
        return edit(chat, mid, "Этот источник уже удалён.", v_sources(cfg, pid)[1])
    if c == "s":
        return show(ctx, chat, mid, v_source(cfg, pid, sc))
    if c == "se":
        return ask(ctx, chat, mid, {"kind": "edit", "pid": pid, "field": parts[3], "src": sc["id"]}, PROMPTS[parts[3]])
    if c == "sv":
        sc["only_video"] = not sc.get("only_video")
        ctx.changed = True
        return show(ctx, chat, mid, v_source(cfg, pid, sc))
    if c == "sd":
        return edit(chat, mid, f"Удалить источник {sc['id']}?", [[("Да, удалить", f"sdy:{pid}:{parts[2]}"), ("Отмена", f"s:{pid}:{parts[2]}")]])
    if c == "sdy":
        prof["sources"].remove(sc)
        ctx.changed = True
        return show(ctx, chat, mid, v_sources(cfg, pid))


def on_message(ctx, chat, uid, text, admin_env):
    cs = ctx.cs
    admin = admin_env or cs.get("admin", 0)
    t = (text or "").strip()
    if not admin and t.startswith("/start"):         # кто первым написал /start, тот и хозяин
        cs["admin"] = admin = uid
        ctx.changed = True
        send(chat, "Ты назначен хозяином бота ✅")
    if uid != admin:
        return
    if t.startswith("/cancel"):
        cs["await"].pop(str(chat), None)
        ctx.changed = True
        return show(ctx, chat, None, v_main(ctx.cfg))
    if t.startswith(("/start", "/menu")):
        cs["await"].pop(str(chat), None)
        ctx.changed = True
        return show(ctx, chat, None, v_main(ctx.cfg))
    if t.startswith("/status"):
        return show(ctx, chat, None, v_status(ctx.cfg))
    if str(chat) in cs["await"]:
        return apply_input(ctx, chat, t)
    show(ctx, chat, None, v_main(ctx.cfg))


def main():
    if not TOKEN:
        print("BOT_TOKEN не задан, управление через бота отключено")
        return
    cfg = load(CONFIG_FILE, {})
    cfg.setdefault("profiles", {})
    cs = load(CS_FILE, {})
    cs.setdefault("await", {})
    ctx = Ctx(cfg, cs)
    admin_env = int(os.environ.get("ADMIN_ID") or 0)
    deadline = time.time() + LIVE
    offset, handled, polls = None, 0, 0
    while True:
        remaining = deadline - time.time()
        poll = 0 if LIVE <= 0 else max(0, min(25, int(remaining)))     # долгий опрос: ответ приходит сразу, как только ты нажал
        params = dict(timeout=poll, limit=100, allowed_updates=["message", "callback_query"])
        if offset:
            params["offset"] = offset                               # это же подтверждает прошлые обращения
        updates = api("getUpdates", **params).get("result") or []
        polls += 1
        for u in updates:
            offset = u["update_id"] + 1
            handled += 1
            try:
                if "callback_query" in u:
                    q = u["callback_query"]
                    api("answerCallbackQuery", callback_query_id=q["id"])
                    if q["from"]["id"] != (admin_env or cs.get("admin", 0)):
                        continue
                    m = q.get("message") or {}
                    on_callback(ctx, m["chat"]["id"], m.get("message_id"), q.get("data", ""))
                elif "message" in u:
                    m = u["message"]
                    if m.get("chat", {}).get("type") != "private":
                        continue
                    on_message(ctx, m["chat"]["id"], m["from"]["id"], m.get("text", ""), admin_env)
            except Exception as e:
                print("  ошибка обработки:", e)
        if ctx.changed:
            save(CONFIG_FILE, cfg)
            save(CS_FILE, cs)
            ctx.changed = False
            print("Настройки сохранены")
        if LIVE <= 0 or time.time() >= deadline - 0.5:
            break
        if poll == 0:
            time.sleep(0.3)
    if offset:
        api("getUpdates", offset=offset, limit=1, timeout=0)         # окончательно подтверждаем обработку
    print(f"Обработано обращений: {handled}, опросов: {polls}, работал {LIVE if LIVE else 0} с")


if __name__ == "__main__":
    main()
