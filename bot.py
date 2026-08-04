import asyncio
import logging
import os
from datetime import datetime, timedelta, date
from typing import Optional

import aiosqlite
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AVIA_KEY = os.getenv("AVIA_KEY")
DB_NAME = "bot.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

# ================== РЕЙСЫ ==================
FLIGHTS = [
    {
        "id": 1,
        "flight_iata": "4S312",
        "date": "2026-08-08",
        "from_city": "Москва",
        "to_city": "Шарм-эль-Шейх",
        "dep_time": "00:40",
        "arr_time": "06:30",
    },
    {
        "id": 2,
        "flight_iata": "4S313",
        "date": "2026-08-15",
        "from_city": "Шарм-эль-Шейх",
        "to_city": "Москва",
        "dep_time": "23:59",
        "arr_time": "05:50 +1",
    }
]

# ================== СОСТОЯНИЯ ==================
class Form(StatesGroup):
    waiting_reminder = State()
    waiting_todo = State()
    waiting_note = State()
    waiting_date = State()
    waiting_expense = State()

# ================== КЛАВИАТУРЫ ==================
def main_menu():
    kb = [
        [InlineKeyboardButton(text="✈️ Мои рейсы", callback_data="flights")],
        [InlineKeyboardButton(text="🔔 Напоминания", callback_data="reminders")],
        [InlineKeyboardButton(text="✅ Список дел", callback_data="todos")],
        [InlineKeyboardButton(text="📝 Заметки", callback_data="notes")],
        [InlineKeyboardButton(text="📅 Важные даты", callback_data="dates")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="finance")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])

def flights_menu():
    kb = []
    for f in FLIGHTS:
        kb.append([InlineKeyboardButton(
            text=f"✈️ {f['flight_iata']} | {f['date']}",
            callback_data=f"flight_{f['id']}"
        )])
    kb.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def flight_detail_menu(flight_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить сейчас", callback_data=f"check_{flight_id}")],
        [InlineKeyboardButton(text="⬅️ К списку рейсов", callback_data="flights")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu")]
    ])

# ================== БАЗА ДАННЫХ ==================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                remind_date TEXT,
                sent INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                done INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                created TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS important_dates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                date TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                category TEXT,
                created TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS flight_status (
                flight_id INTEGER PRIMARY KEY,
                last_status TEXT,
                last_check TEXT
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
            async with session.get(url, params=params, timeout=20) as resp:
                data = await resp.json()
                if data.get("data") and len(data["data"]) > 0:
                    return data["data"][0]
                return None
    except Exception as e:
        logging.error(f"Ошибка API: {e}")
        return None

def format_status(flight_info: dict, status_data: Optional[dict]) -> str:
    today = date.today()
    flight_date = datetime.strptime(flight_info["date"], "%Y-%m-%d").date()
    days_left = (flight_date - today).days

    text = (
        f"✈️ <b>{flight_info['flight_iata']}</b>\n"
        f"📅 {flight_info['date']}\n"
        f"🛫 {flight_info['from_city']} → {flight_info['to_city']}\n"
        f"🕒 Вылет: {flight_info['dep_time']}\n"
        f"🕒 Прилёт: {flight_info['arr_time']}\n"
        f"⏳ Осталось дней: <b>{days_left}</b>\n\n"
    )
    
    if not status_data:
        text += "ℹ️ Статус пока недоступен (слишком рано или нет данных в системе)"
        return text

    status = status_data.get("flight_status", "unknown").upper()
    dep = status_data.get("departure", {}) or {}
    arr = status_data.get("arrival", {}) or {}

    status_map = {
        "SCHEDULED": "🟢 По расписанию",
        "ACTIVE": "🔵 В воздухе",
        "LANDED": "✅ Приземлился",
        "CANCELLED": "🔴 ОТМЕНЁН",
        "INCIDENT": "🟠 Инцидент",
        "DIVERTED": "🟡 Направлен в другой аэропорт"
    }
    text += f"<b>Текущий статус:</b> {status_map.get(status, status)}\n"

    if dep.get("delay"):
        text += f"⚠️ Задержка вылета: <b>{dep['delay']} мин</b>\n"
    if arr.get("delay"):
        text += f"⚠️ Задержка прилёта: <b>{arr['delay']} мин</b>\n"

    if dep.get("estimated"):
        text += f"Ожидаемый вылет: {str(dep['estimated'])[:16].replace('T', ' ')}\n"
    if arr.get("estimated"):
        text += f"Ожидаемый прилёт: {str(arr['estimated'])[:16].replace('T', ' ')}\n"

    return text

# ================== ЛОГИКА АВТОМАТИЧЕСКИХ ПРОВЕРОК ==================
def should_check_now(flight: dict) -> bool:
    today = date.today()
    flight_date = datetime.strptime(flight["date"], "%Y-%m-%d").date()
    days_left = (flight_date - today).days

    if days_left > 5 or days_left < 0:
        return False

    now = datetime.now()
    current_hour = now.hour

    # За 2–5 дней: 4 раза в день
    if days_left >= 2:
        return current_hour in [8, 12, 16, 20]

    # За 1 день и в день вылета: каждый час
    if days_left <= 1:
        return True

    return False

async def check_flights_job():
    users = await get_all_users()
    if not users:
        return

    for f in FLIGHTS:
        if not should_check_now(f):
            continue

        status_data = await get_flight_status(f["flight_iata"], f["date"])
        new_status = status_data.get("flight_status") if status_data else "no_data"

        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT last_status FROM flight_status WHERE flight_id = ?", (f["id"],)
            ) as cursor:
                row = await cursor.fetchone()
                old_status = row[0] if row else None

        # Если статус изменился — уведомляем
        if new_status != old_status and new_status not in (None, "no_data"):
            text = f"🔔 <b>Изменение по рейсу {f['flight_iata']}!</b>\n\n"
            text += format_status(f, status_data)
            for user_id in users:
                try:
                    await bot.send_message(user_id, text, parse_mode="HTML")
                except Exception:
                    pass

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR REPLACE INTO flight_status (flight_id, last_status, last_check) VALUES (?, ?, ?)",
                (f["id"], new_status, datetime.now().isoformat())
            )
            await db.commit()

