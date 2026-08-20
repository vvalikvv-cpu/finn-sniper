import os
import asyncio
import logging
import sqlite3
import html
import feedparser
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from google import genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Например: @finn_sniper_kupp

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# База данных
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT)")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
conn.commit()

# Шаблоны сообщений
TEXTS = {
    "ru": {
        "welcome": "🎯 <b>Добро пожаловать в Finn Sniper!</b>\n\nЯ отслеживаю бесплатные лоты и выгодные находки на Finn.no в реальном времени.",
        "ai_title": "✨ <b>Оценка Gemini AI:</b>",
        "btn_finn": "Открыть на Finn.no ↗",
        "btn_msg": "💬 Сообщение владельцу",
        "msg_template": "Hei! Jeg er veldig interessert og kan hente denne i dag. Passer det for deg?"
    },
    "no": {
        "welcome": "🎯 <b>Velkommen til Finn Sniper!</b>\n\nJeg overvåker gratiskupp og gode tilbud på Finn.no i sanntid.",
        "ai_title": "✨ <b>Gemini AI Vurdering:</b>",
        "btn_finn": "Se annonse på Finn.no ↗",
        "btn_msg": "💬 Melding til selger",
        "msg_template": "Hei! Jeg er veldig interessert og kan hente i dag hvis det passer for deg."
    }
}

async def analyze_with_gemini(title: str, desc: str) -> str:
    if not gemini_client:
        return "Ingen AI-vurdering tilgjengelig."
    try:
        prompt = (
            f"Vurder denne gratis-annonsen fra Finn.no kort på norsk og russisk (maks 2 setninger per språk):\n"
            f"Tittel: {title}\n"
            f"Beskrivelse: {desc}\n"
            f"Er dette attraktivt / har verdi? Noen feil oppgitt?"
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return "AI-analyse midlertidig utilgjengelig."

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("INSERT OR REPLACE INTO users (user_id, lang) VALUES (?, COALESCE((SELECT lang FROM users WHERE user_id = ?), 'no'))", (user_id, user_id))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇳🇴 Norsk", callback_data="lang_no"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")
        ]
    ])
    await message.answer("Velg språk / Выберите язык:", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, callback.from_user.id))
    conn.commit()
    t = TEXTS[lang]
    await callback.message.edit_text(t["welcome"], parse_mode="HTML")
    await callback.answer()

async def monitor_finn():
    rss_url = "https://www.finn.no/bap/forsale/search.html?price_to=0&trade_type=2&sort=PUBLISHED_DESC&sub_category=1.93.3905"
    while True:
        try:
            feed = await asyncio.to_thread(feedparser.parse, rss_url)
            for entry in reversed(feed.entries[:5]):
                item_id = entry.link
                cursor.execute("SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,))
                if cursor.fetchone() is None:
                    title = html.unescape(entry.title)
                    summary = html.unescape(entry.get("summary", ""))
                    ai_verdict = await analyze_with_gemini(title, summary)
                    
                    cursor.execute("INSERT INTO seen_items (item_id) VALUES (?)", (item_id,))
                    conn.commit()

                    # 1. Отправка в публичный канал (если настроен)
                    if CHANNEL_ID:
                        channel_text = (
                            f"🎁 <b>{title}</b>\n\n"
                            f"📍 <b>Finn.no (Gis bort)</b>\n"
                            f"✨ <i>{ai_verdict}</i>\n\n"
                            f"⚡ <a href='https://t.me/{bot.username}?start=vip'>Получать моментальные пуши в боте</a>"
                        )
                        channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=item_id)]
                        ])
                        try:
                            await bot.send_message(chat_id=CHANNEL_ID, text=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                        except Exception as ce:
                            logging.error(f"Channel post error: {ce}")

                    # 2. Отправка подписчикам бота в личку
                    cursor.execute("SELECT user_id, lang FROM users")
                    users = cursor.fetchall()
                    for uid, lang in users:
                        t = TEXTS.get(lang, TEXTS["no"])
                        user_text = (
                            f"🎁 <b>{title}</b>\n\n"
                            f"{t['ai_title']}\n{ai_verdict}\n"
                        )
                        user_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text=t["btn_finn"], url=item_id)]
                        ])
                        try:
                            await bot.send_message(chat_id=uid, text=user_text, parse_mode="HTML", reply_markup=user_kb)
                        except Exception as ue:
                            logging.error(f"User send error to {uid}: {ue}")

        except Exception as e:
            logging.error(f"Monitor loop error: {e}")

        await asyncio.sleep(25)

async def handle_ping(request):
    return web.Response(text="Finn Sniper is online!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_head("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()

async def main():
    await start_web_server()
    asyncio.create_task(monitor_finn())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
