import asyncio
import logging
import os
from datetime import datetime, timedelta, date
from typing import Optional

import aiosqlite
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
AVIA_KEY = os.getenv("AVIA_KEY")
DB_NAME = "bot.db"

# Твои рейсы
FLIGHTS = [
    {
        "id": 1,
        "flight_iata": "4S312",
        "date": "2026-08-08",
        "from_airport": "VKO",
        "to_airport": "SSH",
        "from_city": "Москва",
        "to_city": "Шарм-эль-Шейх",
        "dep_time": "00:40",
        "arr_time": "06:30",
        "airline": "Red Sea"
    },
    {
        "id": 2,
        "flight_iata": "4S313",
        "date": "2026-08-15",
        "from_airport": "SSH",
        "to_airport": "VKO",
        "from_city": "Шарм-эль-Шейх",
        "to_city": "Москва",
        "dep_time": "23:59",
        "arr_time": "05:50 +1",
        "airline": "Red Sea"
    }
]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ================== БАЗА ДАННЫХ ==================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                remind_date TEXT,
                remind_time TEXT DEFAULT '09:00',
                sent INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS flight_status (
                flight_id INTEGER,
                last_status TEXT,
                last_check TEXT,
                PRIMARY KEY (flight_id)
            )
        """)
        await db.commit()

async def add_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

# ================== AVIATIONSTACK ==================
async def get_flight_status(flight_iata: str, flight_date: str) -> Optional[dict]:
    url = "http://api.aviationstack.com/v1/flights"
    params = {
        "access_key": AVIA_KEY,
        "flight_iata": flight_iata,
        "flight_date": flight_date
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=15) as resp:
                data = await resp.json()
                if data.get("data") and len(data["data"]) > 0:
                    return data["data"][0]
                return None
    except Exception as e:
        logging.error(f"Ошибка API: {e}")
        return None

def format_status(flight_info: dict, status_data: Optional[dict]) -> str:
    text = (
        f"✈️ <b>{flight_info['flight_iata']}</b> ({flight_info['airline']})\n"
        f"📅 {flight_info['date']}\n"
        f"🛫 {flight_info['from_city']} ({flight_info['from_airport']}) → "
        f"{flight_info['to_city']} ({flight_info['to_airport']})\n"
        f"🕒 Вылет по расписанию: {flight_info['dep_time']}\n"
        f"🕒 Прилёт по расписанию: {flight_info['arr_time']}\n\n"
    )
    
    if not status_data:
        text += "ℹ️ Статус пока недоступен (слишком рано или нет данных)"
        return text

    status = status_data.get("flight_status", "unknown").upper()
    dep = status_data.get("departure", {})
    arr = status_data.get("arrival", {})

    status_emoji = {
        "SCHEDULED": "🟢 По расписанию",
        "ACTIVE": "🔵 В воздухе",
        "LANDED": "✅ Приземлился",
        "CANCELLED": "🔴 ОТМЕНЁН",
        "INCIDENT": "🟠 Инцидент",
        "DIVERTED": "🟡 Направлен в другой аэропорт"
    }.get(status, f"ℹ️ {status}")

    text += f"<b>Текущий статус:</b> {status_emoji}\n"

    if dep.get("delay"):
        text += f"⚠️ Задержка вылета: <b>{dep['delay']} мин</b>\n"
    if arr.get("delay"):
        text += f"⚠️ Задержка прилёта: <b>{arr['delay']} мин</b>\n"

    if dep.get("estimated"):
        text += f"Ожидаемый вылет: {dep['estimated'][:16].replace('T', ' ')}\n"
    if arr.get("estimated"):
        text += f"Ожидаемый прилёт: {arr['estimated'][:16].replace('T', ' ')}\n"

    return text

# ================== КОМАНДЫ ==================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await add_user(message.from_user.id)
    text = (
        "Привет! Я твой бот по рейсам Red Sea ✈️\n\n"
        "Я отслеживаю два рейса:\n"
        "• <b>4S 312</b> — 08.08.2026 Москва → Шарм\n"
        "• <b>4S 313</b> — 15.08.2026 Шарм → Москва\n\n"
        "За 5 дней до вылета я начну каждый день проверять статус "
        "и присылать тебе обновления (отмена, перенос, задержка).\n\n"
        "<b>Команды:</b>\n"
        "/flights — мои рейсы\n"
        "/status — проверить статус сейчас\n"
        "/remind текст — добавить напоминание\n"
        "/reminders — мои напоминания"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("flights"))
async def cmd_flights(message: Message):
    await add_user(message.from_user.id)
    text = "✈️ <b>Твои рейсы:</b>\n\n"
    for f in FLIGHTS:
        text += (
            f"<b>{f['flight_iata']}</b> | {f['date']}\n"
            f"{f['from_city']} → {f['to_city']}\n"
            f"Вылет: {f['dep_time']} | Прилёт: {f['arr_time']}\n\n"
        )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    await add_user(message.from_user.id)
    await message.answer("Проверяю статус рейсов... ⏳")
    
    for f in FLIGHTS:
        status_data = await get_flight_status(f["flight_iata"], f["date"])
        text = format_status(f, status_data)
        await message.answer(text, parse_mode="HTML")
        await asyncio.sleep(1)

@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    await add_user(message.from_user.id)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Напиши так:\n/remind стрижка во вторник\nили\n/remind встреча 5 августа")
        return
    
    text = args[1]
    # Пока сохраняем на завтра утром (можно потом улучшить парсинг)
    remind_date = (date.today() + timedelta(days=1)).isoformat()
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO reminders (user_id, text, remind_date) VALUES (?, ?, ?)",
            (message.from_user.id, text, remind_date)
        )
        await db.commit()
    
    await message.answer(f"✅ Напоминание сохранено:\n«{text}»\nЯ напомню завтра утром.")

@dp.message(Command("reminders"))
async def cmd_reminders(message: Message):
    await add_user(message.from_user.id)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, text, remind_date FROM reminders WHERE user_id = ? AND sent = 0",
            (message.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        await message.answer("У тебя пока нет активных напоминаний.")
        return
    
    text = "📝 <b>Твои напоминания:</b>\n\n"
    for r in rows:
        text += f"{r[0]}. {r[1]} (на {r[2]})\n"
    await message.answer(text, parse_mode="HTML")

# ================== ПРОВЕРКА РЕЙСОВ ==================
async def check_flights_job():
    today = date.today()
    users = await get_all_users()
    if not users:
        return

    for f in FLIGHTS:
        flight_date = datetime.strptime(f["date"], "%Y-%m-%d").date()
        days_left = (flight_date - today).days

        # Начинаем отслеживать за 5 дней
        if 0 <= days_left <= 5:
            status_data = await get_flight_status(f["flight_iata"], f["date"])
            
            # Получаем предыдущий статус
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute(
                    "SELECT last_status FROM flight_status WHERE flight_id = ?", (f["id"],)
                ) as cursor:
                    row = await cursor.fetchone()
                    old_status = row[0] if row else None

            new_status = status_data.get("flight_status") if status_data else "no_data"

            # Если статус изменился — уведомляем
            if new_status != old_status and new_status != "no_data":
                text = f"🔔 <b>Изменение по рейсу {f['flight_iata']}!</b>\n\n"
                text += format_status(f, status_data)
                
                for user_id in users:
                    try:
                        await bot.send_message(user_id, text, parse_mode="HTML")
                    except Exception:
                        pass

            # Сохраняем новый статус
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO flight_status (flight_id, last_status, last_check) VALUES (?, ?, ?)",
                    (f["id"], new_status, datetime.now().isoformat())
                )
                await db.commit()

            # Ежедневное утреннее напоминание за 1-2 дня
            if days_left in [1, 2]:
                text = f"☀️ Доброе утро! Напоминаю про рейс через {days_left} дн.\n\n"
                text += format_status(f, status_data)
                for user_id in users:
                    try:
                        await bot.send_message(user_id, text, parse_mode="HTML")
                    except Exception:
                        pass

async def check_reminders_job():
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, text FROM reminders WHERE remind_date = ? AND sent = 0",
            (today,)
        ) as cursor:
            rows = await cursor.fetchall()

        for r in rows:
            try:
                await bot.send_message(r[1], f"🔔 Напоминание:\n{r[2]}")
                await db.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (r[0],))
            except Exception:
                pass
        await db.commit()

# ================== ЗАПУСК ==================
async def main():
    await init_db()
    
    # Проверяем рейсы каждый день в 08:00 и 20:00
    scheduler.add_job(check_flights_job, "cron", hour=8, minute=0)
    scheduler.add_job(check_flights_job, "cron", hour=20, minute=0)
    
    # Напоминания каждый день в 09:00
    scheduler.add_job(check_reminders_job, "cron", hour=9, minute=0)
    
    scheduler.start()
    
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
