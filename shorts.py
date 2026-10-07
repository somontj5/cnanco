import os, io, base64, re, json, glob, urllib.parse, random, shutil, subprocess, asyncio, time, wave, datetime, requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageOps
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["TG_SESSION"]
TARGET = os.environ["TARGET_CHANNEL"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
HANDLE = os.environ.get("CHANNEL_HANDLE", "").strip() or (TARGET if TARGET.startswith("@") else "")
SHORTS_CHAT = os.environ.get("SHORTS_CHAT", "").strip() or "me"   # "me" = «Избранное»
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
SCAN = 300          # сколько последних сообщений своего канала просматривать
PER_RUN = 1         # сколько роликов делать за запуск
PHOTOS_PER_ITEM = 5 # сколько фото брать из каждого поста (из них Gemini подбирает подходящие по смыслу)
MIN_ITEMS = 2       # минимум постов одного типа для подборки
MAX_ITEMS = 3       # максимум постов в одной подборке
CLASSIFY_BATCH = 30 # сколько постов определять по типу за один запрос к Gemini
MAX_FAILS = 3
STATE = "shorts_state.json"
WORK = "work"
W, H, FPS = 1080, 1920, 25

# --- озвучка ---
VOICE_ON = True                                   # False = ролик без голоса
VOICE = os.environ.get("TTS_VOICE", "").strip() or "ru-RU-DmitryNeural"   # мужской; женский: ru-RU-SvetlanaNeural
VOICE_RATE = "+10%"                               # скорость речи: "+0%" обычная, "+15%" бодрее
MUSIC_VOL_VOICE = 0.12                            # громкость музыки под голосом (0.0-1.0)
POLL_KEY = os.environ.get("POLLINATIONS_KEY", "").strip()   # бесплатный ключ с enter.pollinations.ai (без него ограничения)
GEN_MAX = 4                                       # сколько картинок генерировать на один ролик (только если реальных фото мало)
TTS_ENGINE = os.environ.get("TTS_ENGINE", "").strip().lower() or "gemini"   # gemini = голос Gemini TTS, edge = голос Microsoft
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "").strip() or "Puck"          # Puck (бодрый), Fenrir (азартный), Charon (диктор), Kore (твёрдый), Leda (молодой)
GEMINI_TTS_STYLE = os.environ.get("GEMINI_TTS_STYLE", "").strip() or "upbeat, energetic, friendly storyteller"
# Порядок моделей озвучки. У каждой бесплатно 10 запросов в сутки, на один ролик уходит 1 запрос.
GEMINI_TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview",
                     "gemini-2.5-flash-preview-tts"]
SFX_ON = True                                     # звуки: свист, щелчок, удар. False = без звуков
SFX_VOL = 0.45                                    # громкость звуков (0.0-1.0)
SWAP_AT = 0.5                                     # на какой доле фразы появляется второе фото
MUSIC_VOL_PLAIN = 0.60                            # громкость музыки, если голоса нет

# --- монтаж ---
TRANSITION = 0.30        # длительность перехода между сценами, сек
PAUSE = 0.12             # пауза после фразы, сек
FALLBACK_DUR = {"hook": 2.8, "bullet": 3.6, "cta": 3.0}   # длительность сцен, если голоса нет
XFADES = ["fade", "slideleft", "slideright", "smoothleft", "smoothright", "wipeleft", "circleopen", "dissolve"]
MOTIONS = ["in", "left", "out", "up"]             # движение камеры по сценам по кругу
BAR_Y = 160              # где рисуется полоска прогресса

# --- оформление карточки ---
TITLE_FONT = "Montserrat:bold"      # шрифт заголовка: «семейство:начертание»
TEXT_FONT = "Open Sans:semibold"    # шрифт пунктов
CTA_FONT = "Montserrat:bold"        # шрифт плашки с призывом
ACCENT = (255, 196, 0)              # фирменный цвет (R, G, B)
DARK = (15, 15, 25, 220)
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# Свой шрифт: положи файлы fonts/title.ttf и fonts/text.ttf в репозиторий, и они заменят шрифты выше.
SAFE_TOP = 190       # сверху YouTube закрывает интерфейсом
SAFE_BOTTOM = 360    # снизу закрывают название, описание и музыка
COLUMN_W = 880       # ширина центральной колонки

TYPES = {
    "addon": "аддоны и моды",
    "cheats": "читы и клиенты",
    "build": "постройки",
    "world": "карты и миры",
    "texture": "текстуры и шейдеры",
    "other": "разное",
}

KEYWORDS = [  # запасной вариант, если Gemini недоступен: определение типа по словам
    ("cheats", ["чит", "клиент", "хак", "cheat", "client", "hack"]),
    ("texture", ["текстур", "шейдер", "ресурспак", "texture", "shader"]),
    ("build", ["построй", "постройк", "схем", "build", "замок", "особняк"]),
    ("world", ["карта", "карту", "мир ", "сид", "seed", "world", "map"]),
    ("addon", ["аддон", "addon", "мод", "mod", "mcaddon", "dlc"]),
]

CLASSIFY_PROMPT = (
    "Определи тип каждого поста из Telegram-канала про Minecraft Bedrock.\n"
    "Типы:\n"
    "addon: аддон, мод или DLC, который добавляет механики, предметы, мобов, оружие, способности;\n"
    "cheats: чит-клиент, хак, утилита или программа для игры с читами;\n"
    "build: идея постройки, дом, замок, ферма, схема, чертёж;\n"
    "world: карта, готовый мир, сид, приключение;\n"
    "texture: текстурпак, ресурспак, шейдеры;\n"
    "other: всё остальное (новости, опросы, реклама).\n"
    'Верни ТОЛЬКО JSON вида {"123": "addon", "124": "build"} для всех id из списка.\n\n'
    "Посты (id: начало текста):\n"
)


def guess_type(text):
    low = (text or "").lower()
    for t, kws in KEYWORDS:
        if any(k in low for k in kws):
            return t
    return "other"


