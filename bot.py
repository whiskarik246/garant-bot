import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ──────────────────────────────────────────────
#  КОНФИГУРАЦИЯ (С ТВОИМ ТОКЕНОМ)
# ──────────────────────────────────────────────
BOT_TOKEN      = "8984175960:AAGfRXKzHJ_3b79ONz5BXoag0fbc9wSY0ME"
ADMIN_USERNAME = "RAZY_YZAR"
SHOP_URL       = "https://t.me/wyxner"  # Ссылку сохраняем, если понадобится в текстах
DB_PATH        = "garant.db"

# ──────────────────────────────────────────────
#  ЛОГИРОВАНИЕ
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s — %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  БАЗА ДАННЫХ
# ──────────────────────────────────────────────
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    username     TEXT,
                    message_text TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'open'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()
        logger.info("База данных инициализирована успешно.")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise


def db_get_config(key: str) -> str | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None
    except Exception as e:
        logger.error(f"db_get_config({key}): {e}")
        return None


def db_set_config(key: str, value: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"db_set_config({key}={value}): {e}")


def db_create_ticket(user_id: int, username: str | None, message_text: str) -> int:
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO tickets (user_id, username, message_text, status) "
                "VALUES (?, ?, ?, 'open')",
                (user_id, username or "unknown", message_text),
            )
            conn.commit()
            ticket_id = cursor.lastrowid
            logger.info(f"Создан тикет #{ticket_id} от user_id={user_id}")
            return ticket_id
    except Exception as e:
        logger.error(f"db_create_ticket: {e}")
        return -1


def db_get_open_tickets(limit: int = 10) -> list:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE status = 'open' "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"db_get_open_tickets: {e}")
        return []


def db_get_ticket(ticket_id: int) -> dict | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"db_get_ticket({ticket_id}): {e}")
        return None


def db_update_ticket_status(ticket_id: int, status: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE tickets SET status = ? WHERE id = ?",
                (status, ticket_id),
            )
            conn.commit()
            logger.info(f"Тикет #{ticket_id} → статус '{status}'")
    except Exception as e:
        logger.error(f"db_update_ticket_status({ticket_id}, {status}): {e}")


# ──────────────────────────────────────────────
#  FSM — СОСТОЯНИЯ
# ──────────────────────────────────────────────
class UserStates(StatesGroup):
    waiting_for_message = State()


class AdminStates(StatesGroup):
    waiting_for_reply_text = State()


# ──────────────────────────────────────────────
#  КЛАВИАТУРЫ
# ──────────────────────────────────────────────

# Нижняя кнопка под полем ввода
def bottom_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Меню")]
        ],
        resize_keyboard=True
    )


# Главное меню бота (вместо прямой ссылки теперь колбэк "shop_menu")
def main_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🤝 Написать гаранту", callback_data="write_garant")],
        [InlineKeyboardButton(text="🛍️ Магазин", callback_data="shop_menu")],
    ]
    if is_admin:
        buttons.append(
            [InlineKeyboardButton(text="📥 Запросы", callback_data="admin_tickets")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Клавиатура выбора категорий в магазине
def shop_categories_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Виртуальные аккаунты", callback_data="shop_accounts")],
        [InlineKeyboardButton(text="📢 Каналы с подписчиками", callback_data="shop_channels")],
        [InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data="back_to_main")]
    ])


# Универсальная кнопка возврата в меню магазина
def back_to_shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤝 Написать гаранту", callback_data="write_garant")],
        [InlineKeyboardButton(text="🔙 Назад в магазин", callback_data="shop_menu")]
    ])


# Клавиатура управления тикетом (для админа)
def ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✍️ Ответить",
                callback_data=f"reply_{ticket_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject_{ticket_id}",
            ),
        ]
    ])


# ──────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ──────────────────────────────────────────────
def is_admin(username: str | None) -> bool:
    if not username:
        return False
    return username.lstrip("@").lower() == ADMIN_USERNAME.lower()


def get_admin_id() -> int | None:
    val = db_get_config("admin_id")
    return int(val) if val else None


async def save_admin_id_if_needed(user_id: int, username: str | None) -> None:
    if is_admin(username):
        existing = db_get_config("admin_id")
        if not existing:
            db_set_config("admin_id", str(user_id))
            logger.info(f"Admin ID сохранён: {user_id} (@{username})")


# ──────────────────────────────────────────────
#  ИНИЦИАЛИЗАЦИЯ БОТА
# ──────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN, default_properties=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher(storage=MemoryStorage())


# ──────────────────────────────────────────────
#  ОБРАБОТЧИКИ
# ──────────────────────────────────────────────

# Хелпер для генерации текста главного меню
def get_start_menu_text(full_name: str, admin: bool) -> str:
    greeting = (
        f"👋 Привет, <b>{full_name}</b>!\n\n"
        f"Я — бот-гарант. Здесь вы можете:\n"
        f"• Написать гаранту по сделке\n"
        f"• Перейти в наш магазин товаров\n\n"
        f"Выберите действие:"
    )
    if admin:
        greeting += "\n\n<i>🔐 Вы вошли как администратор.</i>"
    return greeting


