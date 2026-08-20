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
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT, region TEXT DEFAULT 'all', is_vip INTEGER DEFAULT 0)")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_items (item_id TEXT PRIMARY KEY)")
conn.commit()

# Проверка и добавление колонки region при обновлении старой базы
try:
    cursor.execute("ALTER TABLE users ADD COLUMN region TEXT DEFAULT 'all'")
    conn.commit()
except sqlite3.OperationalError:
    pass

# Список регионов для фильтрации
REGIONS = {
    "all": {"ru": "🇳🇴 Вся Норвегия", "no": "🇳🇴 Hele Norge", "en": "🇳🇴 All Norway", "ua": "🇳🇴 Вся Норвегія", "pl": "🇳🇴 Cała Norwegia"},
    "oslo": {"ru": "🏙️ Осло (Oslo)", "no": "🏙️ Oslo", "en": "🏙️ Oslo", "ua": "🏙️ Осло", "pl": "🏙️ Oslo"},
    "viken": {"ru": "🌲 Викен / Акерсхус", "no": "🌲 Viken / Akershus", "en": "🌲 Viken / Akershus", "ua": "🌲 Вікен / Акерсгус", "pl": "🌲 Viken / Akershus"},
    "vestland": {"ru": "⛰️ Берген (Vestland)", "no": "⛰️ Bergen (Vestland)", "en": "⛰️ Bergen (Vestland)", "ua": "⛰️ Берген", "pl": "⛰️ Bergen"},
    "rogaland": {"ru": "⚓ Ставангер (Rogaland)", "no": "⚓ Stavanger (Rogaland)", "en": "⚓ Stavanger", "ua": "⚓ Ставангер", "pl": "⚓ Stavanger"},
    "trondelag": {"ru": "❄️ Тронхейм (Trøndelag)", "no": "❄️ Trondheim", "en": "❄️ Trondheim", "ua": "❄️ Тронхейм", "pl": "❄️ Trondheim"}
}

# RSS-ленты Finn.no
FEEDS = {
    "Gis bort (0 kr)": "https://www.finn.no/bap/forsale/search.rss?price_to=0&trade_type=2&sort=PUBLISHED_DESC",
    "Tech & Apple": "https://www.finn.no/bap/forsale/search.rss?category=0.93&sub_category=1.93.3215&sort=PUBLISHED_DESC",
    "Verktøy": "https://www.finn.no/bap/forsale/search.rss?category=0.67&sub_category=1.67.3911&sort=PUBLISHED_DESC"
}

