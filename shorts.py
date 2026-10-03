import os, re, json, glob, random, shutil, subprocess, asyncio, time, requests
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
MAX_PHOTOS = 5      # сколько фото брать из поста
MAX_FAILS = 3
STATE = "shorts_state.json"
WORK = "work"
W, H, FPS = 1080, 1920, 25

# --- озвучка ---
VOICE_ON = True                                   # False = ролик без голоса
VOICE = os.environ.get("TTS_VOICE", "").strip() or "ru-RU-DmitryNeural"   # мужской; женский: ru-RU-SvetlanaNeural
VOICE_RATE = "+10%"                               # скорость речи: "+0%" обычная, "+15%" бодрее
MUSIC_VOL_VOICE = 0.12                            # громкость музыки под голосом (0.0-1.0)
MUSIC_VOL_PLAIN = 0.60                            # громкость музыки, если голоса нет

# --- монтаж ---
TRANSITION = 0.35        # длительность перехода между сценами, сек
PAUSE = 0.45             # пауза после фразы, сек
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

PROMPT = (
    "Ты автор вирусных Shorts про Minecraft Bedrock. Цель: чтобы зритель, прочитав и послушав ролик, "
    "захотел СРАЗУ скачать мод и поиграть. По тексту поста напиши сценарий ролика. "
    "Используй только факты из текста, ничего не выдумывай, но подавай их ярко и конкретно.\n\n"
    "ПРАВИЛА:\n"
    "1. hook: первая фраза ролика, до 8 слов. Интрига, вопрос или неожиданный факт именно про этот мод. "
    "Не «Новый мод» и не «Смотрите». Примеры стиля: «Этот мод превращает выживание в RPG», "
    "«А ты знал, что в Майнкрафте можно так строить?».\n"
    "2. bullets: ровно 3 пункта по 40-75 символов. Каждый отвечает: ЧТО ИМЕННО появляется или меняется "
    "в игре и что игрок с этого получает. Называй конкретно: предметы, мобов, способности, механики, "
    "числа из текста. ЗАПРЕЩЕНЫ пустые фразы: «улучшает геймплей», «новые возможности», "
    "«интересные механики», «много нового». Версии игры и ссылки в пункты не пиши. "
    "Начинай пункт с сильного слова или глагола.\n"
    "3. voice: ровно 3 фразы для диктора, по одной на каждый пункт, по 8-16 слов, живая разговорная речь, "
    "как будто рассказываешь другу; можно на «ты». Без эмодзи и номеров версий.\n"
    "4. В hook и voice все иностранные слова пиши русскими буквами, как слышатся "
    "(Minecraft = Майнкрафт, Add-On = аддон), чтобы диктор прочитал правильно.\n\n"
    "Верни ТОЛЬКО JSON без пояснений:\n"
    '{"hook": "...",\n'
    ' "title": "заголовок до 45 символов, без эмодзи и хэштегов",\n'
    ' "bullets": ["...", "...", "..."],\n'
    ' "voice": ["...", "...", "..."],\n'
    ' "yt_title": "название видео для YouTube до 90 символов, с упоминанием Minecraft",\n'
    ' "yt_description": "2-3 предложения о моде или идее и призыв скачать бесплатно в Telegram, '
    'ссылка в шапке канала; в конце хэштеги #Shorts #Minecraft и ещё 2-3 по теме"}\n\n'
    "Текст поста:\n"
)


# ---------- Gemini ----------

def gemini_json(text):
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
                        "contents": [{"parts": [{"text": PROMPT + text}]}],
                        "generationConfig": {"temperature": 0.85, "responseMimeType": "application/json"},
                    },
                    timeout=60,
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
                if isinstance(data, dict) and data.get("title") and data.get("bullets"):
                    print(f"  Gemini {model}: OK")
                    return data
            except Exception:
                pass
            print(f"  Gemini {model}: неверный ответ: {r.text[:150]}")
            break
        print("  переключаюсь на следующую модель")
    return None


# ---------- шрифты и текст ----------

_font_cache = {}


def get_font(kind, size):
    spec, custom = {
        "title": (TITLE_FONT, "fonts/title.ttf"),
        "text": (TEXT_FONT, "fonts/text.ttf"),
        "cta": (CTA_FONT, "fonts/title.ttf"),
    }[kind]
    if os.path.exists(custom):
        path = custom
    else:
        if spec not in _font_cache:
            try:
                out = subprocess.run(["fc-match", "-f", "%{file}", spec],
                                     capture_output=True, text=True).stdout.strip()
            except Exception:
                out = ""
            _font_cache[spec] = out if out and os.path.exists(out) else FALLBACK_FONT
        path = _font_cache[spec]
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

