import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ (ЗАМЕНИ НА СВОИ) =================
BOT_TOKEN = "8253893194:AAGkVF4oVU5v9kaCdFQsVWgdwrQ5NAeXLNc"  # Токен твоего бота
ADMIN_ID = 7130111876  # Твой числовой Telegram ID (узнай в @userinfobot)
SHOP_URL = "https://t.me/wyxner"  # Ссылка на твой магазин
# =============================================================

logging.basicConfig(level=logging.INFO)

# Инициализируем бота и диспетчер
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Функция для автоматического создания базы данных
def init_db():
    conn = sqlite3.connect("garant.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message_text TEXT,
            status TEXT DEFAULT 'open'
        )
    """)
    conn.commit()
    conn.close()

# Запускаем создание БД при старте бота
init_db()

# Состояния ожидания ввода (для FSM)
class Form(StatesGroup):
    waiting_for_user_message = State()
    waiting_for_admin_reply = State()

# Клавиатура главного меню
def main_menu_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🤝 Написать гаранту", callback_data="write_garant"))
    builder.row(types.InlineKeyboardButton(text="🛍️ Магазин", url=SHOP_URL))
    
    # Кнопку просмотра запросов видит ТОЛЬКО админ
    if user_id == ADMIN_ID:
        builder.row(types.InlineKeyboardButton(text="📥 Запросы", callback_data="admin_tickets"))
        
    return builder.as_markup()

# --- ОБРАБОТКА КОМАНД И КНОПОК ---

# Команда /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать! Я бот-помощник гаранта.\nВыберите интересующий вас раздел меню ниже:",
        reply_markup=main_menu_keyboard(message.from_user.id)
    )

# Пользователь нажал "Написать гаранту"
@dp.callback_query(F.data == "write_garant")
async def ask_for_message(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("📝 Напишите снизу сообщение для гаранта (отвечает в течение дня):")
    await state.set_state(Form.waiting_for_user_message)

# Прием сообщения от пользователя
@dp.message(Form.waiting_for_user_message)
async def receive_user_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "Без юзернейма"
    text = message.text

    if not text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение.")
        return

    # Записываем запрос в базу данных SQLite
    conn = sqlite3.connect("garant.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tickets (user_id, username, message_text) VALUES (?, ?, ?)",
        (user_id, username, text)
    )
    conn.commit()
    conn.close()

    await message.answer("✅ Сообщение отправлено, ожидайте ответа.", reply_markup=main_menu_keyboard(user_id))
    
    # Отправляем мгновенное уведомление админу (вам)
    try:
        await bot.send_message(
            ADMIN_ID, 
            f"🔔 **Новый запрос!**\nОт: {username} (ID: {user_id})\nТекст: {text}\n\nЧтобы ответить, нажмите кнопку 'Запросы' в меню."
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление админу: {e}")
        
    await state.clear()

# --- КНОПКИ АДМИНИСТРАТОРА ---

# Просмотр списка активных запросов
@dp.callback_query(F.data == "admin_tickets")
async def admin_tickets_list(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return

    conn = sqlite3.connect("garant.db")
    cursor = conn.cursor()
    # Берем последние 10 открытых запросов
    cursor.execute("SELECT id, username, message_text FROM tickets WHERE status = 'open' LIMIT 10")
    tickets = cursor.fetchall()
    conn.close()

    if not tickets:
        await callback.answer("Нет новых запросов!", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer("📥 **Список активных запросов (последние 10):**")
    
    for ticket_id, username, text in tickets:
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="✍️ Ответить", callback_data=f"reply_{ticket_id}"))
        await callback.message.answer(
            f"🎫 **Запрос #{ticket_id}**\nПользователь: {username}\n\n💬 Текст:\n{text}",
            reply_markup=builder.as_markup()
        )

# Админ нажал кнопку "Ответить" под запросом
@dp.callback_query(F.data.startswith("reply_"))
async def admin_prepare_reply(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[1])
    await callback.answer()
    await callback.message.answer(f"✍️ Введите ответ на запрос #{ticket_id}:")
    await state.update_data(reply_to_ticket=ticket_id)
    await state.set_state(Form.waiting_for_admin_reply)

# Отправка ответа пользователю
@dp.message(Form.waiting_for_admin_reply)
async def admin_send_reply(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    ticket_id = data.get("reply_to_ticket")
    admin_text = message.text

    conn = sqlite3.connect("garant.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM tickets WHERE id = ?", (ticket_id,))
    result = cursor.fetchone()
    
    if result:
        user_id = result[0]
        try:
            # Отправляем ответ пользователю в ЛС от имени бота
            await bot.send_message(user_id, f"💬 **Ответ от гаранта:**\n\n{admin_text}")
            await message.answer(f"✅ Ответ успешно доставлен пользователю (ID: {user_id})!")
            
            # Меняем статус запроса на закрытый
            cursor.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
            conn.commit()
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить (возможно, пользователь заблокировал бота): {e}")
    else:
        await message.answer("❌ Запрос не найден в базе данных.")

    conn.close()
    await state.clear()

# Главная функция запуска
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