# Отправка стартового меню (новым сообщением)
async def send_start_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    username = user.username if user else None
    user_id  = user.id if user else None

    try:
        await save_admin_id_if_needed(user_id, username)
    except Exception as e:
        logger.error(f"save_admin_id_if_needed: {e}")

    admin = is_admin(username)
    greeting = get_start_menu_text(user.full_name, admin)

    try:
        await message.answer(
            greeting, 
            reply_markup=main_keyboard(is_admin=admin), 
            parse_mode="HTML"
        )
        await message.answer("Воспользуйтесь кнопкой «📱 Меню» ниже для быстрой навигации.", reply_markup=bottom_menu_keyboard())
    except Exception as e:
        logger.error(f"send_start_menu answer: {e}")


# Обработка команды /start
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await send_start_menu(message, state)


# ОБРАБОТЧИК ДЛЯ НАЖАТИЯ НА КНОПКУ «📱 Меню»
@dp.message(F.text == "📱 Меню")
async def btn_menu_pressed(message: Message, state: FSMContext) -> None:
    await send_start_menu(message, state)


# Возврат в главное меню через Inline-кнопку (редактированием сообщения)
@dp.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = callback.from_user
    admin = is_admin(user.username)
    text = get_start_menu_text(user.full_name, admin)
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=main_keyboard(is_admin=admin),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"cb_back_to_main: {e}")
        await callback.answer()


# Кнопка «Написать гаранту»
@dp.callback_query(F.data == "write_garant")
async def cb_write_garant(callback: CallbackQuery, state: FSMContext) -> None:
    user = callback.from_user
    if is_admin(user.username):
        await callback.answer("Вы администратор.", show_alert=False)
        return

    await state.set_state(UserStates.waiting_for_message)
    try:
        await callback.message.answer(
            "✏️ <b>Напишите ваше сообщение гаранту:</b>\n"
            "<i>Опишите суть вопроса или сделки как можно подробнее.</i>",
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"cb_write_garant: {e}")
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)


