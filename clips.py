"""Нарезки из видео партнёрского YouTube-канала (с разрешения автора).

Что делает за один запуск: берёт самое свежее ещё не обработанное длинное видео из RSS канала, скачивает его
(yt-dlp), по субтитрам выбирает через Gemini самые сильные моменты, режет их в вертикальные Shorts с русскими
субтитрами, названием автора и призывом скачать мод в Telegram, заливает на твой YouTube и присылает копию в Telegram.
Настройки лежат в config.json (раздел "clips") и меняются через бота.
"""
import os, re, sys, json, shutil, subprocess, asyncio, xml.etree.ElementTree as ET
import requests
from PIL import Image, ImageDraw
import shorts as S          # переиспользуем Gemini, шрифты, оформление и загрузку на YouTube

WORK = "work_clips"
STATE = "clips_state.json"
try:
    CC = json.load(open("config.json", encoding="utf-8")).get("clips") or {}
except Exception:
    CC = {}
CHANNEL_ID = CC.get("channel_id") or "UCkjxZqW8zRi4rexA3TMvmrA"     # ECKOSOLDIER (@ecko)
AUTHOR = CC.get("author") or "ECKOSOLDIER"
CREDIT = CC.get("credit") or "@ecko"
PER_VIDEO = max(1, min(3, int(CC.get("per_video") or 2)))           # сколько нарезок делать из одного видео
MIN_LEN = int(CC.get("min_len") or 20)
MAX_LEN = min(59, int(CC.get("max_len") or 45))
SUBS = CC.get("subs", True)                                         # русские субтитры поверх видео
ENABLED = CC.get("enabled", True)
SMART = CC.get("smart", True)                                       # сначала понять смысл всего видео, резать только моменты обзора
DUB = CC.get("dub", True)                                           # русская озвучка поверх оригинала
DUB_ENGINE = (CC.get("dub_engine") or "auto").lower()               # edge = голос Microsoft (точнее по времени), auto = сначала Gemini TTS
if CC.get("voice"):
    S.VOICE = CC["voice"]                                           # например ru-RU-DmitryNeural или ru-RU-SvetlanaNeural
CHARS_PER_SEC = 15                                                  # сколько русских символов диктор произносит за секунду
MAX_TEMPO = 1.45                                                    # максимум ускорения озвучки, чтобы влезть в оригинальный тайминг
ORIG_VOL = 0.55                                                     # громкость оригинального звука под голосом (звуки игры остаются)
MIN_VIDEO = 150                                                     # видео короче 2,5 минут не нарезаем (это уже Shorts)
FORMAT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
SUB_Y, CTA_Y, CTA_H = 1280, 1440, 120      # положение субтитров и плашки внизу (в безопасной зоне Shorts)


# ---------- RSS и скачивание ----------

def fetch_feed():
    r = requests.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}", timeout=30)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
          "m": "http://search.yahoo.com/mrss/"}
    out = []
    for e in ET.fromstring(r.content).findall("a:entry", ns):
        out.append(dict(id=e.find("yt:videoId", ns).text, title=(e.find("a:title", ns).text or "").strip(),
                        desc=(e.findtext("m:group/m:description", default="", namespaces=ns) or "").strip()))
    return out


