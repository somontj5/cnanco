import os, re, json, time, asyncio, requests
from collections import Counter
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["TG_SESSION"]
SOURCES = [s.strip() for s in os.environ["SOURCE_CHANNELS"].split(",") if s.strip()]
TARGET = os.environ["TARGET_CHANNEL"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
FOOTER = os.environ.get("FOOTER", "").strip()
EXTRA_EXT = {e.strip().lower().lstrip(".") for e in os.environ.get("EXTRA_EXT", "").split(",") if e.strip()}
ALLOWED_EXT = {"mcaddon", "mcpack", "mcworld", "mctemplate"} | EXTRA_EXT
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_PER_RUN = 10          # всего постов за один запуск
FIRST_RUN_HOURS = 9999    # история при первом запуске для нового канала (потом верни 24)
SETTLE_MIN = 5            # свежие посты младше N минут ждут следующего запуска
TARGET_SCAN = 1500        # сколько последних сообщений своего канала сканировать для защиты от дублей
GEMINI_TRIES = 2          # попыток на каждую модель при ошибках 5xx
MAX_FAILS = 3             # после стольких неудач Gemini пост пропускается навсегда
STATE = "state.json"
BAD_WORDS = ["реклам", "erid", "промокод", "казино", "ставк", "1xbet", "заработ"]

PROMPT = (
    "Ты редактор Telegram-канала про Minecraft Bedrock (моды, аддоны, DLC, "
    "текстурпаки, читы). Перепиши описание поста своими словами.\n"
    "1) Название и версию игры (например «1.21.70+») сохрани точно.\n"
    "2) Суть и возможности переформулируй: тот же смысл, но другие слова и структура.\n"
    "3) Убери ссылки, @упоминания, названия чужих каналов, рекламу, призывы "
    "подписаться, слова про комментарии, навигацию и переходники.\n"
    "4) Не больше 600 символов, 1-2 уместных эмодзи.\n"
    "5) В конце один хэштег по типу: #аддон, #мод, #текстурпак, #шейдер, #клиент или #чит.\n"
    "Формат ответа строго такой: первая строка — «НАЗВАНИЕ: » и оригинальное название "
    "мода или аддона (как в источнике, латиницей, если оно там латиницей); "
    "со второй строки — готовое описание. Без пояснений и без markdown.\n\n"
    "Исходный текст:\n"
)

dead = set()
counter = 0

STOP = {
    "аддон", "аддоны", "мод", "моды", "текстурпак", "пак", "версия", "для",
    "майнкрафт", "майнкрафта", "minecraft", "mcpe", "bedrock", "addon", "addons",
    "mod", "mods", "pack", "packs", "bp", "rp", "beta", "alpha", "final",
    "release", "mcaddon", "mcpack", "by",
}


# ---------- Gemini ----------

def call_gemini(model, text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for attempt in range(1, GEMINI_TRIES + 1):
        try:
            r = requests.post(
                url,
                headers={"x-goog-api-key": GEMINI_KEY},
                json={
                    "contents": [{"parts": [{"text": PROMPT + text}]}],
                    "generationConfig": {"temperature": 1.0},
                },
                timeout=60,
            )
        except Exception as e:
            print(f"  Gemini {model}: сетевая ошибка ({e}), попытка {attempt}/{GEMINI_TRIES}")
            time.sleep(3)
            continue
        if r.status_code == 429:
            print(f"  Gemini {model}: лимит исчерпан (429)")
            return "quota", None
        if r.status_code in (500, 502, 503, 504):
            print(f"  Gemini {model}: HTTP {r.status_code}, попытка {attempt}/{GEMINI_TRIES}")
            time.sleep(5)
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            out = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if out:
                print(f"  Gemini {model}: OK")
                return "ok", out
        except Exception:
            pass
        print(f"  Gemini {model}: пустой или ошибочный ответ: {r.text[:150]}")
        return "fail", None
    return "fail", None


def rewrite(text):
    global counter
    n = len(MODELS)
    order = MODELS[counter % n:] + MODELS[:counter % n]
    counter += 1
    for i, model in enumerate(order):
        if model in dead:
            continue
        status, out = call_gemini(model, text)
        if status == "ok":
            return "ok", out
        if status == "quota":
            dead.add(model)
        if i < len(order) - 1:
            print("  переключаюсь на следующую модель")
    if all(m in dead for m in MODELS):
        return "quota", None
    return "fail", None


def parse_answer(out):
    lines = out.strip().splitlines()
    title = None
    if lines and lines[0].strip().upper().startswith("НАЗВАНИЕ:"):
        title = lines[0].split(":", 1)[1].strip()
        out = "\n".join(lines[1:]).strip()
    return title, out


def make_caption(text):
    status, out = rewrite(text)
    if status != "ok":
        return status, None, None
    title, body = parse_answer(out)
    if not body:
        return "fail", None, None
    if FOOTER:
        body = body[:900] + "\n\n" + FOOTER
    return "ok", title, body[:1024]


# ---------- ключи для защиты от дублей ----------

def stem(s):
    s = (s or "").lower()
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", s)
    s = s.replace("add-on", "addon").replace("add on", "addon")
    s = re.sub(r"\bv?\d+(?:\.\d+)+\b", " ", s)
    s = re.sub(r"[_\-.]+", " ", s)
    s = re.sub(r"\bby\b.*", " ", s)
    words = []
    for w in re.findall(r"[a-zа-яё0-9]+", s):
        if w in STOP or w.isdigit() or re.fullmatch(r"(?:v|beta|alpha|rc)\d*", w):
            continue
        words.append(w)
    return "".join(words)


def stem_key(s):
    st = stem(s)
    return f"s{st}" if len(st) >= 4 else None


def fname(m):
    return (m.file.name or "") if m.file else ""


def ext(m):
    return os.path.splitext(fname(m))[1].lstrip(".").lower()


def file_keys(m):
    name = os.path.splitext(fname(m))[0]
    base = re.sub(r"[^a-z0-9а-яё]", "", name.lower())
    ks = [f"d{m.document.id}", f"n{base}|{m.file.size}"]
    sk = stem_key(name)
    if sk:
        ks.append(sk)
    return ks


def text_key(text):
    for line in (text or "").splitlines():
        k = stem_key(line)
        if k:
            return k
    return None


# ---------- разбор сообщений ----------

def is_file(m):
    return bool(m.document) and ext(m) in ALLOWED_EXT


def is_preview(m):
    return bool(m.photo or m.video or m.gif)


def bad(text):
    t = (text or "").lower()
    return any(w in t for w in BAD_WORDS)


def new_post(m, preview, own):
    return {"text": m.message, "preview": preview, "files": [], "first": m.id,
            "last": m.id, "date": m.date, "gid": m.grouped_id, "own": own}


def build_posts(msgs):
    posts, cur = [], None
    skipped = 0
    for m in msgs:
        if m.fwd_from or bad(m.message):
            skipped += 1
            cur = None
            continue
        if is_file(m):
            if m.message:
                if cur and cur["own"] and m.grouped_id and cur["gid"] == m.grouped_id:
                    cur["files"].append(m)
                    cur["last"], cur["date"] = m.id, m.date
                else:
                    cur = new_post(m, None, True)
                    cur["files"].append(m)
                    posts.append(cur)
            elif cur and (m.date - cur["date"]).total_seconds() < 600:
                cur["files"].append(m)
                cur["last"], cur["date"] = m.id, m.date
        elif m.message:
            cur = new_post(m, m if is_preview(m) else None, False)
            posts.append(cur)
    print("  пропущено пересланных/рекламных:", skipped)
    return posts


# ---------- отправка ----------

async def send_media(client, target, medias, caption=None):
    await client.send_file(target, medias[0] if len(medias) == 1 else medias, caption=caption)


async def send_post(client, target, p, files, caption):
    chunks = [files[i:i + 10] for i in range(0, len(files), 10)]
    if p["preview"]:
        await send_media(client, target, [p["preview"].media], caption)
        for ch in chunks:
            await send_media(client, target, [f.media for f in ch])
    else:
        for i, ch in enumerate(chunks):
            await send_media(client, target, [f.media for f in ch], caption if i == 0 else None)


def hold(pending, src, p):
    # запоминаем самый ранний неотправленный пост канала: состояние не уйдёт дальше него
    if src not in pending:
        pending[src] = p["first"]


async def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    seen = list(state.get("_seen", []))
    seen_set = set(seen)
    fails = state.get("_fails", {})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FIRST_RUN_HOURS)
    sent = 0
    dups = 0

    def remember(ks):
        for k in ks:
            if k and k not in seen_set:
                seen.append(k)
                seen_set.add(k)

    print("Разрешённые расширения:", sorted(ALLOWED_EXT))
    print("Источники:", len(SOURCES), "| state:",
          {k: v for k, v in state.items() if not k.startswith("_")})
    try:
        async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
            target = await client.get_entity(TARGET)

            # 1) что уже лежит в твоём канале — по этому тоже ищем дубли
            n0 = len(seen_set)
            async for m in client.iter_messages(target, limit=TARGET_SCAN):
                if m.document and ext(m) in ALLOWED_EXT:
                    remember(file_keys(m))
            print("Ключей из целевого канала добавлено:", len(seen_set) - n0)

            # 2) собираем посты со всех каналов
            collected = []
            max_ids = {}
            for src in SOURCES:
                try:
                    entity = await client.get_entity(src)
                except Exception as e:
                    print(f"[{src}] не удалось открыть канал: {e}")
                    continue
                last = state.get(src, 0)
                if last:
                    it = client.iter_messages(entity, min_id=last, reverse=True)
                else:  # новый канал: история за FIRST_RUN_HOURS часов
                    it = client.iter_messages(entity, offset_date=cutoff, reverse=True)
                msgs = [m async for m in it]
                if not msgs:
                    print(f"[{src}] last={last}, новых сообщений нет")
                    continue
                exts = dict(Counter(ext(m) or "?" for m in msgs if m.document))
                print(f"[{src}] last={last}, сообщений={len(msgs)}, файлы по расширениям={exts}")
                max_ids[src] = max(m.id for m in msgs)
                posts = build_posts(msgs)
                print(f"  постов собрано: {len(posts)}, с файлами: {sum(1 for p in posts if p['files'])}")
                for p in posts:
                    collected.append((src, p))

            # 3) единая очередь: от самых старых постов к самым свежим
            collected.sort(key=lambda t: t[1]["date"])
            settle = datetime.now(timezone.utc) - timedelta(minutes=SETTLE_MIN)
            pending = {}
            stop = False

            for src, p in collected:
                tag = f"[{src} #{p['first']}]"
                if stop:
                    hold(pending, src, p)
                    continue
                if p["date"] > settle:
                    print(tag, "слишком свежий, подождём следующего запуска")
                    stop = True
                    hold(pending, src, p)
                    continue
                if sent >= MAX_PER_RUN:
                    stop = True
                    hold(pending, src, p)
                    continue

                files = [f for f in p["files"] if not any(k in seen_set for k in file_keys(f))]
                tk = text_key(p["text"])
                if files and tk and tk in seen_set:
                    files = []
                if not files:
                    if p["files"]:
                        dups += 1
                        print(tag, "дубль, пропускаю")
                    continue

                print(tag, "запрос описания к Gemini")
                status, title, caption = make_caption(p["text"])
                key = f"{src}:{p['first']}"
                if status == "quota":
                    print(tag, "лимиты обеих моделей исчерпаны, остановка до следующего запуска")
                    stop = True
                    hold(pending, src, p)
                    continue
                if status == "fail":
                    fails[key] = fails.get(key, 0) + 1
                    if fails[key] >= MAX_FAILS:
                        print(tag, f"Gemini не ответил {MAX_FAILS} запуска, пост пропущен навсегда")
                    else:
                        print(tag, f"Gemini недоступен (попытка {fails[key]}/{MAX_FAILS}), "
                                   "пост пропущен сейчас, повторю в следующий запуск")
                        hold(pending, src, p)
                    continue

                tkey = stem_key(title) if title else None
                if tkey and tkey in seen_set:
                    dups += 1
                    print(tag, "дубль по названию:", title)
                    continue

                try:
                    await send_post(client, target, p, files, caption)
                except Exception as e:
                    print(tag, "ошибка отправки:", e)
                    hold(pending, src, p)
                    continue

                remember([k for f in files for k in file_keys(f)] + [tk, tkey])
                fails.pop(key, None)
                sent += 1
                print(tag, "отправлено:", title or "(без названия)")
                await asyncio.sleep(3)

            # 4) сдвигаем отметки: не дальше самого раннего неотправленного поста
            for src, mid in max_ids.items():
                adv = pending[src] - 1 if src in pending else mid
                state[src] = max(state.get(src, 0), adv)
    finally:
        state["_seen"] = seen[-3000:]
        state["_fails"] = fails
        json.dump(state, open(STATE, "w"))
    print(f"Всего отправлено: {sent}, дублей пропущено: {dups}")


asyncio.run(main())