TEXTS = {
    "ru": {
        "menu": "🎯 <b>Панель управления Finn Sniper</b>\n\nРадар активен 24/7. Лоты приходят сразу после публикации на Finn.no.",
        "btn_vip": "⭐ Оформить VIP (250 Stars)",
        "btn_lang": "🌐 Сменить язык",
        "btn_region": "📍 Выбрать регион",
        "btn_finn": "Открыть на Finn.no ↗",
        "btn_templates": "💬 Текст продавцу",
        "region_title": "📍 <b>Выберите ваш регион в Норвегии:</b>\n\n(Бот будет присылать находки только из выбранной области)",
        "region_saved": "✅ Регион успешно установлен:",
        "invoice_title": "VIP Sniper (30 дней)",
        "invoice_desc": "Моментальные персональные уведомления о находках!",
        "success_pay": "🎉 <b>VIP-подписка активирована на 30 дней!</b>",
        "templates_header": "📋 <b>Быстрые шаблоны на норвежском (нажмите, чтобы скопировать):</b>\n\n"
                            "🚗 <b>Быстрый самовывоз сегодня:</b>\n"
                            "<code>Hei! Jeg er veldig interessert og kan hente i dag/kveld hvis det passer for deg. Mvh</code>\n\n"
                            "💰 <b>Торг / предложение цены:</b>\n"
                            "<code>Hei! Er varen fortsatt tilgjengelig? Kan hente raskt mot en smidig handel. Mvh</code>\n\n"
                            "📦 <b>Отправка почтой / Fiks ferdig:</b>\n"
                            "<code>Hei! Har du mulighet til å sende denne via Fiks ferdig / Posten? Mvh</code>"
    },
    "no": {
        "menu": "🎯 <b>Finn Sniper Kontrollpanel</b>\n\nRadaren er aktiv 24/7. Nye kupp sendes umiddelbart.",
        "btn_vip": "⭐ Aktiver VIP (250 Stars)",
        "btn_lang": "🌐 Endre språk",
        "btn_region": "📍 Velg region",
        "btn_finn": "Se annonse på Finn.no ↗",
        "btn_templates": "💬 Melding til selger",
        "region_title": "📍 <b>Velg din region i Norge:</b>\n\n(Radaren vil kun varsle om kupp i dette området)",
        "region_saved": "✅ Region er oppdatert:",
        "invoice_title": "VIP Sniper (30 dager)",
        "invoice_desc": "Motta lynraske varsler om de beste kuppene!",
        "success_pay": "🎉 <b>VIP er aktivert i 30 dager!</b>",
        "templates_header": "📋 <b>Hurtigmaler (trykk for å kopiere):</b>\n\n"
                            "🚗 <b>Hente i dag:</b>\n"
                            "<code>Hei! Jeg er veldig interessert og kan hente i dag hvis det passer. Mvh</code>\n\n"
                            "💰 <b>Rask handel:</b>\n"
                            "<code>Hei! Er varen fortsatt tilgjengelig? Kan hente raskt. Mvh</code>\n\n"
                            "📦 <b>Fiks ferdig:</b>\n"
                            "<code>Hei! Har du mulighet til å sende via Fiks ferdig? Mvh</code>"
    },
    "en": {
        "menu": "🎯 <b>Finn Sniper Control Panel</b>\n\nRadar active 24/7. New deals sent instantly.",
        "btn_vip": "⭐ Get VIP (250 Stars)",
        "btn_lang": "🌐 Change Language",
        "btn_region": "📍 Select Region",
        "btn_finn": "Open on Finn.no ↗",
        "btn_templates": "💬 Message Templates",
        "region_title": "📍 <b>Select your region in Norway:</b>",
        "region_saved": "✅ Region set to:",
        "invoice_title": "VIP Sniper (30 days)",
        "invoice_desc": "Fastest deal alerts directly to your PM!",
        "success_pay": "🎉 <b>VIP activated for 30 days!</b>",
        "templates_header": "📋 <b>Quick Norwegian templates (tap to copy):</b>\n\n"
                            "🚗 <b>Pickup today:</b>\n"
                            "<code>Hei! Jeg er veldig interessert og kan hente i dag/kveld hvis det passer for deg. Mvh</code>\n\n"
                            "📦 <b>Shipping:</b>\n"
                            "<code>Hei! Har du mulighet til å sende denne via Fiks ferdig / Posten? Mvh</code>"
    },
    "ua": {
        "menu": "🎯 <b>Панель управління Finn Sniper</b>\n\nРадар активний 24/7. Знахідки надходять миттєво.",
        "btn_vip": "⭐ Оформити VIP (250 Stars)",
        "btn_lang": "🌐 Змінити мову",
        "btn_region": "📍 Обрати регіон",
        "btn_finn": "Відкрити на Finn.no ↗",
        "btn_templates": "💬 Текст для продавця",
        "region_title": "📍 <b>Оберіть ваш регіон у Норвегії:</b>",
        "region_saved": "✅ Регіон оновлено:",
        "invoice_title": "VIP Sniper (30 днів)",
        "invoice_desc": "Миттєві сповіщення про найкращі пропозиції!",
        "success_pay": "🎉 <b>VIP активовано на 30 днів!</b>",
        "templates_header": "📋 <b>Швидкі шаблони норвезькою (натисніть, щоб скопіювати):</b>\n\n"
                            "🚗 <b>Самовивіз сьогодні:</b>\n"
                            "<code>Hei! Jeg er veldig interessert og kan hente i dag/kveld hvis det passer for deg. Mvh</code>\n\n"
                            "📦 <b>Відправка поштою:</b>\n"
                            "<code>Hei! Натисніть для копіювання: Har du mulighet til å sende via Fiks ferdig? Mvh</code>"
    },
    "pl": {
        "menu": "🎯 <b>Panel sterowania Finn Sniper</b>\n\nRadar jest aktywny 24/7.",
        "btn_vip": "⭐ Aktywuj VIP (250 Stars)",
        "btn_lang": "🌐 Zmień język",
        "btn_region": "📍 Wybierz region",
        "btn_finn": "Zobacz na Finn.no ↗",
        "btn_templates": "💬 Szablon wiadomości",
        "region_title": "📍 <b>Wybierz swój region w Norwegii:</b>",
        "region_saved": "✅ Zaktualizowano region:",
        "invoice_title": "VIP Sniper (30 dni)",
        "invoice_desc": "Błyskawiczne powiadomienia o okazjach!",
        "success_pay": "🎉 <b>VIP aktywowany na 30 dni!</b>",
        "templates_header": "📋 <b>Gotowe szablony po norwesku (kliknij, aby skopiować):</b>\n\n"
                            "🚗 <b>Odbiór dzisiaj:</b>\n"
                            "<code>Hei! Jeg er veldig interessert og kan hente i dag hvis det passer. Mvh</code>\n\n"
                            "📦 <b>Wysyłka:</b>\n"
                            "<code>Hei! Har du mulighet til å sende via Fiks ferdig? Mvh</code>"
    }
}

