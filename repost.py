import os, re, json, asyncio, requests
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
MAX_PER_RUN = 10
SETTLE_MIN = 5
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
    "Верни только готовый текст, без пояснений и без markdown.\n\nИсходный текст:\n"
)

dead = set()
counter = 0


def rewrite(text):
    global counter
    n = len(MODELS)
    order = MODELS[counter % n:] + MODELS[:counter % n]
    counter += 1
    for model in order:
        if model in dead:
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
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
            print(model, "сетевая ошибка:", e)
            continue
        if r.status_code == 429:
            print(model, "лимит исчерпан")
            dead.add(model)
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            out = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if out:
                return out
        except Exception:
            pass
        print(model, "ошибка:", r.text[:200])
    return None


def make_caption(text):
    out = rewrite(text)
    if out is None:
        return None
    if FOOTER:
        out = out[:900] + "\n\n" + FOOTER
    return out[:1024]


def fname(m):
    return (m.file.name or "") if m.file else ""


def ext(m):
    return os.path.splitext(fname(m))[1].lstrip(".").lower()


def is_file(m):
    return bool(m.document) and ext(m) in ALLOWED_EXT


def is_preview(m):
    return bool(m.photo or m.video or m.gif)


def bad(text):
    t = (text or "").lower()
    return any(w in t for w in BAD_WORDS)


def keys(m):
    base = re.sub(r"[^a-z0-9а-я]", "", os.path.splitext(fname(m))[0].lower())
    return [f"d{m.document.id}", f"n{base}|{m.file.size}"]


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


async def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    seen = list(state.get("_seen", []))
    seen_set = set(seen)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    sent = 0
    print("Разрешённые расширения:", sorted(ALLOWED_EXT))
    print("Источники:", len(SOURCES), "| state:", {k: v for k, v in state.items() if k != "_seen"})
    try:
        async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
            target = await client.get_entity(TARGET)
            for src in SOURCES:
                if sent >= MAX_PER_RUN:
                    break
                entity = await client.get_entity(src)
                last = state.get(src, 0)
                if last:
                    it = client.iter_messages(entity, min_id=last, reverse=True)
                else:  # первый запуск: только за последние сутки
                    it = client.iter_messages(entity, offset_date=cutoff, reverse=True)
                msgs = [m async for m in it]
                if not msgs:
                    print(f"[{src}] last={last}, новых сообщений нет")
                    continue
                exts = dict(Counter(ext(m) or "?" for m in msgs if m.document))
                print(f"[{src}] last={last}, сообщений={len(msgs)}, файлы по расширениям={exts}")
                advance = max(m.id for m in msgs)
                settle = datetime.now(timezone.utc) - timedelta(minutes=SETTLE_MIN)
                posts = build_posts(msgs)
                print(f"  постов собрано: {len(posts)}, с файлами: {sum(1 for p in posts if p['files'])}")
                for p in posts:
                    if p["date"] > settle:
                        print("  пост", p["first"], "слишком свежий, ждём")
                        advance = p["first"] - 1
                        break
                    if sent >= MAX_PER_RUN:
                        advance = p["first"] - 1
                        break
                    files = [f for f in p["files"] if not any(k in seen_set for k in keys(f))]
                    if not files:
                        print("  пропуск (дубль или нет файлов), пост", p["first"])
                        continue
                    caption = make_caption(p["text"])
                    if caption is None:
                        advance = p["first"] - 1
                        break
                    try:
                        await send_post(client, target, p, files, caption)
                    except Exception as e:
                        print("Ошибка отправки:", e)
                        advance = p["first"] - 1
                        break
                    for f in files:
                        for k in keys(f):
                            seen.append(k)
                            seen_set.add(k)
                    sent += 1
                    print("Отправлено из", src, "пост", p["first"])
                    await asyncio.sleep(3)
                state[src] = max(last, advance)
    finally:
        state["_seen"] = seen[-3000:]
        json.dump(state, open(STATE, "w"))
    print("Всего отправлено:", sent)


asyncio.run(main())
