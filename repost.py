import os, re, json, time, asyncio, requests
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["TG_SESSION"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_PER_RUN = 10          # сколько постов опубликовать за один запуск
MAX_AI_PER_RUN = 60       # сколько постов показать Gemini за один запуск (защита от слишком долгого запуска)
SETTLE_MIN = 5            # свежие посты младше N минут ждут следующего запуска
TARGET_SCAN = 1500        # сколько последних сообщений своего канала сканировать для защиты от дублей
GEMINI_TRIES = 2          # попыток на каждую модель при ошибках 5xx
MAX_FAILS = 3             # после стольких неудач Gemini пост пропускается навсегда

PROMPT_MC = (
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
    "подписаться, слова про комментарии, навигацию и переходники, а также кнопки и призывы "
    "вроде «Скачать» и указатели 👉👈: файлы мы прикладываем сами.\n"
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

PROMPT_GENERIC = (
    "Ты главный редактор Telegram-канала на тему: {topic}. "
    "Тебе приходит пост из чужого канала: текст (может отсутствовать), количество фото и видео "
    "и список файлов. Сначала реши, публиковать ли его у нас.\n\n"
    "ПУБЛИКОВАТЬ любой нормальный контент по теме канала. Пост без текста, где есть только файл "
    "или только фото, тоже можно публиковать: составь короткое описание по названию файла или по "
    "тому, что видно из контекста, ничего не выдумывая.\n"
    "ПРОПУСТИТЬ только если пост:\n"
    "- реклама или продажа рекламы, услуг, ставок, казино, заработка, ботов, серверов, чужих каналов;\n"
    "- розыгрыш, опрос или объявление канала, не связанное с контентом;\n"
    "- не по теме канала;\n"
    "- пустой или бессмысленный.\n"
    "Если в хорошем посте есть рекламная строка или ссылка на чужой канал, всё равно публикуй, "
    "а эти части убери.\n\n"
    "Если публикуешь, напиши описание своими словами:\n"
    "1) Названия, версии и цифры сохрани точно.\n"
    "2) Суть переформулируй: тот же смысл, но другие слова и структура.\n"
    "3) Убери ссылки, @упоминания, названия чужих каналов, рекламу, призывы подписаться, "
    "слова про комментарии, навигацию и переходники, а также кнопки и призывы вроде «Скачать» "
    "и указатели 👉👈: файлы мы прикладываем сами.\n"
    "4) Не больше 600 символов, 1-2 уместных эмодзи.\n"
    "5) В конце один короткий хэштег по типу контента.\n\n"
    "Формат ответа строго такой, без пояснений и без markdown.\n"
    "Если пропускаешь:\n"
    "РЕШЕНИЕ: ПРОПУСТИТЬ — короткая причина\n"
    "Если публикуешь:\n"
    "РЕШЕНИЕ: ПУБЛИКОВАТЬ\n"
    "НАЗВАНИЕ: оригинальное название (как в источнике), а если названия нет, поставь прочерк\n"
    "готовое описание (со следующей строки)\n\n"
)


def prompt_for(topic):
    """Minecraft Bedrock по умолчанию; если у канала задана тема, подставляется она."""
    return PROMPT_GENERIC.replace("{topic}", topic) if topic else PROMPT_MC


# ---------- профили каналов (config.json, управляется через бота) ----------

CONFIG_FILE = "config.json"
SEQ_BATCH = 100        # в режиме «по порядку» за раз просматривается столько сообщений источника


def ref(x):
    """Числовой id канала (-100...) превращаем в число, имена и @ссылки оставляем как есть."""
    return int(x) if re.fullmatch(r"-?\d+", str(x)) else x


def new_profile(**kw):
    p = dict(title="", target="", footer="", topic="", mode="new", rewrite="ai", per_run=10,
             first_run_hours=999, state_file="", enabled=True, sources=[])
    p.update(kw)
    return p


def load_config():
    try:
        return json.load(open(CONFIG_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _src_list(env):
    return [dict(id=x.strip(), sig="", only_video=False) for x in os.environ.get(env, "").split(",") if x.strip()]


def bootstrap(cfg):
    """Первый запуск без config.json: переносим каналы из старых секретов GitHub, дальше всё через бота."""
    if cfg.get("profiles"):
        return False
    profs = {}
    src, tgt = _src_list("SOURCE_CHANNELS"), os.environ.get("TARGET_CHANNEL", "").strip()
    if src and tgt:
        profs["main"] = new_profile(title="Канал 1", target=tgt, footer=os.environ.get("FOOTER", "").strip(),
                                    topic=os.environ.get("CHANNEL_TOPIC", "").strip(), sources=src,
                                    state_file=os.environ.get("STATE_FILE", "").strip() or "state.json",
                                    first_run_hours=int(os.environ.get("FIRST_RUN_HOURS") or 999))
    src2, tgt2 = _src_list("SOURCE_CHANNELS_2"), os.environ.get("TARGET_CHANNEL_2", "").strip()
    if src2 and tgt2:
        profs["second"] = new_profile(title="Канал 2", target=tgt2, footer=os.environ.get("FOOTER_2", "").strip(),
                                      topic=os.environ.get("CHANNEL_TOPIC_2", "").strip(), sources=src2,
                                      state_file="state2.json", first_run_hours=48)
    if not profs:
        return False
    cfg["profiles"] = profs
    return True


def norm_sources(prof):
    out = []
    for x in prof.get("sources", []):
        if isinstance(x, str):
            x = dict(id=x)
        if x.get("id"):
            out.append(dict(id=x["id"], sig=x.get("sig", ""), only_video=bool(x.get("only_video")),
                            from_id=int(x.get("from_id") or 0)))
    return out


def is_video(m):
    d = getattr(m, "document", None)
    return bool(getattr(m, "video", None) or getattr(m, "gif", None) or
                (d is not None and (getattr(d, "mime_type", "") or "").startswith("video")))


def clean_original(text):
    t = re.sub(r"https?://\S+|t\.me/\S+|@\w+", "", text or "")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def compose(sig, body, footer):
    """Подпись канала-источника в начале, затем текст, затем подпись твоего канала. Всего не больше 1024 знаков."""
    fixed = [x for x in (sig, footer) if x]
    room = max(0, 1024 - sum(len(x) + 2 for x in fixed))
    return "\n\n".join(x for x in (sig, (body or "")[:room], footer) if x)[:1024]


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


def build_input(p, prompt):
    photos = sum(1 for m in p["msgs"] if m.photo)
    videos = sum(1 for m in p["msgs"] if m.video or m.gif)
    docs = [m for m in p["msgs"] if is_doc_file(m)]
    names = "\n".join(f"- {fname(f)} ({(f.file.size or 0) // 1024} КБ)" for f in docs) or "нет"
    fwd = "да" if p["fwd"] else "нет"
    return (prompt + f"Пост переслан из другого канала: {fwd}\n"
            f"Содержимое: фото {photos}, видео {videos}, файлов {len(docs)}\n"
            f"Файлы:\n{names}\n\n"
            f"Текст поста:\n{p['text'] or '(текста нет)'}")


def make_caption(p, prompt, footer="", sig="", mode="ai"):
    """Возвращает (статус, название, описание, причина). Статусы: ok, skip, quota, fail."""
    if mode == "keep":       # без ИИ: текст источника как есть (без ссылок), подпись и футер добавляются
        return "ok", None, compose(sig, clean_original(p["text"]), footer), ""
    status, out = rewrite(build_input(p, prompt))
    if status != "ok":
        return status, None, None, ""
    decision, reason, title, body = parse_answer(out)
    if decision == "skip":
        return "skip", None, None, reason
    if decision != "publish" or not body:
        print("  ответ без понятного решения:", out[:150].replace("\n", " "))
        return "fail", None, None, ""
    return "ok", title, compose(sig, body, footer), ""


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


VERSION = "v6: профили каналов, подписи, режим «по порядку», управление через бота"


def can_copy(ent):
    """Проверка снята: каналы с запретом пересылки копируем, файлы скачиваем и загружаем заново."""
    return True


# ---------- файлы по ссылке «Скачать» (когда файлы лежат в другом канале) ----------

DL_WORDS = re.compile(r"скач|download|загруз|файл|apk", re.I)


def parse_tme(url):
    """Разбор ссылки Telegram. Возвращает ('chan', канал, id_сообщения|None), ('invite',..), ('bot',..) или None."""
    u = re.sub(r"^https?://", "", (url or "").strip(), flags=re.I)
    m = re.match(r"(?:t|telegram)\.me/(.+)", u, flags=re.I)
    if not m:
        return None
    parts = [x for x in re.split(r"[/?#]", m.group(1)) if x]
    if not parts:
        return None
    head = parts[0]
    if head.startswith("+") or head.lower() == "joinchat":
        return ("invite", None, None)
    nums = [int(x) for x in parts[1:] if x.isdigit()]
    if head == "c":
        if len(parts) >= 2 and parts[1].isdigit():
            return ("chan", int("-100" + parts[1]), nums[-1] if len(nums) >= 2 else None)
        return None
    if head.lower().endswith("bot"):
        return ("bot", None, None)
    if not re.fullmatch(r"[A-Za-z]\w{3,}", head):
        return None
    return ("chan", head, nums[-1] if nums else None)


def dl_links(p):
    """Ссылки на скачивание в посте: ссылка на сообщение в канале или с подписью вроде «Скачать»."""
    out, seen_u = [], set()
    for m in p["msgs"]:
        try:
            ents = m.get_entities_text()
        except Exception:
            ents = []
        for ent, txt in ents:
            url = getattr(ent, "url", None) or (txt if type(ent).__name__ == "MessageEntityUrl" else None)
            if not url or url in seen_u:
                continue
            seen_u.add(url)
            kind = parse_tme(url)
            anchor = txt or ""
            if kind is None:
                if DL_WORDS.search(anchor):
                    out.append((url, anchor, ("external", None, None)))
            elif kind[0] == "chan":
                if kind[2] or DL_WORDS.search(anchor):   # ссылка на сообщение или «Скачать» на канал
                    out.append((url, anchor, kind))
            elif DL_WORDS.search(anchor):                # приглашение или бот с подписью «Скачать»
                out.append((url, anchor, kind))
    return out


async def files_from_link(client, ent, mid):
    """Файлы по ссылке на сообщение: само сообщение, его альбом или файлы, идущие под постом."""
    ids = list(range(max(1, mid - 9), mid + 10))
    msgs = [m for m in await client.get_messages(ent, ids=ids) if m]
    tgt = next((m for m in msgs if m.id == mid), None)
    if not tgt:
        return []
    if is_doc_file(tgt):
        if tgt.grouped_id:
            return [m for m in msgs if m.grouped_id == tgt.grouped_id and is_doc_file(m)]
        return [tgt]
    for pp in build_posts(msgs):
        if any(m.id == mid for m in pp["msgs"]):
            return [m for m in pp["msgs"] if is_doc_file(m)]
    return []


async def files_by_name(client, ent, p):
    """Ссылка ведёт на канал целиком: ищем среди последних файлов канала тот, что подходит по названию."""
    names = [stem(l) for l in (p["text"] or "").splitlines() if l.strip()][:4]
    names = [n for n in names if len(n) >= 4]
    if not names:
        return []
    best, best_s = None, 0.0
    async for m in client.iter_messages(ent, limit=400):
        if not is_doc_file(m):
            continue
        cap = next((l for l in (m.message or "").splitlines() if l.strip()), "")
        for a in (stem(os.path.splitext(fname(m))[0]), stem(cap)):
            if len(a) < 4:
                continue
            for b in names:
                sc = SequenceMatcher(None, a, b).ratio()
                if a in b or b in a:
                    sc = max(sc, 0.9)
                if sc > best_s:
                    best, best_s = m, sc
    if not best or best_s < 0.75:
        return []
    return await files_from_link(client, ent, best.id)


async def resolve_files(client, p, cache):
    """Статусы: nolink (скачивать нечего), ok (файлы найдены), unsupported (внешний сайт или бот),
    missing (в канале не нашли), error (сбой, повторим позже)."""
    links = dl_links(p)
    if not links:
        return "nolink", []
    tg = [l for l in links if l[2][0] == "chan"]
    if not tg:
        return "unsupported", []
    found, err, blocked = [], False, False
    for url, anchor, (_, ref, mid) in tg:
        try:
            if ref not in cache:
                cache[ref] = await client.get_entity(ref)
            ent = cache[ref]
            if not can_copy(ent):
                blocked = True
                print(f"  канал {ref}: запрет копирования, и это не твой канал, файлы не беру")
                continue
            got = await files_from_link(client, ent, mid) if mid else await files_by_name(client, ent, p)
            found += got
            await asyncio.sleep(1)
        except Exception as e:
            err = True
            print(f"  ссылка {url}: {e}")
    uniq, ids = [], set()
    for m in found:
        if m.document and m.document.id not in ids:
            ids.add(m.document.id)
            uniq.append(m)
    if uniq:
        return "ok", uniq
    if blocked and not err:
        return "unsupported", []
    return ("error" if err else "missing"), []


# ---------- отправка ----------

def is_protected(m, channel_protected=False):
    """Пересылка запрещена в канале или у сообщения: файлы нужно скачивать и загружать заново."""
    return bool(channel_protected or getattr(m, "noforwards", False))


async def send_media(client, target, msgs, caption=None, force_download=False):
    if not force_download:
        try:
            medias = [m.media for m in msgs]
            await client.send_file(target, medias[0] if len(medias) == 1 else medias, caption=caption)
            return
        except Exception as e:  # например, пересылка запрещена: скачиваем и грузим заново
            print("  прямая отправка не удалась, скачиваю и загружаю заново:", e)
    os.makedirs("tmp", exist_ok=True)
    paths = []
    try:
        for m in msgs:
            pth = await client.download_media(m, file="tmp/")
            if not pth:
                raise RuntimeError("не удалось скачать файл")
            paths.append(pth)
        await client.send_file(target, paths[0] if len(paths) == 1 else paths, caption=caption,
                               supports_streaming=True)
    finally:
        for pth in paths:
            try:
                os.remove(pth)
            except Exception:
                pass


async def send_post(client, target, items, caption, protected=False):
    previews = [m for m in items if is_preview(m)]
    files = [m for m in items if not is_preview(m)]
    first = True
    for group in (previews, files):  # фото и видео отдельным альбомом, файлы отдельным
        for i in range(0, len(group), 10):
            chunk = group[i:i + 10]
            force = any(is_protected(m, protected) for m in chunk)
            await send_media(client, target, chunk, caption if first else None, force_download=force)
            first = False
    if first:  # медиа нет: обычный текстовый пост
        await client.send_message(target, caption)


def hold(pending, src, p):
    # запоминаем самый ранний неотправленный пост канала: состояние не уйдёт дальше него
    if src not in pending:
        pending[src] = p["first"]


async def run_profile(client, name, prof):
    STATE = prof["state_file"] or (f"state_{name}.json")
    SOURCES = norm_sources(prof)
    PROMPT = prompt_for(prof.get("topic", ""))
    FOOTER = prof.get("footer", "")
    MAX_PER_RUN = int(prof.get("per_run") or 10)
    FIRST_RUN_HOURS = int(prof.get("first_run_hours") or 999)
    mode = prof.get("mode", "new")
    rewrite_mode = prof.get("rewrite", "ai")
    sig_by = {sc["id"]: sc["sig"] for sc in SOURCES}
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
        if True:
            target = await client.get_entity(ref(prof["target"]))

            # 1) что уже лежит в твоём канале: по этому тоже ищем дубли
            n0 = len(seen_set)
            async for m in client.iter_messages(target, limit=TARGET_SCAN):
                if has_media(m):
                    remember(item_keys(m))
            print("Ключей из целевого канала добавлено:", len(seen_set) - n0)

            # 2) собираем посты со всех каналов
            collected = []
            max_ids = {}
            protected_src = {}
            for sc in SOURCES:
                src = sc["id"]
                try:
                    entity = await client.get_entity(ref(src))
                except Exception as e:
                    print(f"[{src}] не удалось открыть канал: {e}")
                    continue
                if not can_copy(entity):
                    print(f"[{src}] запрет копирования, и аккаунт бота не создатель и не админ этого канала, пропускаю")
                    continue
                protected_src[src] = bool(getattr(entity, "noforwards", False))
                if protected_src[src]:
                    print(f"[{src}] запрет пересылки: скачиваю файлы и загружаю заново")
                last = state.get(src, 0)
                if mode == "sequential":   # «сериал»: идём от самого первого поста вниз, понемногу за запуск
                    if not last and sc.get("from_id"):
                        last = max(0, sc["from_id"] - 1)
                    it = client.iter_messages(entity, min_id=last, reverse=True, limit=SEQ_BATCH)
                elif last:
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
                if mode == "sequential" and len(msgs) >= SEQ_BATCH and len(posts) > 1:
                    tail = posts.pop()                  # последний пост мог оборваться на границе пачки
                    max_ids[src] = tail["first"] - 1
                print(f"  постов собрано: {len(posts)}")
                if sc.get("only_video"):
                    before = len(posts)
                    posts = [p for p in posts if any(is_video(m) for m in p["msgs"])]
                    print(f"  только видео: осталось {len(posts)} из {before}")
                for p in posts:
                    collected.append((src, p))

            # 3) единая очередь: от самых старых постов к самым свежим
            collected.sort(key=lambda t: t[1]["date"])
            settle = datetime.now(timezone.utc) - timedelta(minutes=SETTLE_MIN)
            pending = {}
            stop = False
            chan_cache = {}

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

                # файлы по ссылке «Скачать»: если под постом файлов нет, а ссылка ведёт в другой канал
                if not any(is_doc_file(m) for m in p["msgs"]):
                    st, extra = await resolve_files(client, p, chan_cache)
                    if st == "ok":
                        p["msgs"] = p["msgs"] + extra
                        print(tag, f"файлов по ссылке найдено: {len(extra)}")
                    elif st == "unsupported":
                        remember([f"p{key}"])
                        print(tag, "файл недоступен (внешний сайт, бот или канал с запретом копирования), пост пропущен")
                        continue
                    elif st in ("missing", "error"):
                        fails[key] = fails.get(key, 0) + 1
                        if fails[key] >= MAX_FAILS:
                            remember([f"p{key}"])
                            print(tag, "файл по ссылке не найден, пост пропущен навсегда")
                        else:
                            print(tag, f"файл по ссылке не найден (попытка {fails[key]}/{MAX_FAILS}), "
                                       "повторю в следующий запуск")
                            hold(pending, src, p)
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

                if rewrite_mode != "keep":
                    ai_calls += 1
                    print(tag, "запрос к Gemini")
                status, title, caption, reason = make_caption(p, PROMPT, FOOTER, sig_by.get(src, ""), rewrite_mode)
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
                    await send_post(client, target, items, caption, protected_src.get(src, False))
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


async def main():
    print("Версия repost.py:", VERSION)
    cfg = load_config()
    if bootstrap(cfg):
        save_config(cfg)
        print("Каналы перенесены из секретов GitHub в config.json. Дальше ими можно управлять через бота.")
    only = os.environ.get("PROFILE", "").strip()
    profs = {k: v for k, v in (cfg.get("profiles") or {}).items()
             if v.get("enabled", True) and norm_sources(v) and v.get("target") and (not only or only == k)}
    if not profs:
        print("Нет настроенных каналов (config.json пуст). Добавь канал через бота.")
        return
    async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        numeric = [x for v in profs.values() for x in [v["target"]] + [sc["id"] for sc in norm_sources(v)]
                   if re.fullmatch(r"-?\d+", str(x))]
        if numeric:        # закрытые каналы по числовому id: Telethon должен сначала «увидеть» их среди диалогов
            await client.get_dialogs()
        for name, prof in profs.items():
            print(f"\n===== Канал «{prof.get('title') or name}» -> {prof['target']} "
                  f"(режим: {prof.get('mode', 'new')}) =====")
            try:
                await run_profile(client, name, prof)
            except Exception as e:
                print(f"[{name}] ошибка, перехожу к следующему каналу: {e}")


if __name__ == "__main__":
    asyncio.run(main())