def get_main_keyboard(lang):
    t = TEXTS.get(lang, TEXTS["no"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_vip"], callback_data="buy_vip")],
        [
            InlineKeyboardButton(text=t["btn_region"], callback_data="open_region_menu"),
            InlineKeyboardButton(text=t["btn_lang"], callback_data="open_lang_menu")
        ]
    ])

def get_region_keyboard(lang):
    keyboard = []
    for reg_key, reg_names in REGIONS.items():
        name = reg_names.get(lang, reg_names["no"])
        keyboard.append([InlineKeyboardButton(text=name, callback_data=f"setreg_{reg_key}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def analyze_with_gemini(title, desc, cat):
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
    cursor.execute("INSERT OR IGNORE INTO users (user_id, lang, region, is_vip) VALUES (?, 'no', 'all', 0)", (user_id,))
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

@dp.callback_query(F.data == "open_region_menu")
async def show_regions(callback: types.CallbackQuery):
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (callback.from_user.id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])
    await callback.message.edit_text(t["region_title"], parse_mode="HTML", reply_markup=get_region_keyboard(lang))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("setreg_"))
async def set_region(callback: types.CallbackQuery):
    region_code = callback.data.split("_")[1]
    cursor.execute("UPDATE users SET region = ? WHERE user_id = ?", (region_code, callback.from_user.id))
    conn.commit()
    
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (callback.from_user.id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])
    reg_name = REGIONS.get(region_code, {}).get(lang, region_code)

    await callback.message.answer(f"{t['region_saved']} <b>{reg_name}</b>", parse_mode="HTML")
    await callback.message.edit_text(t["menu"], parse_mode="HTML", reply_markup=get_main_keyboard(lang))
    await callback.answer()

@dp.callback_query(F.data == "show_templates")
async def handle_show_templates(callback: types.CallbackQuery):
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (callback.from_user.id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])
    await callback.message.answer(t["templates_header"], parse_mode="HTML")
    await callback.answer()