def download(vid):
    """Скачивает видео и английские субтитры. Возвращает (путь_к_видео, путь_к_vtt или None, ошибка)."""
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    extra = []
    cookies = os.environ.get("YT_COOKIES", "").strip()
    if cookies:                          # cookies.txt из браузера: помогает, когда YouTube блокирует серверы GitHub
        open(f"{WORK}/cookies.txt", "w", encoding="utf-8").write(cookies + "\n")
        extra = ["--cookies", f"{WORK}/cookies.txt"]
    attempts = [[], ["--js-runtimes", "node"], ["--extractor-args", "youtube:player_client=tv,web_safari"]]
    err = ""
    for att in attempts:
        cmd = ([sys.executable, "-m", "yt_dlp"] + extra + att +
               ["--no-playlist", "-f", FORMAT, "--merge-output-format", "mp4",
                "--write-auto-subs", "--write-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
                "-o", f"{WORK}/src.%(ext)s", f"https://www.youtube.com/watch?v={vid}"])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if os.path.exists(f"{WORK}/src.mp4"):
            vtts = sorted(f for f in os.listdir(WORK) if f.endswith(".vtt"))
            return f"{WORK}/src.mp4", (f"{WORK}/{vtts[0]}" if vtts else None), ""
        err = (r.stderr or r.stdout or "")[-400:].replace("\n", " ")
        print(f"  yt-dlp {' '.join(att) or '(обычный режим)'}: не вышло: {err[:200]}")
    return None, None, err


# ---------- субтитры ----------

