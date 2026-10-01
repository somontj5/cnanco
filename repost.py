import os, json, asyncio, requests
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["TG_SESSION"]
SOURCES = [s.strip() for s in os.environ["SOURCE_CHANNELS"].split(",") if s.strip()]
TARGET = os.environ["TARGET_CHANNEL"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
STATE = "state.json"

PROMPT = (
    "Перепиши это описание к посту своими словами: сохрани смысл и язык, "
    "измени формулировки и структуру. Убери ссылки, @упоминания, названия "
    "каналов и рекламу. Верни только готовый текст, без пояснений.\n\n"
)

def rewrite(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY},
                      json={"contents": [{"parts": [{"text": PROMPT + text}]}]}, timeout=60)
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()[:1024]
    except Exception:
        print("Gemini error:", r.text[:300])
        return None

async def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        target = await client.get_entity(TARGET)
        for src in SOURCES:
            entity = await client.get_entity(src)
            last = state.get(src, 0)
            if last:
                it = client.iter_messages(entity, min_id=last, reverse=True)
            else:  # первый запуск: только за последние сутки
                it = client.iter_messages(entity, offset_date=cutoff, reverse=True)
            msgs = [m async for m in it]
            if not msgs:
                continue
            max_id = max(m.id for m in msgs)
            media = [m for m in msgs if m.video or m.audio]

            groups, order = {}, []
            for m in media:
                k = m.grouped_id or m.id
                if k not in groups:
                    groups[k] = []
                    order.append(k)
                groups[k].append(m)

            failed = False
            for k in order:
                grp = groups[k]
                old = next((m.message for m in grp if m.message), "")
                new = ""
                if old:
                    new = rewrite(old)
                    if new is None:
                        failed = True
                        break
                try:
                    await client.send_file(target, [m.media for m in grp], caption=new or None)
                except Exception as e:
                    print("Ошибка отправки:", e)
                    failed = True
                    break
                state[src] = max(m.id for m in grp)
                print("Отправлено из", src, "id", grp[0].id)
                await asyncio.sleep(3)
            if not failed:
                state[src] = max_id

    json.dump(state, open(STATE, "w"))

asyncio.run(main())