# Получение сообщения от пользователя
@dp.message(UserStates.waiting_for_message)
async def user_message_received(message: Message, state: FSMContext) -> None:
    if message.text == "📱 Меню":
        await state.clear()
        await send_start_menu(message, state)
        return

    await state.clear()
    user     = message.from_user
    user_id  = user.id
    username = user.username or "нет username"
    text     = message.text or ""

    if not text.strip():
        await message.answer("❗ Пожалуйста, отправьте текстовое сообщение.")
        await state.set_state(UserStates.waiting_for_message)
        return

    ticket_id = db_create_ticket(user_id, user.username, text)

    try:
        await message.answer(
            "✅ <b>Сообщение отправлено, ожидайте ответа.</b>\n"
            "<i>Гарант свяжется с вами в ближайшее время.</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"user_message_received answer user: {e}")

    admin_id = get_admin_id()
    if admin_id:
        notice = (
            f"🔔 <b>Новый запрос!</b>\n\n"
            f"👤 От: @{username} (ID: <code>{user_id}</code>)\n"
            f"🎫 Тикет: <b>#{ticket_id}</b>\n"
            f"📝 Текст:\n<blockquote>{text}</blockquote>"
        )
        try:
            await bot.send_message(
                admin_id, 
                notice, 
                parse_mode="HTML", 
                reply_markup=ticket_keyboard(ticket_id)
            )
        except Exception as e:
            logger.error(f"user_message_received notify admin: {e}")


# ──────────────────────────────────────────────
#  РАЗДЕЛ МАГАЗИНА
# ──────────────────────────────────────────────

# Главное меню магазина
@dp.callback_query(F.data == "shop_menu")
async def cb_shop_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    shop_text = (
        "🛍️ <b>Добро пожаловать в наш магазин!</b>\n\n"
        "Ниже представлены категории доступных товаров.\n"
        "Выберите интересующий вас раздел:"
    )
    try:
        await callback.message.edit_text(
            shop_text,
            reply_markup=shop_categories_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"cb_shop_menu: {e}")
        await callback.answer()


# Товар 1: Виртуальные аккаунты
@dp.callback_query(F.data == "shop_accounts")
async def cb_shop_accounts(callback: CallbackQuery) -> None:
    accs_text = (
        "👤 <b>Раздел: Виртуальные аккаунты</b>\n\n"
        "🔒 <b>Обычный аккаунт</b>\n"
        "├ <b>Отлега:</b> 1 неделя (Минимальная отлега 🙂)\n"
        "├ <b>Описание:</b> Эконом выбор, есть отлега и шанс блокировки аккаунта снижен.\n"
        "└ <b>Цена:</b> <code>80 ₽</code>\n\n"
        "⚡ <b>Средний аккаунт</b>\n"
        "├ <b>Отлега:</b> 2 недели (Средняя отлега 😄)\n"
        "├ <b>Описание:</b> Шанс на риск блокировки в 5 раз меньше, хорошо за свою цену.\n"
        "└ <b>Цена:</b> <code>100 ₽</code>\n\n"
        "👑 <b>Максимальный аккаунт</b>\n"
        "├ <b>Отлега:</b> 1 месяц (Максимальная отлега 😍)\n"
        "├ <b>Описание:</b> Шанс на риск блокировки максимально снижен, лучший выбор.\n"
        "└ <b>Цена:</b> <code>150 ₽</code>\n\n"
        "<i>💡 Для покупки или уточнения деталей напишите нашему гаранту!</i>"
    )
    try:
        await callback.message.edit_text(
            accs_text,
            reply_markup=back_to_shop_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"cb_shop_accounts: {e}")
        await callback.answer()


# Товар 2: Каналы с подписчиками
@dp.callback_query(F.data == "shop_channels")
async def cb_shop_channels(callback: CallbackQuery) -> None:
    channels_text = (
        "📢 <b>Раздел: Каналы с подписчиками</b>\n\n"
        "Доступные варианты накрутки/готовых каналов:\n\n"
        "🙂 <b>100 ПД</b> — <code>50 ₽</code>\n"
        "😊 <b>200 ПД</b> — <code>95 ₽</code>\n"
        "😎 <b>300 ПД</b> — <code>140 ₽</code>\n"
        "🔥 <b>500 ПД</b> — <code>230 ₽</code>\n"
        "🚀 <b>700 ПД</b> — <code>310 ₽</code>\n"
        "👑 <b>1000 ПД</b> — <code>430 ₽</code>\n\n"
        "<i>💡 Для оформления заказа или консультации свяжитесь с гарантом через бота.</i>"
    )
    try:
        await callback.message.edit_text(
            channels_text,
            reply_markup=back_to_shop_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"cb_shop_channels: {e}")
        await callback.answer()


# ──────────────────────────────────────────────
#  АДМИН-ПАНЕЛЬ
# ──────────────────────────────────────────────

# Кнопка «📥 Запросы» — показ тикетов
@dp.callback_query(F.data == "admin_tickets")
async def cb_admin_tickets(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    await state.clear()
    tickets = db_get_open_tickets(10)

    if not tickets:
        await callback.message.answer("📭 <b>Открытых запросов нет.</b>", parse_mode="HTML")
        await callback.answer()
        return

    await callback.message.answer(
        f"📋 <b>Открытые запросы ({len(tickets)} шт.):</b>\n"
        f"<i>Показаны последние 10</i>",
        parse_mode="HTML"
    )

    for t in tickets:
        uname = t.get("username") or "нет username"
        uid   = t.get("user_id")
        tid   = t.get("id")
        txt   = t.get("message_text", "")
        preview = txt[:300] + ("..." if len(txt) > 300 else "")

        card = (
            f"🎫 <b>Тикет #{tid}</b>\n"
            f"👤 @{uname} (ID: <code>{uid}</code>)\n"
            f"📝 <blockquote>{preview}</blockquote>"
        )
        try:
            await callback.message.answer(card, reply_markup=ticket_keyboard(tid), parse_mode="HTML")
        except Exception as e:
            logger.error(f"cb_admin_tickets send card: {e}")

    await callback.answer()


# Кнопка «✍️ Ответить»
@dp.callback_query(F.data.startswith("reply_"))
async def cb_reply_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    ticket_id = int(callback.data.split("_")[1])
    ticket    = db_get_ticket(ticket_id)

    if not ticket:
        await callback.answer("❌ Тикет не найден.", show_alert=True)
        return

    if ticket["status"] != "open":
        await callback.answer("⚠️ Тикет уже закрыт.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_reply_text)
    await state.update_data(ticket_id=ticket_id, message_id=callback.message.message_id)

    try:
        await callback.message.answer(
            f"✏️ <b>Введите ответ на тикет #{ticket_id}:</b>\n"
            f"<i>Ваш ответ будет отправлен пользователю @{ticket.get('username', '?')} в ЛС.</i>",
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"cb_reply_ticket: {e}")
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)


# Получение ответа от админа
@dp.message(AdminStates.waiting_for_reply_text)
async def admin_reply_received(message: Message, state: FSMContext) -> None:
    if message.text == "📱 Меню":
        await state.clear()
        await send_start_menu(message, state)
        return

    data      = await state.get_data()
    ticket_id = data.get("ticket_id")
    msg_id    = data.get("message_id")
    await state.clear()

    reply_text = message.text or ""
    if not reply_text.strip():
        await message.answer("❗ Ответ не может быть пустым.")
        await state.set_state(AdminStates.waiting_for_reply_text)
        await state.update_data(ticket_id=ticket_id, message_id=msg_id)
        return

    ticket = db_get_ticket(ticket_id)
    if not ticket:
        await message.answer("❌ Тикет не найден в БД.")
        return

    user_id = ticket["user_id"]
    reply_msg = (
        f"📬 <b>Ответ гаранта на ваш запрос</b>\n\n"
        f"<blockquote>{reply_text}</blockquote>\n\n"
        f"<i>Если у вас остались вопросы — напишите снова через /start</i>"
    )

    sent = False
    try:
        await bot.send_message(user_id, reply_msg, parse_mode="HTML")
        sent = True
    except Exception as e:
        logger.error(f"admin_reply_received send to user {user_id}: {e}")

    db_upda
