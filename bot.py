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

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# База данных
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT, is_vip INTEGER DEFAULT 0)")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
conn.commit()

# RSS-ленты категорий Finn.no
FEEDS = {
    "Gis bort (0 kr)": "https://www.finn.no/bap/forsale/search.html?price_to=0&trade_type=2&sort=PUBLISHED_DESC",
    "Tech & Apple": "https://www.finn.no/bap/forsale/search.html?category=0.93&sub_category=1.93.3215&sort=PUBLISHED_DESC",
    "Verktøy": "https://www.finn.no/bap/forsale/search.html?category=0.67&sub_category=1.67.3911&sort=PUBLISHED_DESC"
}

TEXTS = {
    "ru": {
        "menu": "🎯 <b>Панель управления Finn Sniper</b>\n\nРадар активен 24/7. Лоты приходят сразу после публикации на Finn.no.",
        "btn_vip": "⭐ Оформить VIP (250 Stars)",
        "btn_lang": "🌐 Сменить язык",
        "btn_finn": "Открыть на Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 дней)",
        "invoice_desc": "Моментальные персональные уведомления о находках!",
        "success_pay": "🎉 <b>VIP-подписка активирована на 30 дней!</b>"
    },
    "no": {
        "menu": "🎯 <b>Finn Sniper Kontrollpanel</b>\n\nRadaren er aktiv 24/7. Nye kupp sendes umiddelbart.",
        "btn_vip": "⭐ Aktiver VIP (250 Stars)",
        "btn_lang": "🌐 Endre språk",
        "btn_finn": "Se annonse på Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 dager)",
        "invoice_desc": "Motta lynraske varsler om de beste kuppene!",
        "success_pay": "🎉 <b>VIP er aktivert i 30 dager!</b>"
    },
    "en": {
        "menu": "🎯 <b>Finn Sniper Control Panel</b>\n\nRadar active 24/7. New deals sent instantly.",
        "btn_vip": "⭐ Get VIP (250 Stars)",
        "btn_lang": "🌐 Change Language",
        "btn_finn": "Open on Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 days)",
        "invoice_desc": "Fastest deal alerts directly to your PM!",
        "success_pay": "🎉 <b>VIP activated for 30 days!</b>"
    },
    "ua": {
        "menu": "🎯 <b>Панель управління Finn Sniper</b>\n\nРадар активний 24/7. Знахідки надходять миттєво.",
        "btn_vip": "⭐ Оформити VIP (250 Stars)",
        "btn_lang": "🌐 Змінити мову",
        "btn_finn": "Відкрити на Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 днів)",
        "invoice_desc": "Миттєві сповіщення про найкращі пропозиції!",
        "success_pay": "🎉 <b>VIP активовано на 30 днів!</b>"
    },
    "pl": {
        "menu": "🎯 <b>Panel sterowania Finn Sniper</b>\n\nRadar jest aktywny 24/7.",
        "btn_vip": "⭐ Aktywuj VIP (250 Stars)",
        "btn_lang": "🌐 Zmień język",
        "btn_finn": "Zobacz na Finn.no ↗",
        "invoice_title": "⭐ VIP Sniper (30 dni)",
        "invoice_desc": "Błyskawiczne powiadomienia o okazjach!",
        "success_pay": "🎉 <b>VIP aktywowany na 30 dni!</b>"
    }
}

def get_main_keyboard(lang: str) -> InlineKeyboardMarkup:
    t = TEXTS.get(lang, TEXTS["no"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_vip"], callback_data="buy_vip")],
        [InlineKeyboardButton(text=t["btn_lang"], callback_data="open_lang_menu")]
    ])

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

    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])

    await message.answer(t["menu"], parse_mode="HTML", reply_markup=get_main_keyboard(lang))

@dp.callback_query(F.data == "open_lang_menu")
async def show_languages(callback: types.CallbackQuery):
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
    await callback.message.edit_text("Velg språk / Choose language / Выберите язык:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, callback.from_user.id))
    conn.commit()
    t = TEXTS.get(lang, TEXTS["no"])
    await callback.message.edit_text(t["menu"], parse_mode="HTML", reply_markup=get_main_keyboard(lang))
    await callback.answer()

# Прямая покупка Telegram Stars по кнопке в чате
@dp.callback_query(F.data == "buy_vip")
@dp.message(Command("vip"))
async def send_stars_invoice(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    chat_id = event.message.chat.id if isinstance(event, types.CallbackQuery) else event.chat.id

    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])

    prices = [LabeledPrice(label="VIP Sniper (30 dager)", amount=250)]  # 250 Stars
    await bot.send_invoice(
        chat_id=chat_id,
        title=t["invoice_title"],
        description=t["invoice_desc"],
        payload=f"vip_sub_{user_id}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )
    if isinstance(event, types.CallbackQuery):
        await event.answer()

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
                                f"⚡ <a href='https://t.me/{bot.username}'>Включить радар в боте</a>"
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
