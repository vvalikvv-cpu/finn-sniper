import os
import asyncio
import logging
import sqlite3
import html
import feedparser
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from google import genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BOT_USERNAME = "finn_sniper_bot"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# База данных
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT, region TEXT DEFAULT 'ostfold', is_vip INTEGER DEFAULT 0)")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS items_feed (
        item_id TEXT PRIMARY KEY,
        title TEXT,
        summary TEXT,
        ai_verdict TEXT,
        cat TEXT,
        link TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

FEEDS = {
    "Gis bort (0 kr)": "https://www.finn.no/bap/forsale/search.rss?price_to=0&trade_type=2&sort=PUBLISHED_DESC",
    "Tech & Apple": "https://www.finn.no/bap/forsale/search.rss?category=0.93&sub_category=1.93.3215&sort=PUBLISHED_DESC",
    "Verktøy": "https://www.finn.no/bap/forsale/search.rss?category=0.67&sub_category=1.67.3911&sort=PUBLISHED_DESC"
}

TEXTS = {
    "ru": {
        "menu": "🎯 <b>Панель управления Finn Sniper</b>\n\nРадар активен 24/7. Нажмите «Finn Radar» внизу для настройки профилей, тегов и городов.",
        "btn_vip": "⭐ Оформить VIP (250 Stars)",
        "btn_templates": "💬 Текст продавцу",
        "btn_finn": "Открыть на Finn.no ↗",
        "invoice_title": "VIP Sniper (30 дней)",
        "invoice_desc": "Моментальные персональные уведомления о находках!",
        "success_pay": "🎉 <b>VIP-подписка активирована на 30 дней!</b>"
    },
    "no": {
        "menu": "🎯 <b>Finn Sniper Kontrollpanel</b>\n\nRadaren er aktiv 24/7. Trykk «Finn Radar» nedenfor for å konfigurere søkeord og byer.",
        "btn_vip": "⭐ Aktiver VIP (250 Stars)",
        "btn_templates": "💬 Melding til selger",
        "btn_finn": "Se annonse på Finn.no ↗",
        "invoice_title": "VIP Sniper (30 dager)",
        "invoice_desc": "Motta lynraske varsler om de beste kuppene!",
        "success_pay": "🎉 <b>VIP er aktivert i 30 dager!</b>"
    }
}

async def analyze_with_gemini(title, desc, cat):
    if not gemini_client:
        return "Gunstig funn registrert på Finn.no."
    try:
        prompt = (
            f"Vurder dette funnet på Finn.no kort (maks 2 linjer):\n"
            f"Kategori: {cat}\nTittel: {title}\nBeskrivelse: {desc}\n"
            f"Er dette attraktivt / god pris?"
        )
        res = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        return res.text.strip()
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return "Attraktivt funn i sanntid."

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("INSERT OR IGNORE INTO users (user_id, lang, region, is_vip) VALUES (?, 'no', 'ostfold', 0)", (user_id,))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ VIP Sniper (250 Stars)", callback_data="buy_vip")]
    ])
    await message.answer(TEXTS["no"]["menu"], parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "buy_vip")
async def handle_buy_vip_callback(callback: types.CallbackQuery):
    prices = [LabeledPrice(label="VIP Sniper (30 dager)", amount=250)]
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="VIP Sniper (30 dager)",
        description="Motta lynraske varsler om de beste kuppene!",
        payload=f"vip_sub_{callback.from_user.id}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET is_vip = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    await message.answer("🎉 <b>VIP-подписка активирована на 30 дней!</b>", parse_mode="HTML")

@dp.message(Command("test"))
async def handle_test(message: types.Message):
    test_title = "Kemppi Minarc 150 Sveiseapparat (Sarpsborg)"
    test_cat = "Verktøy"
    test_ai = "🇳🇴 Legendarisk finsk sveisapparat til superpris i Østfold.\n🇷🇺 Надежный сварочный аппарат."
    test_link = "https://www.finn.no"

    cursor.execute(
        "INSERT OR REPLACE INTO items_feed (item_id, title, summary, ai_verdict, cat, link) VALUES (?, ?, ?, ?, ?, ?)",
        (f"test_{int(asyncio.get_event_loop().time())}", test_title, "Lite brukt sveiseapparat", test_ai, test_cat, test_link)
    )
    conn.commit()

    if CHANNEL_ID:
        try:
            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=test_link)]
            ])
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=(
                    f"🏷️ <b>[{test_cat}]</b>\n📌 <b>{test_title}</b>\n\n"
                    f"✨ <i>{test_ai}</i>\n\n"
                    f"⚡ <a href='https://t.me/{BOT_USERNAME}'>Включить радар в боте</a>"
                ),
                parse_mode="HTML",
                reply_markup=channel_kb
            )
            await message.answer(f"✅ В канал <b>{CHANNEL_ID}</b> тестовое сообщение отправлено!", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки в канал {CHANNEL_ID}: {e}")

async def monitor_finn():
    while True:
        try:
            for cat_name, rss_url in FEEDS.items():
                feed = await asyncio.to_thread(feedparser.parse, rss_url, agent=USER_AGENT)
                
                for entry in reversed(feed.entries[:4]):
                    item_id = entry.link
                    cursor.execute("SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,))
                    if cursor.fetchone() is None:
                        title = html.unescape(entry.title)
                        summary = html.unescape(entry.get("summary", ""))
                        ai_verdict = await analyze_with_gemini(title, summary, cat_name)
                        
                        cursor.execute("INSERT INTO seen_items (item_id) VALUES (?)", (item_id,))
                        cursor.execute(
                            "INSERT OR REPLACE INTO items_feed (item_id, title, summary, ai_verdict, cat, link) VALUES (?, ?, ?, ?, ?, ?)",
                            (item_id, title, summary, ai_verdict, cat_name, item_id)
                        )
                        conn.commit()

                        # 1. Постинг в канал
                        if CHANNEL_ID:
                            channel_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n"
                                f"📌 <b>{title}</b>\n\n"
                                f"✨ <i>{ai_verdict}</i>\n\n"
                                f"⚡ <a href='https://t.me/{BOT_USERNAME}'>Включить радар в боте</a>"
                            )
                            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=item_id)]
                            ])
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                            except Exception as ce:
                                logging.error(f"Channel error: {ce}")

                        # 2. Рассылка пользователям
                        cursor.execute("SELECT user_id FROM users")
                        for (uid,) in cursor.fetchall():
                            user_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n📌 <b>{title}</b>\n\n"
                                f"✨ <b>Gemini AI:</b>\n{ai_verdict}\n"
                            )
                            user_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Se på Finn.no ↗", url=item_id)]
                            ])
                            try:
                                await bot.send_message(chat_id=uid, text=user_text, parse_mode="HTML", reply_markup=user_kb)
                            except Exception as ue:
                                logging.error(f"User error to {uid}: {ue}")

                await asyncio.sleep(4)
        except Exception as e:
            logging.error(f"Monitor error: {e}")

        await asyncio.sleep(20)

async def handle_ping(request):
    return web.Response(text="Finn Sniper is online!")

async def handle_get_items(request):
    cursor.execute("SELECT title, summary, ai_verdict, cat, link FROM items_feed ORDER BY rowid DESC LIMIT 10")
    rows = cursor.fetchall()
    items = []
    for r in rows:
        items.append({
            "title": r[0],
            "summary": r[1],
            "ai_verdict": r[2],
            "cat": r[3],
            "link": r[4]
        })
    return web.json_response({"items": items}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    })

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/api/items", handle_get_items)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    asyncio.create_task(monitor_finn())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
