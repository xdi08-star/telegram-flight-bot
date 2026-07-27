import asyncio
import logging
import os
from datetime import datetime, timedelta, date
from typing import Optional

import aiosqlite
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
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

# ================== КОМАНДЫ ==================
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
    text = "✈️ <b>Твои рейсы:</b>\n\n"
    for f in FLIGHTS:
        flight_date = datetime.strptime(f["date"], "%Y-%m-%d").date()
        days_left = (flight_date - today).days
        text += (
            f"<b>{f['flight_iata']}</b> | {f['date']}\n"
            f"{f['from_city']} → {f['to_city']}\n"
            f"Вылет: {f['dep_time']}\n"
            f"⏳ Осталось дней: <b>{days_left}</b>\n\n"
        )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

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
    await callback.message.edit_text(
        "Напиши текст напоминания.\nНапример: <code>стрижка завтра</code> или <code>позвонить маме 5 августа</code>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(Form.waiting_reminder)
async def save_reminder(message: Message, state: FSMContext):
    text = message.text
    remind_date = (date.today() + timedelta(days=1)).isoformat()  # пока просто на завтра
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO reminders (user_id, text, remind_date) VALUES (?, ?, ?)",
            (message.from_user.id, text, remind_date)
        )
        await db.commit()
    
    await state.clear()
    await message.answer(f"✅ Напоминание сохранено:\n«{text}»", reply_markup=main_menu())

@dp.callback_query(F.data == "list_reminders")
async def list_reminders(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, text, remind_date FROM reminders WHERE user_id = ? AND sent = 0",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "У тебя пока нет активных напоминаний."
    else:
        text = "📋 <b>Твои напоминания:</b>\n\n"
        for r in rows:
            text += f"• {r[1]} (на {r[2]})\n"
    
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
        await db.execute(
            "INSERT INTO todos (user_id, text) VALUES (?, ?)",
            (message.from_user.id, message.text)
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ Задача добавлена!", reply_markup=main_menu())

@dp.callback_query(F.data == "list_todos")
async def list_todos(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, text, done FROM todos WHERE user_id = ? ORDER BY done, id",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "Список дел пуст."
    else:
        text = "✅ <b>Твои задачи:</b>\n\n"
        for r in rows:
            status = "✔️" if r[2] else "⬜"
            text += f"{status} {r[1]}\n"
    
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
            "SELECT text, created FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "Заметок пока нет."
    else:
        text = "📝 <b>Последние заметки:</b>\n\n"
        for r in rows:
            text += f"• {r[0]}\n"
    
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
    await callback.message.edit_text(
        "Напиши в формате:\n<code>День рождения мамы 15.09</code>\nили\n<code>Паспорт до 12.2028</code>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(Form.waiting_date)
async def save_date(message: Message, state: FSMContext):
    # Простое сохранение (можно потом улучшить парсинг)
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
        async with db.execute(
            "SELECT title FROM important_dates WHERE user_id = ?",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "Важных дат пока нет."
    else:
        text = "📅 <b>Важные даты:</b>\n\n"
        for r in rows:
            text += f"• {r[0]}\n"
    
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
    await callback.message.edit_text(
        "Напиши трату в формате:\n<code>1500 продукты</code>\nили\n<code>500 такси</code>",
        parse_mode="HTML"
    )
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
        await message.answer("Не понял формат. Напиши, например:\n<code>1500 продукты</code>", parse_mode="HTML")

@dp.callback_query(F.data == "list_expenses")
async def list_expenses(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT amount, category, created FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 15",
            (callback.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        text = "Трат пока нет."
    else:
        total = sum(r[0] for r in rows)
        text = f"💰 <b>Последние траты:</b>\n\n"
        for r in rows:
            text += f"• {r[0]} ₽ — {r[1]}\n"
        text += f"\n<b>Всего в списке: {total:.0f} ₽</b>"
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_menu())
    await callback.answer()

# ================== ЗАПУСК ==================
async def main():
    await init_db()
    scheduler.start()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