def script_prompt(label, k, per_item, posts_text):
    if k > 1:
        task = (f"Сделай сценарий одного ролика-подборки: {k} поста из рубрики «{label}». "
                "Ролик показывает все по очереди.")
        hook_example = "«Три аддона, после которых выживание не будет прежним», «Эти аддоны ломают правила игры»"
    else:
        task = f"Сделай сценарий ролика про один пост из рубрики «{label}»."
        hook_example = "«Этот мод превращает выживание в RPG»"
    return (
        "Ты опытный сценарист коротких вертикальных видео (Shorts, Reels, TikTok) про Minecraft Bedrock. "
        "Твои ролики досматривают до конца, потому что каждая секунда даёт зрителю новую причину не "
        f"пролистывать. Цель: чтобы зритель захотел СРАЗУ скачать и попробовать. {task} "
        "Факты бери только из текстов постов, ничего не выдумывай, но подавай их ярко и конкретно.\n\n"

        "СЦЕНАРНОЕ МАСТЕРСТВО:\n"
        "• Зацепка (hook) работает на любопытство: обещай результат, но не раскрывай, как. "
        "Рабочие приёмы: боль зрителя («Надоело, что ночью везде мобы?»), контраст до и после, "
        "число плюс обещание, неожиданный факт, прямое обращение на «ты». "
        f"Примеры уровня: {hook_example}. "
        "Проверка: если после этой фразы хочется узнать продолжение, она годится.\n"
        "• ЗАПРЕЩЕНЫ штампы: «полностью изменят», «изменит твой Майнкрафт», «топ», «лучшие», "
        "«ты не поверишь», «это нужно видеть», «новые возможности», «улучшает геймплей».\n"
        "• Конкретика вместо общих слов: один яркий образ на пункт, сильный глагол, число или название "
        "из текста. Не «новые предметы», а «посох, который бьёт молнией по всем мобам в радиусе».\n"
        "• Выгода игроку: каждый пункт отвечает «что я смогу делать, чего не мог раньше» "
        "(быстрее строить, выживать, побеждать, удивлять друзей).\n"
        "• Нарастание: в подборке каждый следующий пост подаётся эффектнее предыдущего. "
        "Для связки используй разные короткие переходы («Второй…», «Дальше — …», «И напоследок…»), "
        "не повторяй один и тот же.\n"
        "• Ритм для озвучки: короткие предложения до 12 слов, разговорная речь как другу, "
        "без канцелярита («данный», «осуществляет», «предоставляет»), не начинай соседние фразы "
        "одним и тем же словом, не повторяй слова подряд.\n"
        "• Последняя фраза последнего поста даёт сильный финал, призыв скачать озвучивается "
        "отдельно, поэтому его не пиши.\n\n"

        "ТРЕБОВАНИЯ К ПОЛЯМ:\n"
        "1. hook: самая первая фраза ролика, до 9 слов, про ВСЮ подборку"
        + (f", назови тип и число ({k})" if k > 1 else "") + ".\n"
        "2. Для каждого поста (строго в том же порядке, что в списке) напиши:\n"
        "   name: название мода, постройки или мира, до 38 символов, без эмодзи и ссылок;\n"
        f"   bullets: ровно {per_item} пункта по 35-65 символов, коротко и конкретно: ЧТО ИМЕННО "
        "появляется или меняется. Версии игры и ссылки не пиши;\n"
        f"   voice: ровно {per_item} фразы для диктора, по одной на пункт, по 8-16 слов. "
        "Первая фраза поста сразу называет его и главную суть "
        "(например: «Первый — аддон про перчатки, которые ломают блоки втрое быстрее»). "
        "Не повторяй пункт дословно, добавляй, зачем это игроку.\n"
        "3. ВАЖНО: после hook зритель сразу слышит первый пост. Не повторяй и не пересказывай hook, "
        "не начинай фразы словами «слушай», «смотри», «представь», без вступлений и приветствий.\n"
        "   art: ровно столько же коротких описаний на АНГЛИЙСКОМ (до 18 слов): что нарисовать к каждой фразе "
        "(предмет, моб или постройка из текста). Это иллюстрации в стиле Minecraft, без текста и надписей.\n"
        "4. В hook и voice все иностранные слова пиши русскими буквами, как слышатся "
        "(Minecraft = Майнкрафт, Add-On = аддон).\n"
        "5. yt_title: до 90 символов, цепляющий, с числом или интригой и словом Minecraft, без обмана.\n\n"

        "Верни ТОЛЬКО JSON без пояснений:\n"
        '{"hook": "...",\n'
        ' "items": [{"name": "...", "bullets": ["..."], "voice": ["..."], "art": ["..."]}],\n'
        ' "yt_title": "...",\n'
        ' "yt_description": "2-3 предложения: что в подборке, призыв скачать бесплатно в Telegram, '
        'ссылка в шапке канала; в конце хэштеги #Shorts #Minecraft и ещё 2-3 по теме"}\n\n'
        "Посты:\n" + posts_text
    )


def valid_script(d, k):
    try:
        if not (isinstance(d, dict) and d.get("hook") and isinstance(d.get("items"), list)):
            return False
        if len(d["items"]) != k:
            return False
        return all(it.get("name") and isinstance(it.get("bullets"), list) and it["bullets"]
                   for it in d["items"])
    except Exception:
        return False


# ---------- Gemini ----------

def gemini_raw(prompt, check, images=None):
    parts_in = []
    for i, b64 in enumerate(images or [], 1):
        parts_in.append({"text": f"Фото {i}:"})
        parts_in.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    parts_in.append({"text": prompt})
    order = MODELS[:]
    random.shuffle(order)
    for model in order:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in (1, 2):
            try:
                r = requests.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_KEY},
                    json={
                        "contents": [{"parts": parts_in}],
                        "generationConfig": {"temperature": 0.85, "responseMimeType": "application/json"},
                    },
                    timeout=90,
                )
            except Exception as e:
                print(f"  Gemini {model}: сетевая ошибка ({e}), попытка {attempt}/2")
                time.sleep(3)
                continue
            if r.status_code == 429:
                print(f"  Gemini {model}: лимит исчерпан (429)")
                break
            if r.status_code in (500, 502, 503, 504):
                print(f"  Gemini {model}: HTTP {r.status_code}, попытка {attempt}/2")
                time.sleep(5)
                continue
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
                raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
                data = json.loads(raw)
                if check(data):
                    print(f"  Gemini {model}: OK")
                    return data
            except Exception:
                pass
            print(f"  Gemini {model}: неверный ответ: {r.text[:150]}")
            break
        print("  переключаюсь на следующую модель")
    return None


def classify(posts, types):
    """Определяет тип постов (пачками, одним запросом на 30 постов). Результат кладёт в types."""
    run_types = {}
    todo = [p for p in posts if str(p["id"]) not in types]
    for i in range(0, len(todo), CLASSIFY_BATCH):
        batch = todo[i:i + CLASSIFY_BATCH]
        lines = "\n".join(f'{p["id"]}: {re.sub(chr(10), " ", p["text"])[:240]}' for p in batch)
        res = gemini_raw(CLASSIFY_PROMPT + lines, lambda d: isinstance(d, dict))
        for p in batch:
            t = (res or {}).get(str(p["id"]))
            if t in TYPES:
                types[str(p["id"])] = t          # надёжный ответ Gemini запоминаем
            else:
                run_types[str(p["id"])] = guess_type(p["text"])   # запасной вариант только на этот запуск
    for p in posts:
        k = str(p["id"])
        run_types.setdefault(k, types.get(k, "other"))
    return run_types


def pick_group(cands, run_types, last_type):
    """Выбирает 2-3 поста одного типа для подборки. Если пар нет, берёт один свежий пост."""
    newest_first = list(reversed(cands))
    by = {}
    for p in newest_first:
        by.setdefault(run_types.get(str(p["id"]), "other"), []).append(p)
    ready = [t for t, ps in by.items() if t != "other" and len(ps) >= MIN_ITEMS]
    if ready:
        pool = [t for t in ready if t != last_type] or ready
        t = random.choices(pool, weights=[len(by[x]) for x in pool])[0]
        pick = random.sample(by[t][:6], min(MAX_ITEMS, len(by[t][:6])))
        pick.sort(key=lambda p: p["id"])
        return pick, t
    p = newest_first[0]
    return [p], run_types.get(str(p["id"]), "other")


def gemini_script(posts, type_key):
    k = len(posts)
    per_item = 3 if k == 1 else 2
    text = "\n\n".join(f"[ПОСТ {n + 1}]\n{p['text'][:1500]}" for n, p in enumerate(posts))
    return gemini_raw(script_prompt(TYPES.get(type_key, "разное"), k, per_item, text),
                      lambda d: valid_script(d, k))


# ---------- шрифты и текст ----------

_font_cache = {}
_font_logged = set()

# Автоскачивание шрифта заголовка (на GitHub Actions есть интернет). Файл сохраняется как fonts/title.ttf.
# Чтобы сменить шрифт: подставь другую ссылку на .ttf (например с github.com/google/fonts) или
# положи свои файлы fonts/title.ttf и fonts/text.ttf в репозиторий, тогда скачивания не будет.
FONT_URLS = {
    "title": "https://github.com/google/fonts/raw/main/ofl/russoone/RussoOne-Regular.ttf",
    "text": "",   # пусто = берётся системный TEXT_FONT
}
FONT_FILES = {"title": "fonts/title.ttf", "text": "fonts/text.ttf"}