@dp.message(Command("test"))
async def handle_test(message: types.Message):
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (message.from_user.id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])

    test_title = "Makita DDF484 Bormaskin / Skrutrekker (Oslo)"
    test_cat = "Verktøy"
    test_ai = "🇳🇴 Meget god profesjonell drill til topp pris.\n🇷🇺 Отличный профессиональный шуруповерт в Осло."
    test_link = "https://www.finn.no"

    item_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_finn"], url=test_link)],
        [InlineKeyboardButton(text=t["btn_templates"], callback_data="show_templates")]
    ])

    await message.answer(
        f"🧪 <b>[ТЕСТОВОЕ ОПОВЕЩЕНИЕ В ЧАТ]</b>\n"
        f"🏷️ <b>[{test_cat}]</b>\n📌 <b>{test_title}</b>\n\n"
        f"✨ <b>Gemini AI:</b>\n{test_ai}",
        parse_mode="HTML",
        reply_markup=item_kb
    )

    if CHANNEL_ID:
        try:
            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=test_link)]
            ])
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=(
                    f"🧪 <b>[ТЕСТ В КАНАЛ]</b>\n"
                    f"🏷️ <b>[{test_cat}]</b>\n📌 <b>{test_title}</b>\n\n"
                    f"✨ <i>{test_ai}</i>\n\n"
                    f"⚡ <a href='https://t.me/{BOT_USERNAME}'>Включить радар в боте</a>"
                ),
                parse_mode="HTML",
                reply_markup=channel_kb
            )
            await message.answer(f"✅ В канал <b>{CHANNEL_ID}</b> тестовое сообщение отправлено!", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Ошибка в канале {CHANNEL_ID}: {e}")

async def send_invoice_logic(chat_id, user_id):
    cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    lang = row[0] if row else "no"
    t = TEXTS.get(lang, TEXTS["no"])

    prices = [LabeledPrice(label="VIP Sniper (30 dager)", amount=250)]
    await bot.send_invoice(
        chat_id=chat_id,
        title=t["invoice_title"],
        description=t["invoice_desc"],
        payload=f"vip_sub_{user_id}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )

@dp.callback_query(F.data == "buy_vip")
async def handle_buy_vip_callback(callback: types.CallbackQuery):
    await send_invoice_logic(callback.message.chat.id, callback.from_user.id)
    await callback.answer()

@dp.message(Command("vip"))
async def handle_buy_vip_command(message: types.Message):
    await send_invoice_logic(message.chat.id, message.from_user.id)

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

def is_item_matching_region(text_to_check: str, user_region: str) -> bool:
    if not user_region or user_region == "all":
        return True
    
    keywords = {
        "oslo": ["oslo"],
        "viken": ["viken", "akershus", "drammen", "lillestrøm", "asker", "bærum", "ski", "moss", "fredrikstad"],
        "vestland": ["vestland", "bergen", "voss", "sogn", "fjordane"],
        "rogaland": ["rogaland", "stavanger", "sandnes", "haugesund"],
        "trondelag": ["trøndelag", "trondelag", "trondheim", "stjørdal", "steinkjer"]
    }
    target_words = keywords.get(user_region, [])
    lowered = text_to_check.lower()
    return any(w in lowered for w in target_words)

async def monitor_finn():
    while True:
        try:
            for cat_name, rss_url in FEEDS.items():
                feed = await asyncio.to_thread(feedparser.parse, rss_url, agent=USER_AGENT)
                
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
                                f"⚡ <a href='https://t.me/{BOT_USERNAME}'>Включить радар в боте</a>"
                            )
                            channel_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Se annonse på Finn.no ↗", url=item_id)]
                            ])
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=channel_text, parse_mode="HTML", reply_markup=channel_kb)
                            except Exception as ce:
                                logging.error(f"Channel send error: {ce}")

                        # 2. Отправка пользователям с учетом выбранного региона
                        combined_item_text = f"{title} {summary}"
                        cursor.execute("SELECT user_id, lang, region FROM users")
                        users = cursor.fetchall()
                        for uid, lang, region in users:
                            if is_item_matching_region(combined_item_text, region):
                                t = TEXTS.get(lang, TEXTS["no"])
                                user_text = (
                                    f"🏷️ <b>[{cat_name}]</b>\n"
                                    f"📌 <b>{title}</b>\n\n"
                                    f"✨ <b>Gemini AI:</b>\n{ai_verdict}\n"
                                )
                                user_kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text=t["btn_finn"], url=item_id)],
                                    [InlineKeyboardButton(text=t["btn_templates"], callback_data="show_templates")]
                                ])
                                try:
                                    await bot.send_message(chat_id=uid, text=user_text, parse_mode="HTML", reply_markup=user_kb)
                                except Exception as ue:
                                    logging.error(f"User send error to {uid}: {ue}")

                await asyncio.sleep(4)
        except Exception as e:
            logging.error(f"Monitor loop error: {e}")

        await asyncio.sleep(20)

async def handle_ping(request):
    return web.Response(text="Finn Sniper is online!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
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
