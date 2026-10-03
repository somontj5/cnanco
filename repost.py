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
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_PER_RUN = 10          # сколько постов опубликовать за один запуск
MAX_AI_PER_RUN = 60       # сколько постов показать Gemini за один запуск (защита от слишком долгого запуска)
FIRST_RUN_HOURS = 24    # история при первом запуске для нового канала (потом верни 24)
SETTLE_MIN = 5            # свежие посты младше N минут ждут следующего запуска
TARGET_SCAN = 1500        # сколько последних сообщений своего канала сканировать для защиты от дублей
GEMINI_TRIES = 2          # попыток на каждую модель при ошибках 5xx
MAX_FAILS = 3             # после стольких неудач Gemini пост пропускается навсегда
STATE = "state.json"

PROMPT = (
    "Ты главный редактор Telegram-канала про Minecraft Bedrock: моды, аддоны, DLC, "
    "текстурпаки, шейдеры, читы, клиенты, а также идеи и фото построек, скриншоты и видео. "
    "Тебе приходит пост из чужого канала: текст (может отсутствовать), количество фото и видео "
    "и список файлов. Сначала реши, публиковать ли его у нас.\n\n"
    "ПУБЛИКОВАТЬ любой нормальный контент по теме: моды, аддоны, DLC, текстурпаки, шейдеры, "
    "читы и клиенты (apk, exe и другие файлы тоже), карты, идеи и фото построек, скриншоты, "
    "видео, обзоры, полезные новости о Minecraft. Читы и клиенты публиковать нормально, "
    "их не нужно отсеивать как «опасные». Пост без текста, где есть только файл или только "
    "фото, тоже можно публиковать: составь короткое описание по названию файла или по тому, "
    "что видно из контекста, ничего не выдумывая.\n"
    "ПРОПУСТИТЬ только если пост:\n"
    "- реклама или продажа рекламы, услуг, ставок, казино, заработка, ботов, серверов, чужих каналов;\n"
    "- розыгрыш, опрос или объявление канала, не связанное с контентом;\n"
    "- вообще не про Minecraft;\n"
    "- пустой или бессмысленный.\n"
    "Если в хорошем посте есть рекламная строка или ссылка на чужой канал, всё равно публикуй, "
    "а эти части убери.\n\n"
    "Если публикуешь, напиши описание своими словами:\n"
    "1) Название и версию игры (например «1.21.70+») сохрани точно.\n"
    "2) Суть и возможности переформулируй: тот же смысл, но другие слова и структура.\n"
    "3) Убери ссылки, @упоминания, названия чужих каналов, рекламу, призывы "
    "подписаться, слова про комментарии, навигацию и переходники.\n"
    "4) Не больше 600 символов, 1-2 уместных эмодзи.\n"
    "5) В конце один хэштег по типу: #аддон, #мод, #текстурпак, #шейдер, #клиент, #чит, "
    "#постройка или #новости.\n\n"
    "Формат ответа строго такой, без пояснений и без markdown.\n"
    "Если пропускаешь:\n"
    "РЕШЕНИЕ: ПРОПУСТИТЬ — короткая причина\n"
    "Если публикуешь:\n"
    "РЕШЕНИЕ: ПУБЛИКОВАТЬ\n"
    "НАЗВАНИЕ: оригинальное название мода, аддона или постройки (как в источнике, латиницей, "
    "если оно там латиницей), а если названия нет, поставь прочерк\n"
    "готовое описание (со следующей строки)\n\n"
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

def call_gemini(model, content):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for attempt in range(1, GEMINI_TRIES + 1):
        try:
            r = requests.post(
                url,
                headers={"x-goog-api-key": GEMINI_KEY},
                json={
                    "contents": [{"parts": [{"text": content}]}],
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


def rewrite(content):
    global counter
    n = len(MODELS)
    order = MODELS[counter % n:] + MODELS[:counter % n]
    counter += 1
    for i, model in enumerate(order):
        if model in dead:
            continue
        status, out = call_gemini(model, content)
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
    """Возвращает (решение, причина, название, описание). Решение: publish / skip / None."""
    lines = out.strip().splitlines()
    for idx, line in enumerate(lines):
        u = line.strip().strip("*").upper()
        if u.startswith("РЕШЕНИЕ"):
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if "ПРОПУСТ" in u:
                reason = ""
                for sep in ("—", "–", "-"):
                    if sep in rest:
                        reason = rest.split(sep, 1)[1].strip()
                        break
                return "skip", reason or rest, None, ""
            lines = lines[idx + 1:]
            title = None
            if lines and lines[0].strip().strip("*").upper().startswith("НАЗВАНИЕ"):
                title = lines[0].split(":", 1)[1].strip() if ":" in lines[0] else None
                if title in ("-", "—", "–", ""):
                    title = None
                lines = lines[1:]
            return "publish", "", title, "\n".join(lines).strip()
    return None, "", None, ""


def build_input(p):
    photos = sum(1 for m in p["msgs"] if m.photo)
    videos = sum(1 for m in p["msgs"] if m.video or m.gif)
    docs = [m for m in p["msgs"] if is_doc_file(m)]
    names = "\n".join(f"- {fname(f)} ({(f.file.size or 0) // 1024} КБ)" for f in docs) or "нет"
    fwd = "да" if p["fwd"] else "нет"
    return (PROMPT + f"Пост переслан из другого канала: {fwd}\n"
            f"Содержимое: фото {photos}, видео {videos}, файлов {len(docs)}\n"
            f"Файлы:\n{names}\n\n"
            f"Текст поста:\n{p['text'] or '(текста нет)'}")


def make_caption(p):
    """Возвращает (статус, название, описание, причина). Статусы: ok, skip, quota, fail."""
    status, out = rewrite(build_input(p))
    if status != "ok":
        return status, None, None, ""
    decision, reason, title, body = parse_answer(out)
    if decision == "skip":
        return "skip", None, None, reason
    if decision != "publish" or not body:
        print("  ответ без понятного решения:", out[:150].replace("\n", " "))
        return "fail", None, None, ""
    if FOOTER:
        body = body[:900] + "\n\n" + FOOTER
    return "ok", title, body[:1024], ""


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
    ks = [f"d{m.document.id}"]
    if base:
        ks.append(f"n{base}|{m.file.size}")
    sk = stem_key(name)
    if sk:
        ks.append(sk)
    return ks


def item_keys(m):
    if m.photo:
        return [f"o{m.photo.id}"]
    return file_keys(m)


def text_key(text):
    for line in (text or "").splitlines():
        k = stem_key(line)
        if k:
            return k
    return None


# ---------- разбор сообщений ----------

def is_preview(m):
    return bool(m.photo or m.video or m.gif)


def has_media(m):
    if m.sticker or m.voice or m.video_note:
        return False
    return bool(m.photo or m.document)


def is_doc_file(m):
    return has_media(m) and bool(m.document) and not is_preview(m)


def build_posts(msgs):
    """Склеивает сообщения в посты: описание, фото-альбомы, файлы под описанием."""
    posts, cur = [], None
    for m in msgs:
        if m.action:  # служебные сообщения
            continue
        media = has_media(m)
        if not (m.message or media):
            continue
        album = bool(cur and m.grouped_id and cur["gid"] == m.grouped_id)
        tail = bool(cur and media and not m.message
                    and (m.date - cur["date"]).total_seconds() < 600)
        if album or tail:
            cur["msgs"].append(m)
            cur["last"], cur["date"] = m.id, m.date
            if not cur["text"] and m.message:
                cur["text"] = m.message
        else:
            cur = {"text": m.message or "", "msgs": [m], "first": m.id, "last": m.id,
                   "date": m.date, "gid": m.grouped_id, "fwd": bool(m.fwd_from)}
            posts.append(cur)
    return posts


# ---------- отправка ----------

async def send_media(client, target, medias, caption=None):
    await client.send_file(target, medias[0] if len(medias) == 1 else medias, caption=caption)


async def send_post(client, target, items, caption):
    previews = [m for m in items if is_preview(m)]
    files = [m for m in items if not is_preview(m)]
    first = True
    for group in (previews, files):  # фото и видео отдельным альбомом, файлы отдельным
        for i in range(0, len(group), 10):
            chunk = group[i:i + 10]
            await send_media(client, target, [m.media for m in chunk], caption if first else None)
            first = False
    if first:  # медиа нет: обычный текстовый пост
        await client.send_message(target, caption)


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
    skipped_ai = 0
    ai_calls = 0

    def remember(ks):
        for k in ks:
            if k and k not in seen_set:
                seen.append(k)
                seen_set.add(k)

    print("Источники:", len(SOURCES), "| state:",
          {k: v for k, v in state.items() if not k.startswith("_")})
    try:
        async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
            target = await client.get_entity(TARGET)

            # 1) что уже лежит в твоём канале: по этому тоже ищем дубли
            n0 = len(seen_set)
            async for m in client.iter_messages(target, limit=TARGET_SCAN):
                if has_media(m):
                    remember(item_keys(m))
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
                photos = sum(1 for m in msgs if m.photo)
                print(f"[{src}] last={last}, сообщений={len(msgs)}, фото={photos}, файлы по расширениям={exts}")
                max_ids[src] = max(m.id for m in msgs)
                posts = build_posts(msgs)
                print(f"  постов собрано: {len(posts)}")
                for p in posts:
                    collected.append((src, p))

            # 3) единая очередь: от самых старых постов к самым свежим
            collected.sort(key=lambda t: t[1]["date"])
            settle = datetime.now(timezone.utc) - timedelta(minutes=SETTLE_MIN)
            pending = {}
            stop = False

            for src, p in collected:
                key = f"{src}:{p['first']}"
                tag = f"[{src} #{p['first']}]"
                if stop:
                    hold(pending, src, p)
                    continue
                if p["date"] > settle:
                    print(tag, "слишком свежий, подождём следующего запуска")
                    stop = True
                    hold(pending, src, p)
                    continue
                if sent >= MAX_PER_RUN or ai_calls >= MAX_AI_PER_RUN:
                    stop = True
                    hold(pending, src, p)
                    continue
                if f"p{key}" in seen_set:  # ИИ уже решил пропустить этот пост раньше
                    continue

                # локальная проверка на дубли (до обращения к Gemini)
                orig_media = any(has_media(m) for m in p["msgs"])
                items = [m for m in p["msgs"] if has_media(m)
                         and not any(k in seen_set for k in item_keys(m))]
                tk = text_key(p["text"])
                if any(is_doc_file(m) for m in p["msgs"]) and tk and tk in seen_set:
                    items = []
                if (orig_media and not items) or (not orig_media and tk and tk in seen_set):
                    dups += 1
                    print(tag, "дубль, пропускаю")
                    continue

                ai_calls += 1
                print(tag, "запрос к Gemini")
                status, title, caption, reason = make_caption(p)
                if status == "quota":
                    print(tag, "лимиты обеих моделей исчерпаны, остановка до следующего запуска")
                    stop = True
                    hold(pending, src, p)
                    continue
                if status == "fail":
                    fails[key] = fails.get(key, 0) + 1
                    if fails[key] >= MAX_FAILS:
                        print(tag, f"Gemini не справился {MAX_FAILS} запуска, пост пропущен навсегда")
                    else:
                        print(tag, f"Gemini недоступен (попытка {fails[key]}/{MAX_FAILS}), "
                                   "пост пропущен сейчас, повторю в следующий запуск")
                        hold(pending, src, p)
                    continue
                if status == "skip":
                    skipped_ai += 1
                    remember([f"p{key}"])
                    fails.pop(key, None)
                    print(tag, "ИИ решил не публиковать:", reason or "без причины")
                    continue

                tkey = stem_key(title) if title else None
                if tkey and tkey in seen_set and any(is_doc_file(m) for m in items):
                    dups += 1
                    print(tag, "дубль по названию:", title)
                    continue

                try:
                    await send_post(client, target, items, caption)
                except Exception as e:
                    print(tag, "ошибка отправки:", e)
                    hold(pending, src, p)
                    continue

                remember([k for m in items for k in item_keys(m)] + [tk, tkey])
                fails.pop(key, None)
                sent += 1
                print(tag, "отправлено:", title or "(без названия)")
                await asyncio.sleep(3)

            # 4) сдвигаем отметки: не дальше самого раннего неотправленного поста
            for src, mid in max_ids.items():
                adv = pending[src] - 1 if src in pending else mid
                state[src] = max(state.get(src, 0), adv)
    finally:
        state["_seen"] = seen[-8000:]
        state["_fails"] = fails
        json.dump(state, open(STATE, "w"))
    print(f"Всего отправлено: {sent}, дублей: {dups}, отклонено ИИ: {skipped_ai}, запросов к ИИ: {ai_calls}")


asyncio.run(main())