def ensure_fonts():
    os.makedirs("fonts", exist_ok=True)
    for kind, url in FONT_URLS.items():
        path = FONT_FILES[kind]
        if os.path.exists(path) or not url:
            continue
        try:
            r = requests.get(url, timeout=60)
            ok = r.status_code == 200 and len(r.content) > 10000 and \
                r.content[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
            if ok:
                open(path, "wb").write(r.content)
                print(f"  шрифт {kind}: скачан {url.rsplit('/', 1)[-1]}")
            else:
                print(f"  шрифт {kind}: не скачался (HTTP {r.status_code}), беру системный")
        except Exception as e:
            print(f"  шрифт {kind}: ошибка скачивания ({e}), беру системный")


def get_font(kind, size):
    spec, custom = {
        "title": (TITLE_FONT, FONT_FILES["title"]),
        "text": (TEXT_FONT, FONT_FILES["text"]),
        "cta": (CTA_FONT, FONT_FILES["title"]),
    }[kind]
    if os.path.exists(custom):
        path = custom
    else:
        if spec not in _font_cache:
            try:
                out = subprocess.run(["fc-match", "-f", "%{file}|%{family}", spec],
                                     capture_output=True, text=True).stdout.strip()
                f, fam = (out.split("|", 1) + [""])[:2]
            except Exception:
                f, fam = "", ""
            if f and os.path.exists(f):
                _font_cache[spec] = f
                if spec.split(":")[0].lower() not in fam.lower():
                    print(f"  ВНИМАНИЕ: шрифт «{spec}» не установлен, подставлен «{fam}»")
            else:
                _font_cache[spec] = FALLBACK_FONT
                print(f"  ВНИМАНИЕ: шрифт «{spec}» не найден, беру DejaVu")
        path = _font_cache[spec]
    if (kind, path) not in _font_logged:
        _font_logged.add((kind, path))
        print(f"  шрифт {kind}: {path}")
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype(FALLBACK_FONT, size)


def clean(s):
    s = re.sub(r"https?://\S+|@\w+", "", s or "")
    s = "".join(ch for ch in s if ch.isalnum() or ch in " .,:;!?-—–+()/%'\"«»№&*•…")
    return re.sub(r"\s+", " ", s).strip()


def wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_lines(draw, text, kind, size, max_w, max_lines, min_size):
    while True:
        f = get_font(kind, size)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines or size <= min_size:
            return f, lines[:max_lines], size
        size -= 4


# ---------- кадры ----------

def make_bg(img):
    bg = ImageOps.fit(img, (W, H), method=Image.LANCZOS).filter(ImageFilter.GaussianBlur(45))
    return ImageEnhance.Brightness(bg).enhance(0.40).convert("RGBA")


def photo_box(img, bw, bh, radius=32):
    """Фото ровно по размеру рамки (форматы совпадают, поэтому лишних полей нет)."""
    base = ImageOps.fit(img, (bw, bh), method=Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw, bh), radius=radius, fill=255)
    return base, mask


