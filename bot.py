import asyncio
import html
import logging
import os
import re
import sqlite3
import feedparser
import httpx
from bs4 import BeautifulSoup
from google import genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Подключение к локальной базе данных
db = sqlite3.connect("sniper.db", check_same_thread=False)
cursor = db.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS seen_ads (ad_id TEXT PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'ru')")
db.commit()

# Тексты интерфейса
TRANSLATIONS = {
    "ru": {
        "welcome": "👋 Привет! Я **Finn Sniper** 🎯\nЯ мониторю новые лоты на Finn.no и мгновенно присылаю выгодные предложения.\n\nВыберите язык интерфейса:",
        "lang_set": "✅ Язык установлен на Русский!",
        "open_btn": "🔗 Открыть на Finn.no",
        "quick_msg": "Hei! Jeg er veldig interessert og kan hente den i dag. Betaler gjerne med Vipps/kontant. Mvh!"
    },
    "no": {
        "welcome": "👋 Hei! Jeg er **Finn Sniper** 🎯\nJeg overvåker nye kupp på Finn.no og sender dem til deg umiddelbart.\n\nVelg språk:",
        "lang_set": "✅ Språk er satt til Norsk!",
        "open_btn": "🔗 Åpne på Finn.no",
        "quick_msg": "Hei! Jeg er veldig interessert og kan hente den i dag. Betaler gjerne med Vipps/kontant. Mvh!"
    },
    "en": {
        "welcome": "👋 Hello! I am **Finn Sniper** 🎯\nI track fresh deals on Finn.no in real time.\n\nSelect your language:",
        "lang_set": "✅ Language set to English!",
        "open_btn": "🔗 Open on Finn.no",
        "quick_msg": "Hei! Jeg er veldig interessert og kan hente den i dag. Betaler gjerne med Vipps/kontant. Mvh!"
    },
    "ua": {
        "welcome": "👋 Привіт! Я **Finn Sniper** 🎯\nЯ моніторю свіжі знахідки на Finn.no у реальному часі.\n\nОберіть мову інтерфейсу:",
        "lang_set": "✅ Мову встановлено на Українську!",
        "open_btn": "🔗 Відкрити на Finn.no",
        "quick_msg": "Hei! Jeg er veldig interessert og kan hente den i dag. Betaler gjerne med Vipps/kontant. Mvh!"
    },
    "pl": {
        "welcome": "👋 Cześć! Jestem **Finn Sniper** 🎯\nMonitoruję najnowsze okazje na Finn.no w czasie rzeczywistym.\n\nWybierz język:",
        "lang_set": "✅ Język ustawiony na Polski!",
        "open_btn": "🔗 Otwórz na Finn.no",
        "quick_msg": "Hei! Jeg er veldig interessert og kan hente den i dag. Betaler gjerne med Vipps/kontant. Mvh!"
    }
}

def get_language_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🇳🇴 Norsk", callback_data="lang_no")
    builder.button(text="🇬🇧 English", callback_data="lang_en")
    builder.button(text="🇺🇦 Українська", callback_data="lang_ua")
    builder.button(text="🇵🇱 Polski", callback_data="lang_pl")
    builder.button(text="🇷🇺 Русский", callback_data="lang_ru")
    builder.adjust(2, 2, 1)
    return builder.as_markup()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("INSERT OR IGNORE INTO users (user_id, lang) VALUES (?, 'ru')", (user_id,))
    db.commit()
    await message.answer(TRANSLATIONS["ru"]["welcome"], parse_mode="Markdown", reply_markup=get_language_kb())

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(callback: types.CallbackQuery):
    lang_code = callback.data.split("_")[1]
    cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang_code, callback.from_user.id))
    db.commit()
    t = TRANSLATIONS.get(lang_code, TRANSLATIONS["ru"])
    await callback.message.edit_text(t["lang_set"])
    await callback.answer()

async def analyze_with_gemini(title: str, description: str, lang: str):
    prompt = f"""
    You are an expert second-hand deal analyzer in Norway.
    Analyze this Finn.no listing:
    Title: {title}
    Description: {description}

    Tasks:
    1. Is this listing complete junk / broken / unrepairable? (Answer YES or NO).
    2. Write a 1-sentence evaluation and highlight key pros/cons in target language ({lang}).
    3. Estimate typical second-hand market value in NOK (e.g. '~1 500 kr' or '0 kr').

    Format output exactly as:
    JUNK: <YES or NO>
    PRICE_EST: <estimated value in NOK>
    VERDICT: <your 1-sentence summary>
    """
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return None

async def poll_finn():
    # RSS-лента бесплатных товаров (Gis bort)
    rss_url = "https://www.finn.no/bap/forsale/search.rss?trade_type=2"
    
    while True:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries:
                ad_id = entry.link.split("finnkode=")[-1] if "finnkode=" in entry.link else entry.id
                
                cursor.execute("SELECT 1 FROM seen_ads WHERE ad_id = ?", (ad_id,))
                if cursor.fetchone():
                    continue

                cursor.execute("INSERT INTO seen_ads (ad_id) VALUES (?)", (ad_id,))
                db.commit()

                clean_desc = BeautifulSoup(entry.summary, "html.parser").get_text() if "summary" in entry else ""
                
                # Получаем всех пользователей для отправки
                cursor.execute("SELECT user_id, lang FROM users")
                all_users = cursor.fetchall()
                
                for user_id, lang in all_users:
                    t = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
                    ai_result = await analyze_with_gemini(entry.title, clean_desc, lang)
                    
                    if ai_result and "JUNK: YES" in ai_result:
                        continue # Пропускаем явный хлам
                    
                    verdict_match = re.search(r"VERDICT:\s*(.*)", ai_result or "")
                    verdict = verdict_match.group(1) if verdict_match else "Лот прошел базовую проверку."
                    
                    text = (
                        f"🎁 <b>Новая находка / Nytt funn</b>\n\n"
                        f"🏷️ <b>{html.escape(entry.title)}</b>\n"
                        f"💰 <b>Цена:</b> 0 kr (Gis bort)\n\n"
                        f"🤖 <b>AI-Вердикт:</b> {html.escape(verdict)}\n\n"
                        f"💬 <i>Быстрый ответ продавцу:</i>\n<code>{t['quick_msg']}</code>"
                    )

                    builder = InlineKeyboardBuilder()
                    builder.button(text=t["open_btn"], url=entry.link)
                    
                    try:
                        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=builder.as_markup())
                    except Exception as err:
                        logging.error(f"Send error: {err}")

        except Exception as e:
            logging.error(f"Polling loop error: {e}")

        await asyncio.sleep(20) # Опрос каждые 20 секунд

async def main():
    asyncio.create_task(poll_finn())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
