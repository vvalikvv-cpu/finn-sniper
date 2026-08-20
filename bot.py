import os
import json
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

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# База данных
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT, is_vip INTEGER DEFAULT 0)")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
conn.commit()

# Категории Finn.no
FEEDS = {
    "Gis bort (0 kr)": "https://www.finn.no/bap/forsale/search.html?price_to=0&trade_type=2&sort=PUBLISHED_DESC",
    "Tech & Apple": "https://www.finn.no/bap/forsale/search.html?category=0.93&sub_category=1.93.3215&sort=PUBLISHED_DESC",
    "Verktøy": "https://www.finn.no/bap/forsale/search.html?category=0.67&sub_category=1.67.3911&sort=PUBLISHED_DESC"
}

TEXTS = {
    "no": {
        "welcome": "🎯 <b>Velkommen til Finn Sniper!</b>\n\nRadaren er aktiv. Overvåker gratiskupp, tech og verktøy på Finn.no i sanntid.",
        "btn_finn": "Se annonse på Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 dager)",
        "invoice_desc": "Motta lynraske varsler 5–15 sekunder før alle andre!",
        "success_pay": "🎉 <b>Gratulerer! VIP er aktivert i 30 dager.</b>\nDu vil nå motta de raskeste varslene!"
    },
    "ru": {
        "welcome": "🎯 <b>Добро пожаловать в Finn Sniper!</b>\n\nРадар активен. Отслеживаю даром (0 kr), технику и электроинструмент на Finn.no в реальном времени.",
        "btn_finn": "Открыть на Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 дней)",
        "invoice_desc": "Моментальные пуши находок на 5–15 секунд быстрее общего канала!",
        "success_pay": "🎉 <b>Поздравляем! VIP-подписка активирована на 30 дней.</b>\nВы будете первыми получать самые горячие лоты!"
    },
    "en": {
        "welcome": "🎯 <b>Welcome to Finn Sniper!</b>\n\nRadar active. Monitoring free items, tech and tools on Finn.no in real time.",
        "btn_finn": "Open on Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 days)",
        "invoice_desc": "Instant notifications 5–15s ahead of everyone else!",
        "success_pay": "🎉 <b>Success! VIP activated for 30 days.</b>\nYou now receive fastest alerts!"
    },
    "ua": {
        "welcome": "🎯 <b>Ласкаво просимо до Finn Sniper!</b>\n\nРадар активний. Відстежую безкоштовні лоти, техніку та інструменти на Finn.no.",
        "btn_finn": "Відкрити на Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 днів)",
        "invoice_desc": "Миттєві сповіщення на 5–15 сек швидше за всіх!",
        "success_pay": "🎉 <b>Вітаємо! VIP активовано на 30 днів.</b>\nВи отримуватимете найгарячіші знахідки першими!"
    },
    "pl": {
        "welcome": "🎯 <b>Witamy w Finn Sniper!</b>\n\nRadar jest aktywny. Monitorujemy darmowe przedmioty, sprzęt i narzędzia na Finn.no.",
        "btn_finn": "Zobacz na Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 dni)",
        "invoice_desc": "Błyskawiczne powiadomienia 5–15 sekund przed innymi!",
        "success_pay": "🎉 <b>Gratulacje! VIP aktywowany na 30 dni.</b>\nOtrzymujesz najszybsze alerty!"
    }
}

async def analyze_with_gemini(title: str, desc: str, cat: str) -> str:
    if not gemini_client:
        return "AI analyse utilgjengelig."
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
        return "Attraktivt funn registrert."

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("INSERT OR IGNORE INTO users (user_id, lang, is_vip) VALUES (?, 'no', 0)", (user_id,))
    conn.commit()

    # Если пользователь нажал кнопку VIP в Mini App
    if "buy_vip" in (message.text or ""):
        cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        lang = row[0] if row else "no"
        t = TEXTS.get(lang, TEXTS["no"])
        
        prices = [LabeledPrice(label="VIP Sniper (30 dager)", amount=250)]  # 250 Stars
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=t["invoice_title"],
            description=t["invoice_desc"],
            payload=f"vip_sub_{user_id}",
            currency="XTR",
            prices=prices,
            provider_token=""
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇳🇴 Norsk", callback_data="lang_no"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ],
        [
            InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_ua"),
            InlineKeyboardButton(text="🇵🇱 Polski", callback_data="lang_pl")
        ],
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")
        ]
    ])
    await message.answer("Velg språk / Choose language / Выберите язык:", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, callback.from_user.id))
    conn.commit()
    t = TEXTS.get(lang, TEXTS["no"])
    await callback.message.edit_text(t["welcome"], parse_mode="HTML")
    await callback.answer()

# Обработка нажатия «Купить VIP» из Mini App
@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    data = json.loads(message.web_app_data.data)
    if data.get("action") == "buy_vip":
        lang = data.get("lang", "no")
        t = TEXTS.get(lang, TEXTS["no"])
        
        # Выставление счета в Telegram Stars (XTR)
        prices = [LabeledPrice(label="VIP Sniper (1 mnd)", amount=250)] # 250 Stars
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=t["invoice_title"],
            description=t["invoice_desc"],
            payload=f"vip_sub_{message.from_user.id}",
            currency="XTR",
            prices=prices,
            provider_token="" # Для Stars provider_token всегда пустой
        )

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET is_vip = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])
    await message.answer(t["success_pay"], parse_mode="HTML")

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

                        # 1. Постинг в открытый канал
                        if CHANNEL_ID:
                            channel_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n"
                                f"📌 <b>{title}</b>\n\n"
                                f"✨ <i>{ai_verdict}</i>\n\n"
                                f"⚡ <a href='https://t.me/{bot.username}'>Включить мгновенный радар в боте</a>"
                            )
                            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=item_id)]
                            ])
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                            except Exception as ce:
                                logging.error(f"Channel send error: {ce}")

                        # 2. Моментальная рассылка пользователям
                        cursor.execute("SELECT user_id, lang FROM users")
                        users = cursor.fetchall()
                        for uid, lang in users:
                            t = TEXTS.get(lang, TEXTS["no"])
                            user_text = (
                                f"🏷️ <b>[{cat_name}]</b>\n"
                                f"📌 <b>{title}</b>\n\n"
                                f"✨ <b>Gemini AI:</b>\n{ai_verdict}\n"
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
