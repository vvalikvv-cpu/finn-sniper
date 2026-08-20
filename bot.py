import os
import asyncio
import logging
import sqlite3
import html
import feedparser
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    LabeledPrice, 
    PreCheckoutQuery
)
from google import genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# База данных
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, 
        lang TEXT,
        is_vip INTEGER DEFAULT 0,
        vip_until TEXT
    )
""")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
conn.commit()

FEEDS = {
    "Gis bort (0 kr)": "https://www.finn.no/bap/forsale/search.html?price_to=0&trade_type=2&sort=PUBLISHED_DESC",
    "Tech & Apple": "https://www.finn.no/bap/forsale/search.html?category=0.93&sub_category=1.93.3215&sort=PUBLISHED_DESC",
    "Verktøy & Sveising": "https://www.finn.no/bap/forsale/search.html?category=0.67&sub_category=1.67.3911&sort=PUBLISHED_DESC"
}

TEXTS = {
    "ru": {
        "welcome": "🎯 <b>Добро пожаловать в Finn Sniper!</b>\n\nРадар активен. Отслеживаю бесплатные лоты (Gis bort), технику и электроинструмент на Finn.no в реальном времени.",
        "ai_title": "✨ <b>Оценка Gemini AI:</b>",
        "btn_finn": "Открыть на Finn.no ↗",
        "vip_info": "⭐ <b>VIP Статус</b>\n\nVIP-пользователи получают персональные моментальные пуши и готовые шаблоны для связи с продавцом на норвежском.",
        "buy_vip_btn": "⭐ Купить VIP (150 Stars)",
        "vip_success": "🎉 <b>Поздравляем! VIP-подписка активирована на 30 дней.</b>"
    },
    "no": {
        "welcome": "🎯 <b>Velkommen til Finn Sniper!</b>\n\nRadaren er aktiv. Overvåker gratiskupp, tech og proffverktøy på Finn.no i sanntid.",
        "ai_title": "✨ <b>Gemini AI Vurdering:</b>",
        "btn_finn": "Se annonse på Finn.no ↗",
        "vip_info": "⭐ <b>VIP Status</b>\n\nVIP-brukere mottar lynraske varsler (5-15 sek) og ferdiglagde meldinger til selger.",
        "buy_vip_btn": "⭐ Kjøp VIP (150 Stars)",
        "vip_success": "🎉 <b>Gratulerer! VIP-abonnementet er aktivert i 30 dager.</b>"
    }
}

async def analyze_with_gemini(title: str, desc: str, category_name: str) -> str:
    if not gemini_client:
        return "Ingen AI-vurdering tilgjengelig."
    try:
        prompt = (
            f"Du er en ekspert på gjenbruk og kupp i Norge.\n"
            f"Kategori: {category_name}\n"
            f"Tittel: {title}\n"
            f"Beskrivelse: {desc}\n\n"
            f"Gi en superkort vurdering på 2 linjer:\n"
            f"1) 🇳🇴 Norsk: Er dette et godt kjøp/kupp? Hva er anslått verdi/etterspørsel?\n"
            f"2) 🇷🇺 Русский: Выгодно ли это забрать/купить и каков потенциал?"
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
    cursor.execute("""
        INSERT INTO users (user_id, lang) VALUES (?, 'no')
        ON CONFLICT(user_id) DO NOTHING
    """, (user_id,))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇳🇴 Norsk", callback_data="lang_no"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")
        ],
        [
            InlineKeyboardButton(text="⭐ VIP Status / Kjøp", callback_data="vip_menu")
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

# Меню VIP и оплата Stars
@dp.callback_query(lambda c: c.data == "vip_menu")
async def show_vip(callback: types.CallbackQuery):
    cursor.execute("SELECT lang, is_vip FROM users WHERE user_id = ?", (callback.from_user.id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])

    prices = [LabeledPrice(label="VIP 1 Month", amount=150)] # 150 Stars
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Finn Sniper VIP (30 dager)",
        description=t["vip_info"],
        payload="vip_sub_30_days",
        currency="XTR",  # Код Telegram Stars
        prices=prices,
        start_parameter="vip_subscription"
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    vip_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("UPDATE users SET is_vip = 1, vip_until = ? WHERE user_id = ?", (vip_until, user_id))
    conn.commit()
    
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])
    await message.answer(t["vip_success"], parse_mode="HTML")

async def monitor_finn():
    while True:
        try:
            for cat_name, rss_url in FEEDS.items():
                feed = await asyncio.to_thread(feedparser.parse, rss_url)
                for entry in reversed(feed.entries[:3]):
                    item_id = entry.link
                    cursor.execute("SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,))
                    if cursor.fetchone() is None:
                        title = html.unescape(entry.title)
                        summary = html.unescape(entry.get("summary", ""))
                        ai_verdict = await analyze_with_gemini(title, summary, cat_name)
                        
                        cursor.execute("INSERT INTO seen_items (item_id) VALUES (?)", (item_id,))
                        conn.commit()

                        # 1. Публикация в канал
                        if CHANNEL_ID:
                            channel_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n"
                                f"📌 <b>{title}</b>\n\n"
                                f"{ai_verdict}\n\n"
                                f"⚡ <a href='https://t.me/{bot.username}?start=vip'>Получать моментальные пуши в боте</a>"
                            )
                            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=item_id)]
                            ])
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                            except Exception as ce:
                                logging.error(f"Channel post error: {ce}")

                        # 2. Отправка пользователям
                        cursor.execute("SELECT user_id, lang FROM users")
                        users = cursor.fetchall()
                        for uid, lang in users:
                            t = TEXTS.get(lang, TEXTS["no"])
                            user_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n"
                                f"📌 <b>{title}</b>\n\n"
                                f"{t['ai_title']}\n{ai_verdict}\n"
                            )
                            user_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text=t["btn_finn"], url=item_id)]
                            ])
                            try:
                                await bot.send_message(chat_id=uid, text=user_text, parse_mode="HTML", reply_markup=user_kb)
                            except Exception as ue:
                                logging.error(f"User send error to {uid}: {ue}")

                await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Monitor loop error: {e}")

        await asyncio.sleep(20)

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