def crop_mode(img, mode):
    """0 = целиком, 1/2/3 = приближение к углу или центру (тот же формат кадра)."""
    if mode == 0:
        return img
    w, h = img.size
    cw, ch = int(w * 0.66), int(h * 0.66)
    x, y = {1: (0, 0), 2: (w - cw, h - ch), 3: ((w - cw) // 2, (h - ch) // 2)}[mode]
    return img.crop((x, y, x + cw, y + ch))


def make_bg(img):
    bg = ImageOps.fit(img, (W, H), method=Image.LANCZOS).filter(ImageFilter.GaussianBlur(45))
    return ImageEnhance.Brightness(bg).enhance(0.40).convert("RGBA")


def photo_box(img, bw, bh):
    base = ImageOps.fit(img, (bw, bh), method=Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    base = ImageEnhance.Brightness(base).enhance(0.55)
    fg = ImageOps.contain(img, (bw, bh), method=Image.LANCZOS)
    base.paste(fg, ((bw - fg.size[0]) // 2, (bh - fg.size[1]) // 2))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw, bh), radius=32, fill=255)
    return base.convert("RGBA"), mask


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


def draw_scene(L, img, stage, out_png):
    """stage = сколько пунктов уже показано (1..N); stage = N+1 — финальная сцена с призывом."""
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
    for i, (by, bh) in enumerate(zip(L["bullet_ys"], L["b_heights"])):
        if i < stage:
            cur = (i == stage - 1) and not cta_on
            od.rounded_rectangle((X0, by, X0 + CW, by + bh), radius=30, fill=(15, 15, 25, 228),
                                 outline=ACCENT + (255,) if cur else (255, 255, 255, 110),
                                 width=5 if cur else 3)
        else:
            od.rounded_rectangle((X0, by, X0 + CW, by + bh), radius=30, fill=(15, 15, 25, 70),
                                 outline=(255, 255, 255, 40), width=2)
    od.rounded_rectangle((X0, L["cta_y"], X0 + CW, L["cta_y"] + L["cta_h"]), radius=40,
                         fill=ACCENT + (255 if cta_on else 55,))
    canvas = Image.alpha_composite(canvas, overlay)

    pb, mask = photo_box(img, L["box_w"], L["box_h"])
    canvas.paste(pb, (L["px"], L["photo_y"] + 8), mask)

    d = ImageDraw.Draw(canvas)
    for i, ln in enumerate(L["t_lines"]):
        d.text((W // 2, L["title_y"] + L["PAD"] + i * L["lh_t"] + L["lh_t"] // 2), ln,
               font=L["ft"], fill="white", anchor="mm")
    for i, (by, ls) in enumerate(zip(L["bullet_ys"], L["b_lines"])):
        if i >= stage:
            continue
        col = "white" if (i == stage - 1 or cta_on) else (205, 205, 215)
        for j, ln in enumerate(ls):
            d.text((W // 2, by + L["pad_b"] + j * L["lh_b"] + L["lh_b"] // 2), ln,
                   font=L["fb"], fill=col, anchor="mm")
    if cta_on:
        cta = f"Скачать в Telegram: {HANDLE}" if HANDLE else "Скачать бесплатно в Telegram"
        fs = 50
        while True:
            fc = get_font("cta", fs)
            if d.textlength(cta, font=fc) <= CW - 2 * L["PAD"]:
                break
            if fs <= 28:
                if cta != HANDLE and HANDLE:
                    cta, fs = HANDLE, 50
                    continue
                break
            fs -= 4
        d.text((W // 2, L["cta_y"] + 52), cta, font=fc, fill="black", anchor="mm")
        d.text((W // 2, L["cta_y"] + 100), "ссылка в шапке канала", font=get_font("text", 30),
               fill=(40, 40, 40), anchor="mm")
    canvas.convert("RGB").save(out_png)


def draw_hook(img, hook, out_png):
    """Заставка: крупная зацепка на жёлтой плашке + фото."""
    canvas = make_bg(img)
    CW, PAD, GAP = COLUMN_W, 44, 36
    X0 = (W - CW) // 2
    m = ImageDraw.Draw(canvas)
    f, lines, size = fit_lines(m, hook.upper(), "title", 104, CW - 2 * PAD, 4, 60)
    lh = int(size * 1.2)
    plate_h = len(lines) * lh + 2 * PAD
    avail = (H - SAFE_BOTTOM) - SAFE_TOP
    box_w = CW - 24
    aspect = img.size[0] / img.size[1]
    box_h = int(max(300, min(800, box_w / aspect, avail - plate_h - GAP - 16)))
    y = SAFE_TOP + max(0, (avail - (plate_h + GAP + box_h + 16)) // 2)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((X0, y, X0 + CW, y + plate_h), radius=40, fill=ACCENT + (255,),
                         outline=(0, 0, 0, 255), width=6)
    plate_y = y
    y += plate_h + GAP
    od.rounded_rectangle(((W - box_w) // 2 - 8, y, (W - box_w) // 2 + box_w + 8, y + box_h + 16),
                         radius=40, fill=(255, 255, 255, 235))
    canvas = Image.alpha_composite(canvas, overlay)
    pb, mask = photo_box(img, box_w, box_h)
    canvas.paste(pb, ((W - box_w) // 2, y + 8), mask)
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


async def tts(text, path):
    """Озвучивает текст нейроголосом Microsoft (edge-tts). Возвращает длительность или 0."""
    try:
        import edge_tts
    except ImportError:
        print("  TTS: не установлен edge-tts")
        return 0.0
    text = tts_prep(text)
    for voice in dict.fromkeys([VOICE, "ru-RU-SvetlanaNeural"]):
        try:
            await edge_tts.Communicate(text, voice, rate=VOICE_RATE).save(path)
            d = probe(path)
            if d > 0.3:
                return d
        except Exception as e:
            print(f"  TTS {voice}: {e}")
    return 0.0


# ---------- видео ----------

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise RuntimeError("ffmpeg завершился с ошибкой")


def motion_vf(kind, n, strength):
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    x, y = cx, cy
    if kind == "in":
        z = f"1+{strength}*on/{n}"
    elif kind == "out":
        z = f"{1 + strength}-{strength}*on/{n}"
    elif kind == "left":
        z = f"{1 + strength}"
        x = f"(iw-iw/zoom)*on/{n}"
    else:  # up
        z = f"{1 + strength}"
        y = f"(ih-ih/zoom)*(1-on/{n})"
    return (f"scale=2160:3840,zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},"
            f"format=yuv420p")


def render_video(scenes, subs, out):
    """scenes: список со словарями (png, d, motion, strength, flash). Возвращает общую длину."""
    n = len(scenes)
    T = TRANSITION
    clips = []
    for i, s in enumerate(scenes):
        clip = f"{WORK}/clip{i}.mp4"
        frames = int(s["d"] * FPS)
        vf = motion_vf(s["motion"], frames, s["strength"])
        if s.get("flash"):
            vf += ",fade=t=in:st=0:d=0.35:color=white"
        run(["ffmpeg", "-y", "-i", s["png"], "-vf", vf, "-t", f"{s['d']:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", clip])
        clips.append(clip)

    total = sum(s["d"] for s in scenes) - (n - 1) * T
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-f", "lavfi", "-i", f"color=c=0xFFC400:s={W}x12:r={FPS}:d={total + 1:.2f}"]
    for sb in subs:
        cmd += ["-loop", "1", "-t", f"{total + 1:.2f}", "-i", sb["png"]]

    fc, cur = [], "[0:v]"
    prev = None
    for k in range(1, n):
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


def mix_audio(scenes, total, out):
    silent = f"{WORK}/silent.mp4"
    T = TRANSITION
    voiced = [s for s in scenes if s.get("voice_d")]
    tracks = [t for ext in ("mp3", "m4a", "ogg", "wav") for t in glob.glob(f"music/*.{ext}")]
    track = random.choice(tracks) if tracks else None
    if track:
        print("  музыка:", os.path.basename(track))
    else:
        print("  папка music/ пуста, музыки не будет")

    if not voiced and not track:
        shutil.copy(silent, out)
        return
    cmd = ["ffmpeg", "-y", "-i", silent]
    fc = []
    idx = 1
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
    if track:
        cmd += ["-stream_loop", "-1", "-i", track]
        vol = MUSIC_VOL_VOICE if voiced else MUSIC_VOL_PLAIN
        fc.append(f"[{idx}:a]volume={vol},afade=t=in:st=0:d=1,"
                  f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[mus]")
    if voiced and track:
        fc.append("[voice][mus]amix=inputs=2:duration=first:dropout_transition=0,volume=2,"
                  "alimiter=limit=0.95[a]")
    elif voiced:
        fc.append("[voice]alimiter=limit=0.95[a]")
    else:
        fc.append("[mus]anull[a]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", out]
    run(cmd)


async def build_short(paths, data, out):
    """Собирает ролик из фото и сценария Gemini. Возвращает длительность."""
    title = clean(data["title"])[:60] or "Minecraft"
    hook = clean(data.get("hook") or "")[:70] or title
    bullets = [clean(b)[:80] for b in data["bullets"] if clean(b)][:3]
    voice_lines = [clean(v)[:150] for v in (data.get("voice") or []) if clean(v)]
    while len(voice_lines) < len(bullets):
        voice_lines.append(bullets[len(voice_lines)])
    voice_lines = voice_lines[:len(bullets)]

    imgs = [Image.open(p).convert("RGB") for p in paths]
    n_img = len(imgs)
    L = compute_layout(title, bullets, imgs[0].size[0] / imgs[0].size[1])

    nb = len(bullets)
    plan = [dict(kind="hook", text=hook)]
    plan += [dict(kind="bullet", text=voice_lines[i], k=i + 1) for i in range(nb)]
    plan += [dict(kind="cta", text="Скачивай бесплатно в Телеграме. Ссылка в шапке канала.")]

    voice_ok = VOICE_ON
    for si, s in enumerate(plan):
        img = crop_mode(imgs[si % n_img], (si // n_img) % 4)
        s["png"] = f"{WORK}/scene{si}.png"
        if s["kind"] == "hook":
            draw_hook(imgs[0], hook, s["png"])
        elif s["kind"] == "bullet":
            draw_scene(L, img, s["k"], s["png"])
        else:
            draw_scene(L, img, nb + 1, s["png"])
        s["motion"] = "in" if s["kind"] == "hook" else MOTIONS[si % len(MOTIONS)]
        s["strength"] = 0.14 if s["kind"] == "hook" else 0.08
        s["flash"] = s["kind"] == "hook"
        s["voice_d"] = 0.0
        if voice_ok:
            s["voice"] = f"{WORK}/voice{si}.mp3"
            s["voice_d"] = await tts(s["text"], s["voice"])
            if not s["voice_d"]:
                print("  озвучка недоступна, делаю ролик без голоса")
                voice_ok = False
                for p in plan:
                    p["voice_d"] = 0.0
        base = s["voice_d"] if s["voice_d"] else FALLBACK_DUR[s["kind"]]
        s["d"] = base + PAUSE + TRANSITION

    # моменты начала сцен на итоговой шкале времени
    t = 0.0
    for s in plan:
        s["start"] = t
        t += s["d"] - TRANSITION

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
                subs.append(dict(png=png, a=at, b=at + dur, y=L["photo_bottom"] - h - 16))
                at += dur

    total = render_video(plan, subs, out)
    mix_audio(plan, total, out)
    return total


# ---------- основной цикл ----------

async def main():
    os.makedirs(WORK, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    used = set(state.get("used", []))
    fails = state.get("fails", {})
    done = 0
    try:
        async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
            target = await client.get_entity(TARGET)
            cands = await find_candidates(client, target, used)
            print("Кандидатов:", len(cands))
            for p in reversed(cands):  # самые свежие первыми
                if done >= PER_RUN:
                    break
                tag = f"[пост #{p['id']}]"
                try:
                    shutil.rmtree(WORK, ignore_errors=True)
                    os.makedirs(WORK, exist_ok=True)
                    print(tag, "скачиваю фото:", len(p["photos"][:MAX_PHOTOS]))
                    paths = []
                    for i, m in enumerate(p["photos"][:MAX_PHOTOS]):
                        path = await client.download_media(m, file=f"{WORK}/p{p['id']}_{i}.jpg")
                        if path:
                            paths.append(path)
                    if not paths:
                        raise RuntimeError("не удалось скачать фото")

                    print(tag, "запрос к Gemini")
                    data = gemini_json(p["text"][:3000])
                    if not data:
                        raise RuntimeError("Gemini не вернул текст")
                    title = clean(data["title"])[:60] or "Minecraft"
                    yt_title = (data.get("yt_title") or title).strip()[:95]
                    yt_desc = (data.get("yt_description") or "").strip()
                    if "#shorts" not in yt_desc.lower():
                        yt_desc += "\n#Shorts #Minecraft"

                    out = f"{WORK}/short_{p['id']}.mp4"
                    total = await build_short(paths, data, out)
                    print(tag, f"ролик собран, {total:.1f} сек")

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
                    used.add(p["id"])
                    fails.pop(str(p["id"]), None)
                    done += 1
                    print(tag, "ролик отправлен в", SHORTS_CHAT)
                except Exception as e:
                    k = str(p["id"])
                    fails[k] = fails.get(k, 0) + 1
                    print(tag, f"ошибка: {e} (попытка {fails[k]}/{MAX_FAILS})")
                    if fails[k] >= MAX_FAILS:
                        used.add(p["id"])
                        print(tag, "пропущен навсегда")
    finally:
        state["used"] = sorted(used)[-2000:]
        state["fails"] = fails
        json.dump(state, open(STATE, "w"))
    print("Готово, роликов:", done)


if __name__ == "__main__":
    asyncio.run(main())