def _sec(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_vtt(path):
    """Субтитры YouTube -> [(начало, конец, текст)]. Повторы строк (автосубтитры показывают предыдущую заново) убираются."""
    txt = open(path, encoding="utf-8", errors="replace").read()
    cues = []
    for block in re.split(r"\n\s*\n", txt):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        m = re.match(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)", lines[i].strip())
        if not m:
            continue
        st, en = _sec(*m.group(1, 2, 3, 4)), _sec(*m.group(5, 6, 7, 8))
        tl = [re.sub(r"<[^>]+>", "", l).strip() for l in lines[i + 1:]]
        cues.append((st, en, [t for t in tl if t]))
    out, prev = [], []
    for st, en, tl in cues:
        new = [t for t in tl if t not in prev]
        if new and en - st > 0.05:
            out.append((st, en, " ".join(new)))
        prev = tl
    return out


# ---------- выбор моментов ----------

def plan_clips(video, cues, duration):
    """Gemini выбирает лучшие отрезки по субтитрам. Если не вышло, берёт равномерно разнесённые."""
    def fallback():
        out = []
        for k in range(PER_VIDEO):
            st = duration * (0.2 + 0.35 * k)
            out.append(dict(start=st, end=min(duration - 1, st + MAX_LEN), title=video["title"][:45],
                            yt_title=video["title"][:85]))
        return out

    if not cues:
        print("  субтитров нет, беру равномерные отрезки")
        return fallback()
    tr = "\n".join(f"[{int(a) // 60:02d}:{int(a) % 60:02d}] {t[:140]}" for a, b, t in cues)[:26000]
    prompt = (
        f"Ты редактор вертикальных Shorts про Minecraft Bedrock. Ниже английские субтитры обзора и описание "
        f"видео автора {AUTHOR}. Выбери {PER_VIDEO} самых цепляющих отрезка длиной {MIN_LEN}-{MAX_LEN} секунд: "
        "яркий момент показа аддона или механики, понятный без остального видео. Начало и конец ставь по границам "
        "фраз (по таймкодам субтитров), отрезки не должны пересекаться.\n"
        "Для каждого напиши: title: заголовок на РУССКОМ до 45 символов без эмодзи (интрига или польза, без штампов "
        "«полностью изменит»); yt_title: русское название для YouTube до 85 символов, со словом Minecraft.\n"
        'Верни ТОЛЬКО JSON: {"clips": [{"start": секунды, "end": секунды, "title": "...", "yt_title": "..."}]}\n\n'
        f"Название видео: {video['title']}\nОписание: {video['desc'][:700]}\n\nСубтитры:\n{tr}")

    def check(d):
        good, last_end = [], -1
        try:
            for c in sorted(d["clips"], key=lambda c: float(c["start"])):
                st, en = float(c["start"]), float(c["end"])
                if en - st > MAX_LEN + 8:
                    en = st + MAX_LEN
                if st < 0 or en > duration + 1 or en - st < MIN_LEN - 6 or st < last_end:
                    continue
                good.append(dict(start=st, end=en, title=str(c.get("title") or video["title"])[:45],
                                 yt_title=str(c.get("yt_title") or c.get("title") or video["title"])[:85]))
                last_end = en
        except Exception:
            return False
        d["clips"] = good[:PER_VIDEO]
        return bool(good)

    d = S.gemini_raw(prompt, check)
    return d["clips"] if d else fallback()


def translate(texts):
    """Русский перевод субтитров (короткий, живой). При сбое возвращает None."""
    if not texts:
        return []
    out = []
    for i in range(0, len(texts), 60):
        part = texts[i:i + 60]
        lines = "\n".join(f"{k + 1}. {t}" for k, t in enumerate(part))
        prompt = ("Переведи на русский субтитры обзора Minecraft: коротко, живо, как говорят геймеры. "
                  "Названия аддонов, модов и предметов сохрани как в оригинале. "
                  f'Верни ТОЛЬКО JSON {{"ru": ["...", ...]}} ровно {len(part)} строк, в том же порядке.\n\n{lines}')
        d = S.gemini_raw(prompt, lambda x, n=len(part): isinstance(x.get("ru"), list) and len(x["ru"]) == n)
        if not d:
            return None
        out += [str(x) for x in d["ru"]]
    return out


# ---------- умный выбор: сначала смысл всего видео ----------

def _snap_clip(cues, st, en):
    """Двигает границы к границам фраз: начало на старт реплики, конец на конец (лучше там, где стоит точка)."""
    starts = [c[0] for c in cues]
    s = min(starts, key=lambda x: abs(x - st))
    if abs(s - st) > 4:
        s = st
    near = [c for c in cues if abs(c[1] - en) <= 4]
    if near:
        e = min(near, key=lambda c: (c[2].rstrip()[-1:] not in ".?!", abs(c[1] - en)))[1]
    else:
        e = en
    if e - s > MAX_LEN:
        ok = [c[1] for c in cues if s + MIN_LEN <= c[1] <= s + MAX_LEN]
        e = max(ok) if ok else s + MAX_LEN
    return s, e


def analyze_video(video, cues, duration):
    """Этап 1. Gemini читает ВСЕ субтитры и размечает видео: где обзор аддона/мода/карты, а где болтовня, реклама, интро."""
    tr = "\n".join(f"[{int(a)}] {t[:200]}" for a, b, t in cues)[:150000]
    prompt = (
        f"Ты редактор. Ниже полные английские субтитры видео автора {AUTHOR} про Minecraft Bedrock (аддон, мод, карта, "
        f"ресурспак). В скобках секунды от начала. Длина видео {duration:.0f} секунд.\n"
        "Пойми смысл всего ролика и разбей его на последовательные части. Для каждой укажи kind:\n"
        '- "review": автор АКТИВНО показывает и разбирает, что есть в аддоне/моде/карте или что он меняет '
        "(новые предметы, мобы, механики, постройки, команды, настройки, сравнение до/после);\n"
        '- "intro": приветствие, тизер, «в этом видео»; "chat": болтовня, просьбы подписаться, личное; '
        '"ad": реклама, спонсор, ссылки; "outro": прощание; "other": остальное.\n'
        "Кратко (по-русски) напиши what: что именно показывают в этой части. И общий summary на 2-3 предложения.\n"
        'Верни ТОЛЬКО JSON: {"summary": "...", "sections": [{"start": сек, "end": сек, "kind": "review", "what": "..."}]}\n\n'
        f"Название: {video['title']}\nОписание: {video['desc'][:700]}\n\nСубтитры:\n{tr}")

    def check(d):
        good = []
        try:
            for x in d["sections"]:
                a, b = float(x["start"]), float(x["end"])
                if b - a >= 3:
                    good.append(dict(start=max(0.0, a), end=min(duration, b), kind=str(x.get("kind", "other")).lower(),
                                     what=str(x.get("what", ""))[:160]))
        except Exception:
            return False
        d["sections"] = sorted(good, key=lambda x: x["start"])
        d["summary"] = str(d.get("summary", ""))[:600]
        return any(x["kind"] == "review" for x in good)

    return S.gemini_raw(prompt, check)


def _review_share(info, st, en):
    """Какая доля отрезка лежит внутри частей kind=review."""
    cover = 0.0
    for x in info["sections"]:
        if x["kind"] == "review":
            cover += max(0.0, min(en, x["end"]) - max(st, x["start"]))
    return cover / max(0.1, en - st)


def pick_clips(video, info, cues, duration):
    """Этап 2. Выбор самых сильных отрезков строго внутри частей обзора, с учётом смысла всего видео."""
    rev = [x for x in info["sections"] if x["kind"] == "review"]
    lines = []
    for a, b, t in cues:
        if any(x["start"] - 1 <= a <= x["end"] + 1 for x in rev):
            lines.append(f"[{int(a)}] {t[:160]}")
    outline = "\n".join(f"- {x['start']:.0f}-{x['end']:.0f} с ({x['kind']}): {x['what']}" for x in info["sections"])
    prompt = (
        f"Ты редактор вертикальных Shorts про Minecraft Bedrock. Общий смысл видео автора {AUTHOR}: {info['summary']}\n"
        f"Структура видео:\n{outline}\n\n"
        f"Выбери {PER_VIDEO} самых сильных отрезка длиной {MIN_LEN}-{MAX_LEN} секунд ТОЛЬКО из частей review: "
        "там, где автор прямо показывает фишку аддона/мода/карты или изменение, и это понятно без остального видео. "
        "Отрезок начинай с начала мысли (сразу с показа, без «так, давайте посмотрим»), заканчивай на конце фразы. "
        "Не бери интро, болтовню, рекламу, просьбы подписаться. Отрезки не пересекаются, лучше из разных частей.\n"
        "Для каждого: title: заголовок на РУССКОМ до 45 символов без эмодзи; yt_title: русское название для YouTube "
        "до 85 символов со словом Minecraft.\n"
        'Верни ТОЛЬКО JSON: {"clips": [{"start": сек, "end": сек, "title": "...", "yt_title": "..."}]}\n\n'
        f"Название видео: {video['title']}\n\nСубтитры частей review:\n" + "\n".join(lines)[:90000])

    def check(d):
        good, last_end = [], -1
        try:
            for c in sorted(d["clips"], key=lambda c: float(c["start"])):
                st, en = _snap_clip(cues, float(c["start"]), float(c["end"]))
                if st < 0 or en > duration + 1 or en - st < MIN_LEN - 6 or st < last_end:
                    continue
                if _review_share(info, st, en) < 0.7:
                    print(f"  отрезок {st:.0f}-{en:.0f} с вне обзора, пропускаю")
                    continue
                good.append(dict(start=st, end=en, title=str(c.get("title") or video["title"])[:45],
                                 yt_title=str(c.get("yt_title") or c.get("title") or video["title"])[:85]))
                last_end = en
        except Exception:
            return False
        d["clips"] = good[:PER_VIDEO]
        return bool(good)

    d = S.gemini_raw(prompt, check)
    return d["clips"] if d else None


def clips_from_sections(video, info, cues):
    """Запасной вариант: если Gemini не выбрал, режем начала самых длинных частей обзора."""
    rev = sorted([x for x in info["sections"] if x["kind"] == "review"], key=lambda x: x["start"] - x["end"])
    out = []
    for x in rev:
        st, en = _snap_clip(cues, x["start"], min(x["end"], x["start"] + MAX_LEN))
        if en - st >= MIN_LEN - 6:
            out.append(dict(start=st, end=en, title=video["title"][:45], yt_title=video["title"][:85]))
        if len(out) >= PER_VIDEO:
            break
    return sorted(out, key=lambda c: c["start"])


def plan_clips_smart(video, cues, duration):
    """Двухэтапный выбор. На любом сбое откатывается к прежнему plan_clips."""
    if not SMART or not cues:
        return plan_clips(video, cues, duration)
    info = analyze_video(video, cues, duration)
    if not info:
        print("  анализ всего видео не удался, использую обычный выбор")
        return plan_clips(video, cues, duration)
    rev = [x for x in info["sections"] if x["kind"] == "review"]
    print(f"  смысл видео: {info['summary'][:200]}")
    print(f"  частей: {len(info['sections'])}, из них обзор: {len(rev)}, "
          f"{sum(x['end'] - x['start'] for x in rev):.0f} с")
    clips = pick_clips(video, info, cues, duration) or clips_from_sections(video, info, cues)
    return clips or plan_clips(video, cues, duration)


# ---------- русская озвучка ----------

def make_segments(cues, st, en):
    """Склеивает мелкие реплики субтитров в законченные куски речи (2-6 с), чтобы перевод был цельным."""
    segs, cur = [], None
    for a, b, t in cues:
        a, b = max(a, st), min(b, en)
        if b - a < 0.05:
            continue
        if cur and (a - cur[1] > 0.8 or cur[1] - cur[0] >= 6.5
                    or (cur[2].rstrip()[-1:] in ".?!" and cur[1] - cur[0] >= 2.0)):
            segs.append(tuple(cur))
            cur = None
        cur = [cur[0], b, cur[2] + " " + t] if cur else [a, b, t]
    if cur:
        segs.append(tuple(cur))
    return segs


def translate_segments(segs, video):
    """Перевод для озвучки: ru (для субтитров) и say (для голоса, английские названия записаны по-русски).
    Длина каждой фразы ограничена временем, которое у неё есть. Возвращает (ru, say) или None."""
    n = len(segs)
    rows = []
    for i, (a, b, t) in enumerate(segs):
        slot = (segs[i + 1][0] if i + 1 < n else b) - a
        rows.append(f"{i + 1}. [до {max(12, int(max(b - a, slot) * CHARS_PER_SEC * 1.1))} симв.] {t}")
    prompt = (
        "Ты переводчик-диктор: делаешь русскую озвучку обзора Minecraft Bedrock. Ниже куски речи автора подряд. "
        "Переведи живо и естественно, как говорит геймер, от первого лица как в оригинале; фразы должны складываться "
        "в связный рассказ. Не длиннее лимита в скобках (озвучка должна успеть за видео), сокращай, но не теряй суть.\n"
        "Для каждой строки верни два поля: ru: перевод для субтитров (названия аддонов, модов, предметов оставь как в "
        "оригинале); say: тот же текст для диктора, где английские названия и слова записаны русскими буквами "
        "так, как их произносят (Minecraft -> Майнкрафт), без цифр-аббревиатур, числа словами.\n"
        f'Верни ТОЛЬКО JSON {{"ru": [...], "say": [...]}}, в каждом ровно {n} строк, в том же порядке.\n\n'
        f"Видео: {video['title']}\n\n" + "\n".join(rows))
    d = S.gemini_raw(prompt, lambda x: isinstance(x.get("ru"), list) and isinstance(x.get("say"), list)
                     and len(x["ru"]) == n and len(x["say"]) == n)
    if not d:
        return None
    ru = [str(x).strip() for x in d["ru"]]
    say = [str(y).strip() or r for y, r in zip(d["say"], ru)]
    return ru, say


async def synth(say):
    """Озвучивает куски по одному (так тайминг точный). Возвращает [(длительность, файл)] или None."""
    os.makedirs(S.WORK, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    if DUB_ENGINE == "auto":
        pre = await S.gemini_tts_batch(say)
        if pre and len(pre) == len(say):
            return pre
    out = []
    for i, text in enumerate(say):
        d, f = await S.tts(text, f"{WORK}/tts{i}.wav")
        if not f:
            print(f"  озвучка куска {i + 1} не получилась")
            return None
        out.append((d, f))
    return out


def schedule(segs, tts, st, dur):
    """Раскладывает куски озвучки по времени: ускоряет, если русский длиннее оригинала, и не даёт им накладываться.
    Возвращает ([(начало, длина, темп, файл)], во сколько закончится озвучка) в секундах от начала нарезки."""
    items, cursor = [], 0.0
    for i, ((a, b, _), (d, f)) in enumerate(zip(segs, tts)):
        a0 = a - st
        nxt = (segs[i + 1][0] - st) if i + 1 < len(segs) else dur
        start = max(a0, cursor)
        slot = max(0.8, nxt - start)
        tempo = min(MAX_TEMPO, d / slot) if d > slot else 1.0
        real = d / tempo
        items.append((start, real, tempo, f))
        cursor = start + real + 0.1
    return items, (items[-1][0] + items[-1][1] if items else 0.0)


def mix_dub(items, dur, out):
    """Собирает дорожку озвучки длиной dur: каждый кусок ставится в своё время."""
    cmd, fc = ["ffmpeg", "-y"], []
    for j, (start, real, tempo, f) in enumerate(items):
        cmd += ["-i", f]
        flt = f"[{j}:a]aresample=44100,aformat=channel_layouts=mono"
        if tempo > 1.01:
            flt += f",atempo={tempo:.3f}"
        fc.append(flt + f",adelay={int(start * 1000)}:all=1[d{j}]")
    n = len(items)
    fc.append("".join(f"[d{j}]" for j in range(n)) +
              f"amix=inputs={n}:duration=longest:normalize=0,apad=whole_dur={dur:.2f}[dub]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[dub]", "-t", f"{dur:.2f}", "-ar", "44100", "-ac", "1", out]
    S.run(cmd)


def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())


# ---------- оформление ----------

def title_overlay(title, path):
    """Плашка с заголовком: сохраняет только нужную область. Возвращает y, куда её накладывать."""
    im = Image.new("RGBA", (S.W, S.H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    CW, PAD = S.COLUMN_W, 30
    X0 = (S.W - CW) // 2
    f, lines, size = S.fit_lines(d, title, "title", 70, CW - 2 * PAD, 3, 44)
    lh = int(size * 1.22)
    h, y = len(lines) * lh + 2 * PAD, S.SAFE_TOP + 20
    d.rounded_rectangle((X0, y, X0 + CW, y + h), radius=36, fill=S.DARK, outline=S.ACCENT + (255,), width=5)
    for i, ln in enumerate(lines):
        d.text((S.W // 2, y + PAD + i * lh + lh // 2), ln, font=f, fill="white", anchor="mm")
    im.crop((0, y - 4, S.W, y + h + 4)).save(path)
    return y - 4


def cta_overlay(path):
    """Жёлтая плашка с призывом и названием автора. Возвращает y для наложения."""
    im = Image.new("RGBA", (S.W, CTA_H + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    CW = S.COLUMN_W
    X0 = (S.W - CW) // 2
    d.rounded_rectangle((X0, 4, X0 + CW, 4 + CTA_H), radius=36, fill=S.ACCENT + (255,))
    cta = f"Скачать мод в Telegram: {S.HANDLE}" if S.HANDLE else "Скачать мод бесплатно в Telegram"
    fs = 46
    while d.textlength(cta, font=S.get_font("cta", fs)) > CW - 60 and fs > 26:
        fs -= 4
    d.text((S.W // 2, 4 + 40), cta, font=S.get_font("cta", fs), fill="black", anchor="mm")
    credit = f"Оригинал: {AUTHOR} ({CREDIT})"
    cs = 30
    while d.textlength(credit, font=S.get_font("text", cs)) > CW - 60 and cs > 18:
        cs -= 2
    d.text((S.W // 2, 4 + 88), credit, font=S.get_font("text", cs), fill=(40, 40, 40), anchor="mm")
    im.save(path)
    return CTA_Y - 4


def render(src, clip, sub_cues, out, dub=None):
    """Вертикальный ролик 1080x1920: размытый фон, видео по центру, заголовок, субтитры, плашка внизу."""
    st, en = clip["start"], clip["end"]
    dur = en - st
    ty = title_overlay(clip["title"], f"{WORK}/title.png")
    cy = cta_overlay(f"{WORK}/cta.png")
    subs = []
    for k, (a, b, text) in enumerate(sub_cues):          # субтитры: длинные реплики делим на короткие куски
        a, b = max(0.0, a - st), min(dur, b - st)
        if b - a < 0.3:
            continue
        chunks = S.sub_chunks(text, max_words=6, max_chars=38)
        weights = [len(c) + 2 for c in chunks]
        t = a
        for c, w in zip(chunks, weights):
            d_ = (b - a) * w / sum(weights)
            png = f"{WORK}/sub{len(subs)}.png"
            S.draw_sub(c, png)
            subs.append((png, t, t + d_))
            t += d_
    cmd = ["ffmpeg", "-y", "-ss", f"{st:.2f}", "-t", f"{dur:.2f}", "-i", src,
           "-loop", "1", "-t", f"{dur:.2f}", "-i", f"{WORK}/title.png",
           "-loop", "1", "-t", f"{dur:.2f}", "-i", f"{WORK}/cta.png"]
    for png, a, b in subs:
        cmd += ["-loop", "1", "-t", f"{dur:.2f}", "-i", png]
    if dub:
        cmd += ["-i", dub]
    fc = [f"[0:v]split=2[a][b]",
          # фон: размываем маленькую копию и растягиваем (в разы быстрее, чем размывать полный кадр)
          f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,scale={S.W}:{S.H},eq=brightness=-0.18[bg]",
          f"[b]scale={S.W}:-2[fg]",
          "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]",
          f"[v0][1:v]overlay=0:{ty}[v1]", f"[v1][2:v]overlay=0:{cy}[v2]"]
    cur = "[v2]"
    for j, (png, a, b) in enumerate(subs):
        nxt = f"[s{j}]"
        fc.append(f"{cur}[{3 + j}:v]overlay=0:{SUB_Y}:enable='between(t,{a:.2f},{b:.2f})'{nxt}")
        cur = nxt
    fc.append(f"{cur}format=yuv420p[vout]")
    if dub:
        k = 3 + len(subs)
        if has_audio(src):       # оригинал приглушается, когда говорит диктор, и возвращается в паузах
            fc += [f"[{k}:a]asplit=2[dv][dk]",
                   f"[0:a:0]volume={ORIG_VOL}[oa]",
                   "[oa][dk]sidechaincompress=threshold=0.02:ratio=14:attack=10:release=350[du]",
                   "[du][dv]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]"]
        else:
            fc.append(f"[{k}:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        amap, af = ["-map", "[aout]"], []
    else:
        amap, af = ["-map", "0:a:0?"], ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]"] + amap + af + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k", "-r", "30", "-movflags", "+faststart", "-t", f"{dur:.2f}", out]
    S.run(cmd)


def description(video, clip):
    return (f"{clip['yt_title']}\n\n"
            f"Оригинальное видео: https://youtu.be/{video['id']}\n"
            f"Автор: {AUTHOR} ({CREDIT}). Нарезка опубликована с разрешения автора."
            + (" Русская озвучка." if clip.get("dubbed") else "") + "\n\n"
            f"Скачать моды бесплатно: Telegram {S.HANDLE or ''} (ссылка в шапке канала).\n\n"
            "#Shorts #Minecraft #MinecraftBedrock")


# ---------- основной цикл ----------

async def main():
    if not ENABLED:
        print("Нарезки выключены в настройках бота")
        return
    S.ensure_fonts()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    done = set(state.get("done", []))
    feed = fetch_feed()
    print(f"В ленте канала {AUTHOR}: {len(feed)} видео, новых: {sum(1 for v in feed if v['id'] not in done)}")
    results = []
    try:
        for video in feed:
            if video["id"] in done:
                continue
            tag = f"[{video['id']}]"
            print(tag, video["title"])
            src, vtt, err = download(video["id"])
            if not src:
                print(tag, "не удалось скачать, попробую в следующий запуск. Если YouTube просит войти, добавь секрет "
                           "YT_COOKIES (cookies.txt из браузера).")
                break
            duration = S.probe(src)
            if duration < MIN_VIDEO:
                print(tag, f"видео короткое ({duration:.0f} с), это уже Shorts, пропускаю")
                done.add(video["id"])
                continue
            cues = parse_vtt(vtt) if vtt else []
            clips = plan_clips_smart(video, cues, duration)
            for n, clip in enumerate(clips, 1):
                print(tag, f"нарезка {n}/{len(clips)}: {clip['start']:.0f}-{clip['end']:.0f} с, «{clip['title']}»")
                window = [c for c in cues if c[1] > clip["start"] and c[0] < clip["end"]]
                sub_cues, dub = [], None
                if DUB and window:                       # озвучка: перевод цельными кусками + голос + субтитры в такт
                    st0, en0 = clip["start"], clip["end"]
                    segs = make_segments(window, st0, en0)
                    tr = translate_segments(segs, video) if segs else None
                    tts = await synth(tr[1]) if tr else None
                    if tr and tts:
                        items, need = schedule(segs, tts, st0, en0 - st0)
                        if need > en0 - st0:             # русская речь длиннее: чуть продлеваем ролик, чтобы не обрезать
                            clip["end"] = min(st0 + 59.0, duration - 0.2, st0 + need + 0.3)
                        dur_ = clip["end"] - st0
                        dub = f"{WORK}/dub{n}.wav"
                        mix_dub(items, dur_, dub)
                        clip["dubbed"] = True
                        if SUBS:
                            sub_cues = [(st0 + it[0], st0 + it[0] + it[1], t) for it, t in zip(items, tr[0])]
                    else:
                        print(tag, "озвучка не получилась, делаю ролик с субтитрами и оригинальным звуком")
                if not dub and SUBS and window:
                    ru = translate([c[2] for c in window])
                    if ru:
                        sub_cues = [(a, b, t) for (a, b, _), t in zip(window, ru)]
                    else:
                        print(tag, "перевод не получился, ролик без субтитров")
                out = f"{WORK}/clip{n}.mp4"
                render(src, clip, sub_cues, out, dub)
                link = ""
                if S.YT_ID and S.YT_SECRET and S.YT_REFRESH:
                    try:
                        link = S.youtube_upload(out, clip["yt_title"], description(video, clip))
                        print(tag, "загружено на YouTube:", link)
                    except Exception as e:
                        print(tag, "YouTube не принял:", e)
                results.append((out, clip, link))
            done.add(video["id"])
            break                      # одно видео за запуск
    finally:
        state["done"] = sorted(done)[-300:]
        json.dump(state, open(STATE, "w"))
    if results:
        async with S.TelegramClient(S.StringSession(S.SESSION), S.API_ID, S.API_HASH) as client:
            for out, clip, link in results:
                head = f"YouTube: {link}\n\n" if link else "Загрузи вручную:\n\n"
                await client.send_file(S.SHORTS_CHAT, out, caption=(head + clip["yt_title"])[:1024],
                                       attributes=[S.DocumentAttributeVideo(duration=int(clip["end"] - clip["start"]),
                                                                            w=S.W, h=S.H, supports_streaming=True)])
    print("Готово, нарезок:", len(results))


if __name__ == "__main__":
    asyncio.run(main())