# ================== КОМАНДЫ И МЕНЮ ==================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await add_user(message.from_user.id)
    await message.answer(
        "Привет! Я твой личный помощник ✈️\n\nВыбери, что нужно:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Главное меню:\nВыбери нужный раздел:",
        reply_markup=main_menu()
    )
    await callback.answer()

# ================== РЕЙСЫ ==================
@dp.callback_query(F.data == "flights")
async def show_flights(callback: CallbackQuery):
    today = date.today()
    text = "✈️ <b>Твои рейсы:</b>\n\nВыбери рейс, чтобы посмотреть детали или проверить статус:"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=flights_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("flight_"))
async def show_flight_detail(callback: CallbackQuery):
    flight_id = int(callback.data.split("_")[1])
    f = next((x for x in FLIGHTS if x["id"] == flight_id), None)
    if not f:
        await callback.answer("Рейс не найден")
        return

    today = date.today()
    flight_date = datetime.strptime(f["date"], "%Y-%m-%d").date()
    days_left = (flight_date - today).days

    text = (
        f"✈️ <b>{f['flight_iata']}</b>\n"
        f"📅 {f['date']}\n"
        f"🛫 {f['from_city']} → {f['to_city']}\n"
        f"🕒 Вылет: {f['dep_time']}\n"
        f"🕒 Прилёт: {f['arr_time']}\n"
        f"⏳ Осталось дней: <b>{days_left}</b>\n\n"
        f"Нажми «Проверить сейчас», чтобы узнать актуальный статус."
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=flight_detail_menu(flight_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("check_"))
async def manual_check(callback: CallbackQuery):
    flight_id = int(callback.data.split("_")[1])
    f = next((x for x in FLIGHTS if x["id"] == flight_id), None)
    if not f:
        await callback.answer("Рейс не найден")
        return

    await callback.answer("Проверяю статус...")
    status_data = await get_flight_status(f["flight_iata"], f["date"])
    text = format_status(f, status_data)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=flight_detail_menu(flight_id))

# ================== НАПОМИНАНИЯ ==================
@dp.callback_query(F.data == "reminders")
async def reminders_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить напоминание", callback_data="add_reminder")],
        [InlineKeyboardButton(text="📋 Мои напоминания", callback_data="list_reminders")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text("🔔 Раздел напоминаний:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "add_reminder")
async def add_reminder_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_reminder)
    await callback.message.edit_text("Напиши текст напоминания:")
    await callback.answer()

@dp.message(Form.waiting_reminder)
async def save_reminder(message: Message, state: FSMContext):
    remind_date = (date.today() + timedelta(days=1)).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO reminders (user_id, text, remind_date) VALUES (?, ?, ?)",
            (message.from_user.id, message.text, remind_date)
        )
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Напоминание сохранено:\n«{message.text}»", reply_markup=main_menu())