def compute_layout(title, bullets, aspect):
    m = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    CW = COLUMN_W
    X0 = (W - CW) // 2
    PAD, GAP = 34, 34
    ft, t_lines, tsize = fit_lines(m, title, "title", 74, CW - 2 * PAD, 3, 48)
    lh_t = int(tsize * 1.22)
    title_h = len(t_lines) * lh_t + 2 * PAD

    bsize = 40
    while bsize > 30 and any(len(wrap(m, b, get_font("text", bsize), CW - 2 * PAD)) > 2 for b in bullets):
        bsize -= 2
    fb = get_font("text", bsize)
    b_lines = [wrap(m, b, fb, CW - 2 * PAD)[:2] for b in bullets]
    lh_b, pad_b, gap_b = int(bsize * 1.3), 24, 16
    b_heights = [len(ls) * lh_b + 2 * pad_b for ls in b_lines]
    bullets_h = sum(b_heights) + gap_b * max(0, len(b_heights) - 1)
    cta_h = 130
    fixed = title_h + bullets_h + cta_h + 3 * GAP

    avail = (H - SAFE_BOTTOM) - SAFE_TOP
    box_w = CW - 24
    box_h = int(max(300, min(780, box_w / aspect, avail - fixed - 16)))
    box_w = min(box_w, int(box_h * aspect))      # рамка точно по размеру фото, без размытых полей
    stack_h = fixed + box_h + 16
    y = SAFE_TOP + max(0, (avail - stack_h) // 2)

    L = dict(X0=X0, CW=CW, PAD=PAD, ft=ft, t_lines=t_lines, lh_t=lh_t, title_h=title_h, title_y=y,
             box_w=box_w, box_h=box_h, px=(W - box_w) // 2)
    y += title_h + GAP
    L["photo_y"] = y
    y += box_h + 16 + GAP
    L["bullet_ys"], L["b_heights"], L["b_lines"] = [], b_heights, b_lines
    L.update(fb=fb, lh_b=lh_b, pad_b=pad_b)
    for h in b_heights:
        L["bullet_ys"].append(y)
        y += h + gap_b
    y += GAP - gap_b
    L["cta_y"], L["cta_h"] = y, cta_h
    L["photo_bottom"] = L["photo_y"] + 8 + box_h
    return L


def plate_img(L, i, mode):
    """Плашка пункта i. mode: hidden (заготовка), current (подсвечен), past (уже показан), full (финал)."""
    CW, bh = L["CW"], L["b_heights"][i]
    im = Image.new("RGBA", (CW, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if mode == "hidden":
        d.rounded_rectangle((0, 0, CW - 1, bh - 1), radius=30, fill=(15, 15, 25, 70),
                            outline=(255, 255, 255, 40), width=2)
        return im
    cur = mode == "current"
    d.rounded_rectangle((0, 0, CW - 1, bh - 1), radius=30, fill=(15, 15, 25, 228),
                        outline=ACCENT + (255,) if cur else (255, 255, 255, 110), width=5 if cur else 3)
    col = "white" if mode in ("current", "full") else (205, 205, 215)
    for j, ln in enumerate(L["b_lines"][i]):
        d.text((CW // 2, L["pad_b"] + j * L["lh_b"] + L["lh_b"] // 2), ln,
               font=L["fb"], fill=col, anchor="mm")
    return im


def cta_img(L, active):
    """Жёлтая плашка с призывом. active=False: тусклая заготовка без текста."""
    CW, ch = L["CW"], L["cta_h"]
    im = Image.new("RGBA", (CW, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, CW - 1, ch - 1), radius=40, fill=ACCENT + (255 if active else 55,))
    if not active:
        return im
    cta = f"Скачать в Telegram: {HANDLE}" if HANDLE else "Скачать бесплатно в Telegram"
    fs = 50
    while True:
        fc = get_font("cta", fs)
        if d.textlength(cta, font=fc) <= CW - 2 * L["PAD"]:
            break
        if fs <= 28:
            if HANDLE and cta != HANDLE:
                cta, fs = HANDLE, 50
                continue
            break
        fs -= 4
    d.text((CW // 2, 52), cta, font=fc, fill="black", anchor="mm")
    d.text((CW // 2, 100), "ссылка в шапке канала", font=get_font("text", 30), fill=(40, 40, 40), anchor="mm")
    return im


def draw_scene(L, img, stage, out_png):
    """Рисует основу сцены. Новая плашка не рисуется: возвращается отдельным слоем для анимации.
    stage = сколько пунктов показано (1..N); stage = N+1 это финал с призывом."""
    nb = len(L["b_lines"])
    cta_on = stage > nb
    canvas = make_bg(img)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    X0, CW = L["X0"], L["CW"]
    od.rounded_rectangle((X0, L["title_y"], X0 + CW, L["title_y"] + L["title_h"]), radius=36,
                         fill=DARK, outline=ACCENT + (255,), width=5)
    od.rounded_rectangle((L["px"] - 8, L["photo_y"], L["px"] + L["box_w"] + 8, L["photo_y"] + L["box_h"] + 16),
                         radius=40, fill=(255, 255, 255, 235))
    canvas = Image.alpha_composite(canvas, overlay)
    pb, mask = photo_box(img, L["box_w"], L["box_h"])
    canvas.paste(pb, (L["px"], L["photo_y"] + 8), mask)

    d = ImageDraw.Draw(canvas)
    for i, ln in enumerate(L["t_lines"]):
        d.text((W // 2, L["title_y"] + L["PAD"] + i * L["lh_t"] + L["lh_t"] // 2), ln,
               font=L["ft"], fill="white", anchor="mm")
    for i in range(nb):
        mode = "full" if cta_on else ("past" if i < stage - 1 else "hidden")
        canvas.alpha_composite(plate_img(L, i, mode), (X0, L["bullet_ys"][i]))
    canvas.alpha_composite(cta_img(L, False), (X0, L["cta_y"]))
    canvas.convert("RGB").save(out_png)

    if cta_on:
        return dict(img=cta_img(L, True), x=X0, y=L["cta_y"], kind="pop", dur=0.55)
    return dict(img=plate_img(L, stage - 1, "current"), x=X0, y=L["bullet_ys"][stage - 1],
                kind="slide", dur=0.45)


def photo_layer(L, img):
    """Второе фото для плавной подмены в рамке."""
    pb, mask = photo_box(img, L["box_w"], L["box_h"])
    pb.putalpha(mask)
    return dict(img=pb, x=L["px"], y=L["photo_y"] + 8, kind="fade", dur=0.4)


def pack_tiles(aspects, box_w, room, gap, cap_h):
    """Выбирает раскладку фото (столбик, ряд, 1+2 и т.д.), при которой фото получаются самыми крупными.
    Возвращает список рядов: [(индекс, ширина, высота), ...] с внешними размерами (вместе с рамкой)."""
    k = len(aspects)
    layouts = {1: [[[0]]], 2: [[[0], [1]], [[0, 1]]],
               3: [[[0], [1], [2]], [[0, 1, 2]], [[0], [1, 2]], [[0, 1], [2]]]}.get(k, [[[i] for i in range(k)]])
    best, best_area = None, -1
    for lay in layouts:
        rows = []
        for row in lay:
            wsum = sum(aspects[i] for i in row)
            h = (box_w - gap * (len(row) - 1)) / wsum        # высота ряда, если растянуть на всю ширину
            rows.append((row, min(h, cap_h)))
        total = sum(h for _, h in rows) + gap * (len(rows) - 1)
        sc = min(1.0, (room - 0) / total)
        out, area = [], 0
        for row, h in rows:
            h *= sc
            out.append([(i, int(h * aspects[i]), int(h)) for i in row])
            area += sum(int(h * aspects[i]) * int(h) for i in row)
        if area > best_area:
            best, best_area = out, area
    return best


def draw_hook(imgs, hook, out_png):
    """Заставка: крупная зацепка на жёлтой плашке + фото постов крупной сеткой, каждое в своей рамке."""
    canvas = make_bg(imgs[0])
    CW, PAD, GAP, TG, FR = COLUMN_W, 44, 36, 14, 8
    X0 = (W - CW) // 2
    m = ImageDraw.Draw(canvas)
    text_up = hook.upper()
    f, lines, size = fit_lines(m, text_up, "title", 92, CW - 2 * PAD, 4, 56)   # плашка не выше 4 строк, чтобы осталось место фото
    if len(wrap(m, text_up, f, CW - 2 * PAD)) > 4:                              # очень длинная зацепка: допускаем 5 строк
        f, lines, size = fit_lines(m, text_up, "title", 80, CW - 2 * PAD, 5, 44)
    lh = int(size * 1.2)
    plate_h = len(lines) * lh + 2 * PAD
    avail = (H - SAFE_BOTTOM) - SAFE_TOP
    room = avail - plate_h - GAP

    box_w = CW - 24
    aspects = [im.size[0] / im.size[1] for im in imgs]
    # внешние размеры включают рамку: считаем с запасом, потом вычитаем 2*FR
    rows = pack_tiles(aspects, box_w, room, TG, 760 if len(imgs) == 1 else 640)
    stack_h = sum(max(h for _, _, h in row) for row in rows) + TG * (len(rows) - 1)
    y = SAFE_TOP + max(0, (avail - (plate_h + GAP + stack_h)) // 2)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((X0, y, X0 + CW, y + plate_h), radius=40, fill=ACCENT + (255,),
                         outline=(0, 0, 0, 255), width=6)
    plate_y = y
    y += plate_h + GAP
    spots = []
    for row in rows:
        row_w = sum(w for _, w, _ in row) + TG * (len(row) - 1)
        x = (W - row_w) // 2
        rh = max(h for _, _, h in row)
        for i, w, h in row:
            od.rounded_rectangle((x, y, x + w, y + h), radius=30, fill=(255, 255, 255, 235))
            spots.append((i, x + FR, y + FR, max(40, w - 2 * FR), max(40, h - 2 * FR)))
            x += w + TG
        y += rh + TG
    canvas = Image.alpha_composite(canvas, overlay)
    for i, x, yy, w, h in spots:
        pb, mask = photo_box(imgs[i], w, h, radius=24)
        canvas.paste(pb, (x, yy), mask)
    d = ImageDraw.Draw(canvas)
    for i, ln in enumerate(lines):
        d.text((W // 2, plate_y + PAD + i * lh + lh // 2), ln, font=f, fill="black", anchor="mm")
    canvas.convert("RGB").save(out_png)


def sub_chunks(text, max_words=3, max_chars=20):
    chunks, cur = [], []
    for w in text.split():
        if cur and (len(cur) >= max_words or len(" ".join(cur + [w])) > max_chars):
            chunks.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def draw_sub(text, out_png):
    f = get_font("title", 54)
    m = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = wrap(m, text, f, W - 160)[:2]
    lh = 68
    im = Image.new("RGBA", (W, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((W // 2, 10 + i * lh + lh // 2), ln, font=f, fill="white", anchor="mm",
               stroke_width=7, stroke_fill="black")
    im.save(out_png)
    return im.size[1]


# ---------- поиск постов ----------

async def find_candidates(client, target, used):
    msgs = [m async for m in client.iter_messages(target, limit=SCAN)]
    msgs.reverse()  # от старых к новым
    posts, cur = [], None
    for m in msgs:
        if m.action:
            continue
        if m.photo:
            if cur and m.grouped_id and cur["gid"] == m.grouped_id:
                cur["photos"].append(m)
                if not cur["text"] and m.message:
                    cur["text"] = m.message
            else:
                cur = {"id": m.id, "gid": m.grouped_id, "text": m.message or "", "photos": [m]}
                posts.append(cur)
        else:
            cur = None
    return [p for p in posts if p["id"] not in used and len(p["text"]) >= 20]


YT_ID = os.environ.get("YT_CLIENT_ID", "").strip()
YT_SECRET = os.environ.get("YT_CLIENT_SECRET", "").strip()
YT_REFRESH = os.environ.get("YT_REFRESH_TOKEN", "").strip()
YT_PRIVACY = os.environ.get("YT_PRIVACY", "").strip() or "public"   # public / unlisted / private


def youtube_upload(path, title, description):
    """Загружает ролик на YouTube. Возвращает ссылку или бросает исключение."""
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": YT_ID, "client_secret": YT_SECRET,
        "refresh_token": YT_REFRESH, "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"YouTube токен: {r.status_code} {r.text[:300]}")
    token = r.json()["access_token"]
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "categoryId": "20"},
        "status": {"privacyStatus": YT_PRIVACY, "selfDeclaredMadeForKids": False},
    }
    size = os.path.getsize(path)
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size),
                 "X-Upload-Content-Type": "video/mp4"},
        data=json.dumps(meta), timeout=60)
    if init.status_code != 200:
        raise RuntimeError(f"YouTube старт: {init.status_code} {init.text[:300]}")
    with open(path, "rb") as f:
        up = requests.put(init.headers["Location"], data=f,
                          headers={"Content-Type": "video/mp4"}, timeout=600)
    if up.status_code not in (200, 201):
        raise RuntimeError(f"YouTube загрузка: {up.status_code} {up.text[:300]}")
    return "https://youtube.com/shorts/" + up.json()["id"]



# ---------- озвучка ----------

TTS_FIXES = [(r"minecraft", "Майнкрафт"), (r"bedrock", "Бедрок"), (r"add-?on", "аддон"),
             (r"telegram", "Телеграм"), (r"\bmod\b", "мод"), (r"\bpe\b", "пи-и")]


def tts_prep(text):
    for pat, rep in TTS_FIXES:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise RuntimeError("ffmpeg завершился с ошибкой")


def trim_silence(src, dst):
    """Убирает тишину в начале и в конце фразы, иначе между фразами получается длинная пауза."""
    one = "silenceremove=start_periods=1:start_threshold=-45dB:detection=peak"
    run(["ffmpeg", "-y", "-i", src, "-af", f"{one},areverse,{one},areverse",
         "-ar", "24000", "-ac", "1", dst])


async def tts(text, path):
    """Озвучивает текст нейроголосом Microsoft (edge-tts). Возвращает (длительность, файл) или (0, None)."""
    try:
        import edge_tts
    except ImportError:
        print("  TTS: не установлен edge-tts")
        return 0.0, None
    text = tts_prep(text)
    raw = path + ".raw.mp3"
    for voice in dict.fromkeys([VOICE, "ru-RU-SvetlanaNeural"]):
        try:
            await edge_tts.Communicate(text, voice, rate=VOICE_RATE).save(raw)
            if probe(raw) < 0.3:
                continue
            try:
                trim_silence(raw, path)
                d = probe(path)
                if d > 0.3:
                    return d, path
            except Exception as e:
                print(f"  TTS: не удалось обрезать тишину ({e}), беру как есть")
            return probe(raw), raw
        except Exception as e:
            print(f"  TTS {voice}: {e}")
    return 0.0, None


# ---------- озвучка Gemini TTS: весь сценарий одним запросом ----------

tts_state = {"block": {}}      # модель -> дата (по тихоокеанскому времени), когда она исчерпала суточный лимит


class DailyQuota(Exception):
    pass


def pacific_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:
        return (datetime.datetime.utcnow() - datetime.timedelta(hours=8)).date().isoformat()


def pcm_to_wav(pcm, path, rate=24000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def gemini_tts_call(model, text, out_wav):
    """Один запрос к Gemini TTS. Сохраняет WAV. Бросает DailyQuota, если исчерпан суточный лимит модели."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    if model.startswith("gemini-3.8"):   # новая схема: стиль отдельно от текста
        body = {"contents": [{"role": "user", "parts": [{"text": text,
                                                         "speech_metadata": {"style": GEMINI_TTS_STYLE}}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"voice": GEMINI_VOICE}}}}
    else:                                # старые модели: стиль словами перед текстом
        body = {"contents": [{"parts": [{"text": f"Say in an {GEMINI_TTS_STYLE} way: {text}"}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig":
                                                                       {"voiceName": GEMINI_VOICE}}}}}
    for attempt in (1, 2):
        r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=180)
        if r.status_code == 429:
            low = r.text.lower()
            if "perday" in low or "per day" in low or "daily" in low:
                raise DailyQuota(model)
            if attempt == 1:                  # минутный лимит: ждём и пробуем ещё раз
                print(f"  {model}: лимит в минуту, жду 25 сек")
                time.sleep(25)
                continue
            raise DailyQuota(model)
        if r.status_code >= 500 and attempt == 1:
            time.sleep(6)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
        break
    part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    data = base64.b64decode(part["data"])
    if data[:4] == b"RIFF":
        open(out_wav, "wb").write(data)
    else:                                     # сырой PCM без заголовка
        m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
        pcm_to_wav(data, out_wav, int(m.group(1)) if m else 24000)
    return out_wav


def find_pauses(wav):
    """Паузы в аудио: [(начало, конец)] и общая длительность."""
    total = probe(wav)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af", "silencedetect=noise=-38dB:d=0.22",
                        "-f", "null", "-"], capture_output=True, text=True)
    gaps, start = [], None
    for line in r.stderr.splitlines():
        m1 = re.search(r"silence_start: ([\d.]+)", line)
        m2 = re.search(r"silence_end: ([\d.]+)", line)
        if m1:
            start = float(m1.group(1))
        elif m2 and start is not None:
            gaps.append((start, float(m2.group(1))))
            start = None
    return gaps, total


def split_by_pauses(wav, texts, work_prefix):
    """Режет озвучку на len(texts) сцен по самым длинным паузам. Возвращает [(длительность, файл)] или None."""
    n = len(texts)
    gaps, total = find_pauses(wav)
    inner = [g for g in gaps if g[0] > 0.3 and total - g[1] > 0.15]
    if len(inner) < n - 1:
        print(f"  в озвучке нашлось {len(inner)} пауз из {n - 1} нужных")
        return None
    top = sorted(sorted(inner, key=lambda g: g[1] - g[0], reverse=True)[:n - 1])
    cuts = [0.0] + [(a + b) / 2 for a, b in top] + [total]
    chars = [max(1, len(t)) for t in texts]
    speech = total - sum(b - a for a, b in top)
    rate = speech / sum(chars)
    out = []
    for i in range(n):
        dur = cuts[i + 1] - cuts[i]
        exp = chars[i] * rate
        if dur < 0.5 or not (0.4 < dur / max(exp, 0.1) < 3.0):
            print(f"  сцена {i + 1}: длительность {dur:.1f} с не похожа на ожидаемую {exp:.1f} с, нарезка отклонена")
            return None
        seg, fin = f"{work_prefix}seg{i}.wav", f"{work_prefix}voice{i}.wav"
        run(["ffmpeg", "-y", "-i", wav, "-ss", f"{cuts[i]:.3f}", "-to", f"{cuts[i + 1]:.3f}",
             "-ar", "24000", "-ac", "1", seg])
        trim_silence(seg, fin)
        d = probe(fin)
        if d < 0.3:
            return None
        out.append((d, fin))
    return out


async def gemini_tts_batch(texts):
    """Озвучивает весь сценарий ОДНИМ запросом (так укладываемся в лимит 10 запросов в сутки на модель)
    и режет на сцены. Возвращает [(длительность, файл)] или None, тогда включается запасной голос."""
    if TTS_ENGINE != "gemini" or not GEMINI_KEY:
        return None
    today = pacific_today()
    prepared = [tts_prep(t) for t in texts]
    tried = 0
    for model in GEMINI_TTS_MODELS:
        if tts_state["block"].get(model) == today:
            continue
        if tried >= 2:                       # не жжём лимиты: максимум две модели на ролик
            break
        sep = " <long pause> " if model.startswith("gemini-3.8") else "\n\n"
        text = sep.join(prepared)
        tried += 1
        try:
            full = gemini_tts_call(model, text, f"{WORK}/gtts_full.wav")
        except DailyQuota:
            tts_state["block"][model] = today
            print(f"  {model}: суточный лимит исчерпан, переключаюсь")
            tried -= 1
            continue
        except Exception as e:
            print(f"  {model}: {e}")
            continue
        parts = split_by_pauses(full, prepared, f"{WORK}/g_")
        if parts:
            print(f"  озвучка: {model}, голос {GEMINI_VOICE}, запрос 1, сцен {len(parts)}")
            return parts
    return None


# ---------- видео ----------

# ---------- генерация иллюстраций (Pollinations) ----------

_gen_off = [False]     # после ошибки ключа или нехватки pollen генерацию в этом запуске отключаем
_models_shown = [False]
_good_model = [None]    # имя модели, которое сервис уже принял


def _show_models():
    """При сбое один раз печатает список доступных моделей картинок (этот запрос ключа не требует)."""
    if _models_shown[0]:
        return
    _models_shown[0] = True
    try:
        r = requests.get("https://gen.pollinations.ai/image/models", timeout=30)
        names = []
        for m in (r.json() if r.status_code == 200 else []):
            names.append(m.get("name") or m.get("id") or str(m)[:40]) if isinstance(m, dict) else names.append(str(m))
        print("  доступные модели картинок:", ", ".join(names[:20]) or f"HTTP {r.status_code}")
    except Exception as e:
        print("  список моделей не получен:", e)


def _is_image(r):
    return r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 3000


def gen_image(prompt, ar):
    """Рисует иллюстрацию в стиле Minecraft через Pollinations. Возвращает картинку или None."""
    if _gen_off[0]:
        return None
    w = 1024
    h = int(max(512, min(1536, w / ar)) // 16 * 16)
    q = urllib.parse.quote(f"{prompt}, Minecraft style, blocky voxel art, vibrant colors, clean render, "
                           "no text, no letters, no watermark")
    seed = random.randint(1, 10 ** 6)

    if not POLL_KEY:   # без ключа сервис, скорее всего, не работает: пробуем один раз старый адрес
        try:
            r = requests.get(f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}&model=flux"
                             f"&seed={seed}&nologo=true", timeout=120)
            if _is_image(r):
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                im.load()
                print("  картинка нарисована (без ключа)")
                return im
            print(f"  генерация без ключа не сработала (HTTP {r.status_code}): добавь секрет POLLINATIONS_KEY")
        except Exception as e:
            print("  генерация без ключа:", e)
        _gen_off[0] = True
        return None

    names = ["flux", "black-forest-labs/flux.1-schnell"]    # старое короткое имя и новое полное
    if _good_model[0]:
        names.sort(key=lambda n: n != _good_model[0])
    for mdl in names:
        url = (f"https://gen.pollinations.ai/image/{q}?width={w}&height={h}&seed={seed}"
               f"&model={urllib.parse.quote(mdl, safe='')}")
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {POLL_KEY}"}, timeout=120)
        except Exception as e:
            print(f"  Pollinations ({mdl}): {e}")
            continue
        if _is_image(r):
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            im.load()
            _good_model[0] = mdl
            print(f"  картинка нарисована (модель {mdl})")
            return im
        body = "" if r.headers.get("content-type", "").startswith("image") else r.text[:200].replace("\n", " ")
        print(f"  Pollinations ({mdl}): HTTP {r.status_code} {body}")
        if r.status_code in (401, 403):
            print("  ключ не принят: проверь секрет POLLINATIONS_KEY (нужен ключ sk_..., не pk_...)")
            _gen_off[0] = True
            return None
        if r.status_code == 402:
            print("  на аккаунте Pollinations закончились pollen: проверь баланс на enter.pollinations.ai")
            _gen_off[0] = True
            return None
        if r.status_code == 429:
            time.sleep(10)
    _show_models()
    return None


# ---------- подбор фото по смыслу (Gemini смотрит на картинки) ----------

def img_b64(im, side=512):
    t = im.copy()
    t.thumbnail((side, side))
    buf = io.BytesIO()
    t.convert("RGB").save(buf, "JPEG", quality=72)
    return base64.b64encode(buf.getvalue()).decode()


def valid_shots(d, k, n):
    try:
        sh = d["shots"]
        return (isinstance(sh, list) and len(sh) == k and
                all(isinstance(x, list) and x and all(isinstance(i, int) and 1 <= i <= n for i in x)
                    for x in sh))
    except Exception:
        return False


def pick_shots(name, voices, imgs):
    """Для каждой фразы диктора выбирает 1-2 фото, подходящих по смыслу. Запасной вариант: по кругу."""
    n, k = len(imgs), len(voices)
    fallback = [[j % n, (j + 1) % n] if n > 1 else [0] for j in range(k)]
    if n < 3:
        return fallback
    lines = "\n".join(f"{j + 1}. {v}" for j, v in enumerate(voices))
    prompt = (
        f"Выше {n} фото из поста про «{name}» для Minecraft (пронумерованы с 1). "
        "Для каждой фразы диктора выбери 1-2 фото, которые лучше всего подходят по смыслу к тому, "
        "о чём в ней говорится (показывают описанный предмет, моба, постройку, механику). "
        "Если точного совпадения нет, выбери самые яркие. Первым в списке фразы ставь главное фото. "
        "Одно и то же главное фото у разных фраз по возможности не повторяй.\n"
        f"Фразы:\n{lines}\n\n"
        f'Верни ТОЛЬКО JSON вида {{"shots": [[1, 3], [2], ...]}}: по одному списку на каждую фразу, '
        f"всего {k} списков."
    )
    d = gemini_raw(prompt, lambda x: valid_shots(x, k, n), images=[img_b64(im) for im in imgs])
    if not d:
        print("  фото по смыслу не подобрались, раскладываю по кругу")
        return fallback
    out = []
    for lst in d["shots"]:
        uniq = []
        for i in lst:
            if i - 1 not in uniq:
                uniq.append(i - 1)
        out.append(uniq[:2])
    return out


# ---------- кадры с анимацией ----------

def ease_out(u):
    return 1 - (1 - u) ** 3


def ease_back(u):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (u - 1) ** 3 + c1 * (u - 1) ** 2


def cam(kind, S, p):
    """Движение камеры: масштаб и положение окна (0..1) по прогрессу p."""
    if kind == "in":
        return 1 + S * p, 0.5, 0.5
    if kind == "out":
        return 1 + S - S * p, 0.5, 0.5
    if kind == "left":
        return 1 + S, p, 0.5
    return 1 + S, 0.5, 1 - p     # up


def paste_layer(frame, ly, t):
    u = min(1.0, max(0.0, (t - ly["t0"]) / ly["dur"]))
    im, x, y = ly["img"], ly["x"], ly["y"]
    if ly["kind"] == "slide":        # плашка плавно выезжает снизу и проявляется
        a = ease_out(u)
        y += int(70 * (1 - a))
    elif ly["kind"] == "pop":        # плашка «выскакивает» с лёгким отскоком
        a = min(1.0, u * 2)
        sc = 0.8 + 0.2 * ease_back(u)
        w, h = im.size
        nw, nh = max(1, int(w * sc)), max(1, int(h * sc))
        im = im.resize((nw, nh), Image.BICUBIC)
        x += (w - nw) // 2
        y += (h - nh) // 2
    else:                            # fade: фото плавно сменяется другим
        a = ease_out(u)
    if a < 0.999:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v, a=a: int(v * a)))
    frame.alpha_composite(im, (x, y))


def render_clip(s, out):
    """Рисует кадры сцены в Pillow и отправляет в ffmpeg."""
    n = max(2, int(s["d"] * FPS))
    base = Image.open(s["png"]).convert("RGBA")
    layers = sorted(s.get("layers", []), key=lambda ly: ly["t0"])
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", out],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    white = Image.new("RGB", (W, H), "white")
    try:
        for i in range(n):
            t = i / FPS
            p = s["p0"] + (s["p1"] - s["p0"]) * i / (n - 1)
            z, px, py = cam(s["motion"], s["S"], p)
            frame = base
            live = [ly for ly in layers if t >= ly["t0"]]
            if live:
                frame = base.copy()
                for ly in live:
                    paste_layer(frame, ly, t)
            cw, ch = W / z, H / z
            x0, y0 = (W - cw) * px, (H - ch) * py
            f = frame.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch)).convert("RGB")
            if s["kind"] == "hook" and t < 0.35:      # вспышка в первую долю секунды
                f = Image.blend(f, white, 1 - t / 0.35)
            proc.stdin.write(f.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg не смог собрать сцену")
    except Exception:
        proc.kill()
        raise


def render_video(scenes, subs):
    n = len(scenes)
    T = TRANSITION
    clips = []
    for i, s in enumerate(scenes):
        clip = f"{WORK}/clip{i}.mp4"
        render_clip(s, clip)
        clips.append(clip)

    total = sum(s["d"] for s in scenes) - (n - 1) * T
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-f", "lavfi", "-i", f"color=c=0xFFC400:s={W}x12:r={FPS}:d={total + 1:.2f}"]
    for sb in subs:
        cmd += ["-loop", "1", "-t", f"{total + 1:.2f}", "-i", sb["png"]]

    fc, cur, prev = [], "[0:v]", None
    for k in range(1, n):
        if scenes[k]["soft"]:      # тот же пост: мягкая смена, без слайдов
            tr = random.choice(["fade", "dissolve"])
        else:                      # новый пост: заметный переход
            tr = random.choice([t for t in XFADES if t != prev])
            prev = tr
        off = sum(s["d"] for s in scenes[:k]) - k * T
        fc.append(f"{cur}[{k}:v]xfade=transition={tr}:duration={T}:offset={off:.3f}[x{k}]")
        cur = f"[x{k}]"
    fc.append(f"{cur}drawbox=x=0:y={BAR_Y}:w={W}:h=12:color=white@0.28:t=fill[b0]")
    fc.append(f"[b0][{n}:v]overlay=x='-w+w*t/{total:.3f}':y={BAR_Y}[b1]")
    cur = "[b1]"
    for j, sb in enumerate(subs):
        idx = n + 1 + j
        nxt = f"[s{j}]"
        fc.append(f"{cur}[{idx}:v]overlay=x=0:y={sb['y']}:enable='between(t,{sb['a']:.2f},{sb['b']:.2f})'{nxt}")
        cur = nxt
    fc.append(f"{cur}format=yuv420p[vout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            f"{WORK}/silent.mp4"]
    run(cmd)
    return total


# ---------- звуки (SFX) ----------

SFX_DEFS = {   # генерируются в ffmpeg; свои файлы кладутся в sfx/whoosh.mp3, sfx/pop.mp3 и т.д.
    "whoosh": "aevalsrc='(0.5*(random(0)*2-1)+0.25*sin(2*PI*(300*t+1800*t*t)))*sin(PI*t/0.6)*sin(PI*t/0.6)'"
              ":d=0.6:s=24000,highpass=f=250,lowpass=f=4800",
    "pop": "aevalsrc='0.8*sin(2*PI*(700*t-1500*t*t))*exp(-22*t)':d=0.18:s=24000",
    "tick": "aevalsrc='0.7*sin(2*PI*1700*t)*exp(-110*t)':d=0.1:s=24000",
    "hit": "aevalsrc='0.9*sin(2*PI*(120*t-180*t*t))*exp(-7*t)+0.3*(random(0)*2-1)*exp(-45*t)'"
           ":d=0.6:s=24000,lowpass=f=3500",
    "ding": "aevalsrc='0.5*sin(2*PI*1319*t)*exp(-5*t)+0.3*sin(2*PI*1975*t)*exp(-7*t)"
            "+0.15*sin(2*PI*2637*t)*exp(-9*t)':d=1.0:s=24000",
}


def sfx_file(kind):
    for ext in ("mp3", "wav", "m4a", "ogg"):
        p = f"sfx/{kind}.{ext}"
        if os.path.exists(p):
            return p
    path = f"{WORK}/sfx_{kind}.wav"
    if not os.path.exists(path):
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", SFX_DEFS[kind], "-ar", "24000", "-ac", "1", path])
    return path


def mix_audio(scenes, total, out, events):
    """Голос + музыка + звуки. events: [(время_сек, 'whoosh'|'pop'|...)]."""
    silent = f"{WORK}/silent.mp4"
    T = TRANSITION
    voiced = [s for s in scenes if s.get("voice_d")]
    tracks = [t for ext in ("mp3", "m4a", "ogg", "wav") for t in glob.glob(f"music/*.{ext}")]
    track = random.choice(tracks) if tracks else None
    print("  музыка:", os.path.basename(track) if track else "папка music/ пуста, музыки не будет")
    events = [(max(0.0, t), k) for t, k in events] if SFX_ON else []

    if not voiced and not track and not events:
        shutil.copy(silent, out)
        return
    cmd = ["ffmpeg", "-y", "-i", silent]
    fc, labels, idx = [], [], 1
    if voiced:
        parts, cursor = [], 0.0
        for s in voiced:
            cmd += ["-i", s["voice"]]
            vs = s["start"] + T / 2
            gap = max(0.0, vs - cursor)
            if gap > 0.001:
                fc.append(f"anullsrc=r=24000:cl=mono,atrim=duration={gap:.3f}[g{idx}]")
                parts.append(f"[g{idx}]")
            fc.append(f"[{idx}:a]aresample=24000,aformat=channel_layouts=mono[a{idx}]")
            parts.append(f"[a{idx}]")
            cursor = vs + s["voice_d"]
            idx += 1
        fc.append("".join(parts) + f"concat=n={len(parts)}:v=0:a=1[v0]")
        fc.append(f"[v0]apad=whole_dur={total:.3f}[voice]")
        labels.append("[voice]")
    if track:
        cmd += ["-stream_loop", "-1", "-i", track]
        vol = MUSIC_VOL_VOICE if voiced else MUSIC_VOL_PLAIN
        fc.append(f"[{idx}:a]volume={vol},afade=t=in:st=0:d=1,"
                  f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[mus]")
        labels.append("[mus]")
        idx += 1
    if events:
        evl = []
        for t, kind in events:
            cmd += ["-i", sfx_file(kind)]
            fc.append(f"[{idx}:a]aresample=24000,aformat=channel_layouts=mono,"
                      f"adelay=delays={int(t * 1000)}:all=1,volume={SFX_VOL}[e{idx}]")
            evl.append(f"[e{idx}]")
            idx += 1
        if len(evl) == 1:
            fc.append(f"{evl[0]}apad=whole_dur={total:.3f}[sfx]")
        else:
            fc.append("".join(evl) + f"amix=inputs={len(evl)}:normalize=0:duration=longest[sfx0]")
            fc.append(f"[sfx0]apad=whole_dur={total:.3f}[sfx]")
        labels.append("[sfx]")
    if len(labels) == 1:
        fc.append(f"{labels[0]}alimiter=limit=0.95[a]")
    else:
        fc.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,"
                  "alimiter=limit=0.95[a]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", out]
    run(cmd)


async def build_short(items, hook, out):
    """items: [{name, imgs, bullets, voices}]. Собирает ролик-подборку. Возвращает длительность."""
    k = len(items)
    plan = [dict(kind="hook", text=hook, soft=False, item=-1)]
    gen_used = [0]
    for ii, it in enumerate(items):
        title = f"{ii + 1}. {it['name']}" if k > 1 else it["name"]
        a = it["imgs"][0].size
        ar0 = a[0] / a[1]    # берём только фото того же формата, что первое, иначе рамка будет некрасивой
        it["imgs"] = [im for im in it["imgs"] if abs((im.size[0] / im.size[1]) / ar0 - 1) < 0.2]
        it["L"] = compute_layout(title, it["bullets"], ar0)
        gen_idx = {}
        if len(it["imgs"]) < 2 and gen_used[0] < GEN_MAX:     # реальных фото мало: дорисовываем иллюстрации
            for bi, prompt in enumerate(it.get("art", [])[:len(it["bullets"])]):
                if gen_used[0] >= GEN_MAX:
                    break
                gim = gen_image(prompt, ar0)
                if gim:
                    it["imgs"].append(gim)
                    gen_idx[bi] = len(it["imgs"]) - 1
                    gen_used[0] += 1
        if gen_idx:   # главное фото всегда реальное, иллюстрация только как вставка
            shots = [[0, gen_idx[bi]] if bi in gen_idx else [0] for bi in range(len(it["bullets"]))]
        else:
            shots = pick_shots(it["name"], it["voices"], it["imgs"])
        for bi in range(len(it["bullets"])):
            lst = shots[bi]
            plan.append(dict(kind="bullet", item=ii, stage=bi + 1, L=it["L"],
                             img=it["imgs"][lst[0]],
                             alt=it["imgs"][lst[1]] if len(lst) > 1 else None,
                             text=it["voices"][bi], soft=bi > 0))
    last = items[-1]
    cta_text = ("Всё это бесплатно в Телеграме. Ссылка в шапке канала." if k > 1
                else "Скачивай бесплатно в Телеграме. Ссылка в шапке канала.")
    prev = plan[-1]
    plan.append(dict(kind="cta", item=k - 1, stage=len(last["bullets"]) + 1, L=last["L"],
                     img=prev["alt"] or prev["img"], alt=None, text=cta_text, soft=True))

    # движение камеры: на один пост одно непрерывное движение, которое делится между его сценами
    for ii in range(k):
        idxs = [i for i, s in enumerate(plan) if s["item"] == ii]
        for j, i in enumerate(idxs):
            plan[i].update(motion=MOTIONS[ii % len(MOTIONS)], S=0.04,
                           p0=j / len(idxs), p1=(j + 1) / len(idxs))
    plan[0].update(motion="in", S=0.05, p0=0.0, p1=1.0)

    voice_ok = VOICE_ON
    pre = await gemini_tts_batch([x["text"] for x in plan]) if VOICE_ON else None
    if VOICE_ON and not pre:
        print("  Gemini TTS не сработал, использую запасной голос Microsoft")
    for si, s in enumerate(plan):
        s["png"] = f"{WORK}/scene{si}.png"
        s["layers"] = []
        if s["kind"] == "hook":
            draw_hook([it["imgs"][0] for it in items], hook, s["png"])
        else:
            ly = draw_scene(s["L"], s["img"], s["stage"], s["png"])
            ly["t0"] = 0.15
            s["layers"].append(ly)
            s["anim"] = ly
        s["voice_d"], s["voice"] = 0.0, None
        if voice_ok and pre:
            s["voice_d"], s["voice"] = pre[si]
        elif voice_ok:
            s["voice_d"], s["voice"] = await tts(s["text"], f"{WORK}/voice{si}.wav")
            if not s["voice_d"]:
                print("  озвучка недоступна, делаю ролик без голоса")
                voice_ok = False
                for p in plan:
                    p["voice_d"] = 0.0
        base = s["voice_d"] if s["voice_d"] else FALLBACK_DUR[s["kind"]]
        s["d"] = base + PAUSE + TRANSITION
        if s["kind"] == "bullet" and s.get("alt") is not None:    # второе фото возникает на середине фразы
            ly = photo_layer(s["L"], s["alt"])
            ly["t0"] = TRANSITION / 2 + base * SWAP_AT
            s["layers"].append(ly)
            s["swap"] = ly["t0"]

    t = 0.0
    for s in plan:           # момент начала каждой сцены на итоговой шкале времени
        s["start"] = t
        t += s["d"] - TRANSITION

    events = []              # звуки: (время, вид)
    for si, s in enumerate(plan):
        st = s["start"]
        if s["kind"] == "hook":
            events.append((st, "hit"))
        else:
            if not s["soft"]:
                events.append((st - 0.05, "whoosh"))
            events.append((st + s["anim"]["t0"] + 0.03, "ding" if s["kind"] == "cta" else "pop"))
            if "swap" in s:
                events.append((st + s["swap"], "tick"))

    subs = []
    if voice_ok:
        for s in plan:
            if s["kind"] != "bullet" or not s["voice_d"]:
                continue
            chunks = sub_chunks(s["text"])
            weights = [len(c) + 2 for c in chunks]
            at = s["start"] + TRANSITION / 2
            for c, w in zip(chunks, weights):
                dur = s["voice_d"] * w / sum(weights)
                png = f"{WORK}/sub{len(subs)}.png"
                h = draw_sub(c, png)
                subs.append(dict(png=png, a=at, b=at + dur, y=s["L"]["photo_bottom"] - h - 16))
                at += dur

    total = render_video(plan, subs)
    mix_audio(plan, total, out, events)
    return total


# ---------- основной цикл ----------

async def main():
    os.makedirs(WORK, exist_ok=True)
    ensure_fonts()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    used = set(state.get("used", []))
    fails = state.get("fails", {})
    types = state.get("types", {})
    last_type = state.get("last_type", "")
    tts_state["block"] = state.get("tts_block", {})
    done = 0
    try:
        async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
            target = await client.get_entity(TARGET)
            for _ in range(PER_RUN):
                cands = [p for p in await find_candidates(client, target, used)]
                print("Кандидатов:", len(cands))
                if not cands:
                    break
                run_types = classify(cands, types)
                counts = {}
                for v in run_types.values():
                    counts[v] = counts.get(v, 0) + 1
                print("Типы постов:", {TYPES.get(a, a): b for a, b in counts.items()})
                group, gtype = pick_group(cands, run_types, last_type)
                tag = f"[{TYPES.get(gtype, gtype)}: {', '.join('#' + str(p['id']) for p in group)}]"
                print(tag, "собираю ролик")
                try:
                    shutil.rmtree(WORK, ignore_errors=True)
                    os.makedirs(WORK, exist_ok=True)
                    got, photo_sets = [], []
                    for p in group:
                        paths = []
                        for i, m in enumerate(p["photos"][:PHOTOS_PER_ITEM]):
                            path = await client.download_media(m, file=f"{WORK}/p{p['id']}_{i}.jpg")
                            if path:
                                paths.append(path)
                        if paths:
                            got.append(p)
                            photo_sets.append(paths)
                    if not got:
                        raise RuntimeError("не удалось скачать фото")

                    print(tag, "запрос к Gemini")
                    data = gemini_script(got, gtype)
                    if not data:
                        raise RuntimeError("Gemini не вернул сценарий")

                    per_item = 3 if len(got) == 1 else 2
                    items = []
                    for it, paths in zip(data["items"], photo_sets):
                        bullets = [clean(b)[:80] for b in it["bullets"] if clean(b)][:per_item]
                        voices = [clean(v)[:150] for v in (it.get("voice") or []) if clean(v)]
                        while len(voices) < len(bullets):
                            voices.append(bullets[len(voices)])
                        art = [re.sub(r"[^A-Za-z0-9 ,.'-]", "", a)[:160] for a in (it.get("art") or [])
                               if isinstance(a, str)]
                        items.append(dict(name=clean(it["name"])[:38] or "Minecraft",
                                          imgs=[Image.open(x).convert("RGB") for x in paths],
                                          bullets=bullets, voices=voices[:len(bullets)], art=art))
                    hook = clean(data["hook"])[:80]

                    out = f"{WORK}/short.mp4"
                    total = await build_short(items, hook, out)
                    print(tag, f"ролик собран, {total:.1f} сек")

                    yt_title = (data.get("yt_title") or hook).strip()[:95]
                    yt_desc = (data.get("yt_description") or "").strip()
                    if "#shorts" not in yt_desc.lower():
                        yt_desc += "\n#Shorts #Minecraft"

                    yt_link = ""
                    if YT_ID and YT_SECRET and YT_REFRESH:
                        try:
                            yt_link = youtube_upload(out, yt_title, yt_desc)
                            print(tag, "загружено на YouTube:", yt_link)
                        except Exception as e:
                            print(tag, "YouTube не принял:", e)
                    head = f"YouTube: {yt_link}\n\n" if yt_link else "Загрузи вручную:\n\n"
                    caption = (head + f"НАЗВАНИЕ:\n{yt_title}\n\nОПИСАНИЕ:\n{yt_desc}")[:1024]
                    await client.send_file(
                        SHORTS_CHAT, out, caption=caption,
                        attributes=[DocumentAttributeVideo(duration=int(total), w=W, h=H,
                                                           supports_streaming=True)],
                    )
                    for p in got:
                        used.add(p["id"])
                        fails.pop(str(p["id"]), None)
                    last_type = gtype
                    done += 1
                    print(tag, "ролик отправлен в", SHORTS_CHAT)
                except Exception as e:
                    for p in group:
                        kk = str(p["id"])
                        fails[kk] = fails.get(kk, 0) + 1
                        print(tag, f"ошибка: {e} (пост #{p['id']}, попытка {fails[kk]}/{MAX_FAILS})")
                        if fails[kk] >= MAX_FAILS:
                            used.add(p["id"])
                            print(tag, f"пост #{p['id']} пропущен навсегда")
    finally:
        state["used"] = sorted(used)[-2000:]
        state["fails"] = fails
        state["types"] = dict(list(types.items())[-3000:])
        state["last_type"] = last_type
        today = pacific_today()
        state["tts_block"] = {m: d for m, d in tts_state["block"].items() if d == today}
        json.dump(state, open(STATE, "w"))
    print("Готово, роликов:", done)


if __name__ == "__main__":
    asyncio.run(main())
