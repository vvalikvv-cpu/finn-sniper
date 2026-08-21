import os
import asyncio
import logging
import sqlite3
import html
import re
import json
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

cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        lang TEXT DEFAULT 'no',
        is_vip INTEGER DEFAULT 0,
        settings_json TEXT DEFAULT '{}'
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS items_feed (
        item_id TEXT PRIMARY KEY,
        title TEXT,
        summary TEXT,
        ai_verdict TEXT,
        cat TEXT,
        link TEXT,
        image_url TEXT,
        price TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
conn.commit()

FEEDS = {
    "Gis bort (0 kr)": "https://www.finn.no/bap/forsale/search.rss?price_to=0&trade_type=2&sort=PUBLISHED_DESC",
    "Tech & Apple": "https://www.finn.no/bap/forsale/search.rss?category=0.93&sub_category=1.93.3215&sort=PUBLISHED_DESC",
    "Verktøy": "https://www.finn.no/bap/forsale/search.rss?category=0.67&sub_category=1.67.3911&sort=PUBLISHED_DESC"
}

def extract_image(entry):
    if "media_content" in entry and len(entry.media_content) > 0:
        return entry.media_content[0].get("url", "")
    if "enclosures" in entry and len(entry.enclosures) > 0:
        return entry.enclosures[0].get("href", "")
    summary = entry.get("summary", "")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
    if match:
        return match.group(1)
    return ""

async def analyze_with_gemini(title, desc, cat):
    if not gemini_client:
        return "Gunstig kjøpsmulighet registrert på Finn.no."
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
    cursor.execute("INSERT OR IGNORE INTO users (user_id, lang, is_vip, settings_json) VALUES (?, 'no', 0, '{}')", (user_id,))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ VIP Sniper (250 Stars)", callback_data="buy_vip")]
    ])
    await message.answer(
        "🎯 <b>Finn Sniper Studio</b>\n\n"
        "Радар активен 24/7. Откройте <b>«Finn Radar»</b> внизу для тонкой настройки фильтров, тегов и городов.",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.callback_query(F.data == "buy_vip")
async def handle_buy_vip(callback: types.CallbackQuery):
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
async def process_pre_checkout_query(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET is_vip = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    await message.answer("🎉 <b>VIP-подписка активирована на 30 дней!</b>", parse_mode="HTML")

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
                        summary = html.unescape(re.sub('<[^<]+?>', '', entry.get("summary", "")))
                        img_url = extract_image(entry)
                        ai_verdict = await analyze_with_gemini(title, summary, cat_name)
                        
                        cursor.execute("INSERT INTO seen_items (item_id) VALUES (?)", (item_id,))
                        cursor.execute(
                            "INSERT OR REPLACE INTO items_feed (item_id, title, summary, ai_verdict, cat, link, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (item_id, title, summary, ai_verdict, cat_name, item_id, img_url)
                        )
                        conn.commit()

                        # Публикация в канал
                        if CHANNEL_ID:
                            channel_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n"
                                f"📌 <b>{title}</b>\n\n"
                                f"✨ <i>{ai_verdict}</i>\n\n"
                                f"⚡ <a href='https://t.me/{BOT_USERNAME}'>Настроить радар под свой город</a>"
                            )
                            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=item_id)]
                            ])
                            try:
                                if img_url:
                                    await bot.send_photo(chat_id=CHANNEL_ID, photo=img_url, caption=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                                else:
                                    await bot.send_message(chat_id=CHANNEL_ID, text=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                            except Exception as ce:
                                logging.error(f"Channel error: {ce}")

                await asyncio.sleep(4)
        except Exception as e:
            logging.error(f"Monitor error: {e}")

        await asyncio.sleep(20)

# API
async def handle_ping(request):
    return web.Response(text="Finn Sniper is online!")

async def handle_get_feed(request):
    cursor.execute("SELECT title, summary, ai_verdict, cat, link, image_url FROM items_feed ORDER BY rowid DESC LIMIT 15")
    rows = cursor.fetchall()
    items = []
    for r in rows:
        items.append({
            "title": r[0],
            "summary": r[1],
            "ai_verdict": r[2],
            "cat": r[3],
            "link": r[4],
            "image_url": r[5]
        })
    return web.json_response({"items": items}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_get_user_state(request):
    uid = request.query.get("user_id", "")
    cursor.execute("SELECT is_vip, settings_json FROM users WHERE user_id = ?", (uid,))
    row = cursor.fetchone()
    if row:
        return web.json_response({"is_vip": bool(row[0]), "settings": json.loads(row[1] or "{}")}, headers={"Access-Control-Allow-Origin": "*"})
    return web.json_response({"is_vip": False, "settings": {}}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_save_user_state(request):
    data = await request.json()
    uid = data.get("user_id")
    settings = json.dumps(data.get("settings", {}))
    if uid:
        cursor.execute("INSERT INTO users (user_id, settings_json) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET settings_json = ?", (uid, settings, settings))
        conn.commit()
    return web.json_response({"status": "ok"}, headers={"Access-Control-Allow-Origin": "*"})

async def handle_cors_options(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    })

async def start_web_server():
    app = web.Application()
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_cors_options)
    app.router.add_get("/", handle_ping)
    app.router.add_get("/api/feed", handle_get_feed)
    app.router.add_get("/api/user_state", handle_get_user_state)
    app.router.add_post("/api/save_state", handle_save_user_state)
    
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