@dp.callback_query(F.data == "list_reminders")
async def list_reminders(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text, remind_date FROM reminders WHERE user_id = ? AND sent = 0",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    text = "У тебя пока нет активных напоминаний." if not rows else "📋 <b>Твои напоминания:</b>\n\n" + "\n".join(f"• {r[0]} (на {r[1]})" for r in rows)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

# ================== СПИСОК ДЕЛ ==================
@dp.callback_query(F.data == "todos")
async def todos_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить задачу", callback_data="add_todo")],
        [InlineKeyboardButton(text="📋 Мои задачи", callback_data="list_todos")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text("✅ Список дел:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "add_todo")
async def add_todo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_todo)
    await callback.message.edit_text("Напиши задачу:")
    await callback.answer()

@dp.message(Form.waiting_todo)
async def save_todo(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO todos (user_id, text) VALUES (?, ?)", (message.from_user.id, message.text))
        await db.commit()
    await state.clear()
    await message.answer("✅ Задача добавлена!", reply_markup=main_menu())

@dp.callback_query(F.data == "list_todos")
async def list_todos(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT text, done FROM todos WHERE user_id = ? ORDER BY done, id", (callback.from_user.id,)) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "Список дел пуст."
    else:
        text = "✅ <b>Твои задачи:</b>\n\n" + "\n".join(f"{'✔️' if r[1] else '⬜'} {r[0]}" for r in rows)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

# ================== ЗАМЕТКИ ==================
@dp.callback_query(F.data == "notes")
async def notes_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новая заметка", callback_data="add_note")],
        [InlineKeyboardButton(text="📋 Мои заметки", callback_data="list_notes")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text("📝 Заметки:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "add_note")
async def add_note_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_note)
    await callback.message.edit_text("Напиши заметку:")
    await callback.answer()

@dp.message(Form.waiting_note)
async def save_note(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO notes (user_id, text, created) VALUES (?, ?, ?)",
            (message.from_user.id, message.text, datetime.now().isoformat())
        )
        await db.commit()
    await state.clear()
    await message.answer("📝 Заметка сохранена!", reply_markup=main_menu())

@dp.callback_query(F.data == "list_notes")
async def list_notes(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    text = "Заметок пока нет." if not rows else "📝 <b>Последние заметки:</b>\n\n" + "\n".join(f"• {r[0]}" for r in rows)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

# ================== ВАЖНЫЕ ДАТЫ ==================
@dp.callback_query(F.data == "dates")
async def dates_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить дату", callback_data="add_date")],
        [InlineKeyboardButton(text="📋 Мои даты", callback_data="list_dates")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text("📅 Важные даты:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "add_date")
async def add_date_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_date)
    await callback.message.edit_text("Напиши важную дату (например: День рождения мамы 15.09):")
    await callback.answer()

@dp.message(Form.waiting_date)
async def save_date(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO important_dates (user_id, title, date) VALUES (?, ?, ?)",
            (message.from_user.id, message.text, date.today().isoformat())
        )
        await db.commit()
    await state.clear()
    await message.answer("📅 Дата сохранена!", reply_markup=main_menu())

@dp.callback_query(F.data == "list_dates")
async def list_dates(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT title FROM important_dates WHERE user_id = ?", (callback.from_user.id,)) as cursor:
            rows = await cursor.fetchall()
    
    text = "Важных дат пока нет." if not rows else "📅 <b>Важные даты:</b>\n\n" + "\n".join(f"• {r[0]}" for r in rows)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

# ================== ФИНАНСЫ ==================
@dp.callback_query(F.data == "finance")
async def finance_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Записать трату", callback_data="add_expense")],
        [InlineKeyboardButton(text="📊 Мои траты", callback_data="list_expenses")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text("💰 Финансы:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "add_expense")
async def add_expense_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_expense)
    await callback.message.edit_text("Напиши трату (например: 1500 продукты):")
    await callback.answer()

@dp.message(Form.waiting_expense)
async def save_expense(message: Message, state: FSMContext):
    try:
        parts = message.text.split(maxsplit=1)
        amount = float(parts[0].replace(",", "."))
        category = parts[1] if len(parts) > 1 else "другое"
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO expenses (user_id, amount, category, created) VALUES (?, ?, ?, ?)",
                (message.from_user.id, amount, category, datetime.now().isoformat())
            )
            await db.commit()
        await state.clear()
        await message.answer(f"✅ Записал: {amount} ₽ — {category}", reply_markup=main_menu())
    except:
        await message.answer("Не понял формат. Напиши, например:\n1500 продукты")

@dp.callback_query(F.data == "list_expenses")
async def list_expenses(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT amount, category FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 15",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "Трат пока нет."
    else:
        total = sum(r[0] for r in rows)
        text = "💰 <b>Последние траты:</b>\n\n" + "\n".join(f"• {r[0]} ₽ — {r[1]}" for r in rows)
        text += f"\n\n<b>Всего: {total:.0f} ₽</b>"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

# ================== ЗАПУСК ==================
async def main():
    await init_db()
    
    # Проверка каждый час (логика внутри решает, делать ли запрос)
    scheduler.add_job(check_flights_job, "interval", hours=1)
    
    scheduler.start()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
