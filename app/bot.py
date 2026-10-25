"""Telegram presentation layer for the N13 PDD candidate question bank.

Telegram identifiers and training answers are persisted only after consent.
This is a training interface, not a certified GIBDD examination.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Message

from app.question_bank import QuestionBank
from app.storage import SessionStore

router = Router()
bank = None
# Ephemeral per-user state: lost on restart by design, until consent storage is implemented.
sessions = {}
store = None

async def configure_bot_menu(bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать или продолжить обучение"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="review", description="Разобрать ошибки последнего билета"),
        BotCommand(command="help", description="Помощь и команды"),
    ])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def get_state(user_id):
    return sessions.get(user_id) or (store.get(user_id) if store else None)

def save_state(user_id, state):
    store.save(user_id, state)

def keyboard(buttons):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=value)]
        for label, value in buttons
    ])

@router.message(Command("delete_data"))
async def delete_data(message: Message):
    sessions.pop(message.from_user.id, None)
    store.delete(message.from_user.id)
    await message.answer("Ваш профиль и ответы удалены из локальной базы N13 ПДД.")

@router.message(CommandStart())
async def start(message: Message):
    previous = get_state(message.from_user.id)
    if previous:
        if previous.get("step") == "training":
            await message.answer("Незавершённая тренировка найдена.", reply_markup=keyboard([("▶️ Продолжить", "resume"), ("🏠 В начало", "nav:home")]))
        elif previous.get("step") == "ready":
            await message.answer("Вернулись к билетам.", reply_markup=ticket_menu())
        else:
            await message.answer("Настройка не завершена. Продолжите с сохранённого шага.", reply_markup=keyboard([
                ("▶️ Продолжить настройку", "nav:setup"),
                ("🏠 В меню", "nav:home"),
                ("🔄 Начать сначала", "nav:restart"),
            ]))
        return
    await message.answer(
        "N13 ПДД 🚘\nДля обучения нужно согласие на обработку Telegram ID "
        "и ответов в рамках текущей сессии. При согласии Telegram ID и ответы сохраняются в локальной БД. "
        "Для удаления данных используйте /delete_data. При отказе обучение не начнётся.",
        reply_markup=keyboard([("Согласен", "consent:yes"), ("Не согласен", "consent:no")]),
    )

@router.callback_query(F.data == "consent:no")
async def decline(call: CallbackQuery):
    sessions.pop(call.from_user.id, None)
    await call.message.edit_text("Вы отказались. Для нового запуска используйте /start.")
    await call.answer()

@router.callback_query(F.data == "consent:yes")
async def accept(call: CallbackQuery):
    sessions[call.from_user.id] = store.accept(call.from_user.id)
    await call.message.edit_text(
        "Цель подготовки:",
        reply_markup=keyboard([
            ("Обмен иностранных прав", "goal:exchange"),
            ("Получение впервые", "goal:first"),
            ("Возврат после лишения", "goal:return"),
            ("Освежить знания", "goal:refresh"),
            ("Другая причина", "goal:other"),
        ]),
    )
    await call.answer()

@router.callback_query(F.data.startswith("goal:"))
async def goal(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "goal":
        await call.answer("Начните с /start", show_alert=True)
        return
    state["goal"] = call.data.split(":", 1)[1]
    if state["goal"] == "exchange":
        state["step"] = "country"
        save_state(call.from_user.id, state)
        await call.message.edit_text(
            "Страна выдачи иностранного удостоверения:",
            reply_markup=keyboard([("Казахстан", "country:KZ"), ("Другая", "country:OTHER"),
                                   ("◀️ Назад", "nav:restart")]),
        )
    else:
        state["country"] = "NA"
        state["step"] = "category"
        save_state(call.from_user.id, state)
        await call.message.edit_text(
            "Выберите категорию:",
            reply_markup=keyboard([("B", "category:B"), ("A", "category:A"),
                                   ("◀️ Назад", "nav:restart")]),
        )
    await call.answer()

@router.callback_query(F.data.startswith("country:"))
async def country(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "country":
        await call.answer("Начните с /start", show_alert=True)
        return
    state["country"] = call.data.split(":", 1)[1]
    state["step"] = "category"
    save_state(call.from_user.id, state)
    await call.message.edit_text(
        "Выберите категорию:",
        reply_markup=keyboard([("B", "category:B"), ("A", "category:A")]),
    )
    await call.answer()

@router.callback_query(F.data.startswith("category:"))
async def category(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "category":
        await call.answer("Начните с /start", show_alert=True)
        return
    state["category"] = call.data.split(":", 1)[1]
    state["step"] = "ready"
    save_state(call.from_user.id, state)
    await call.message.edit_text(
        "Настройки приняты. Условия обмена и допуска уточняйте в Госавтоинспекции. "
        "База пока не сверена с актуальным официальным сборником.\n\n"
        "Выберите тренировочный билет:",
        reply_markup=ticket_menu(),
    )
    await call.answer()

def ticket_menu(page=0):
    page = max(0, min(4, page))
    rows = []
    for number in range(page * 8 + 1, page * 8 + 9, 2):
        rows.append([
            InlineKeyboardButton(text=f"Билет {n}", callback_data=f"ticket:{n}")
            for n in (number, number + 1)
        ])
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text="◀️", callback_data=f"page:{page-1}"))
    navigation.append(InlineKeyboardButton(text=f"{page+1}/5", callback_data="noop"))
    if page < 4:
        navigation.append(InlineKeyboardButton(text="▶️", callback_data=f"page:{page+1}"))
    rows.append(navigation)
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def home_keyboard(state):
    buttons = []
    if state.get("step") == "training":
        buttons.append(("▶️ Продолжить", "resume"))
    elif state.get("step") in ("goal", "country", "category"):
        buttons.append(("▶️ Продолжить настройку", "nav:setup"))
    buttons += [("📚 Выбрать билет", "nav:tickets"), ("🔄 Начать сначала", "nav:restart")]
    return keyboard(buttons)

async def navigation_screen(call, text, markup):
    """Photo questions have captions: Telegram cannot edit them as text messages."""
    if call.message.photo:
        await call.message.answer(text, reply_markup=markup)
        await call.message.edit_reply_markup(reply_markup=None)
    else:
        await call.message.edit_text(text, reply_markup=markup)

@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()

@router.callback_query(F.data.startswith("page:"))
async def page(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "ready":
        await call.answer("Откройте выбор билетов из меню", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=ticket_menu(int(call.data.split(":")[1])))
    await call.answer()

@router.message(Command("menu"))
async def menu(message: Message):
    state = get_state(message.from_user.id)
    if state:
        await message.answer("🏠 Главное меню", reply_markup=home_keyboard(state))
    else:
        await start(message)

@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "N13 ПДД 🚘\n\n"
        "/start — начать или продолжить обучение\n"
        "/menu — главное меню\n"
        "/review — ошибки последнего завершённого билета\n"
        "/help — эта справка\n\n"
        "Команды также доступны по кнопке Telegram «Меню» рядом с полем ввода."
    )

@router.callback_query(F.data == "nav:setup")
async def continue_setup(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state:
        await call.answer("Начните с /start", show_alert=True)
        return
    step = state.get("step")
    if step == "goal":
        text = "Цель подготовки:"
        buttons = [
            ("Обмен иностранных прав", "goal:exchange"),
            ("Получение впервые", "goal:first"),
            ("Возврат после лишения", "goal:return"),
            ("Освежить знания", "goal:refresh"),
            ("Другая причина", "goal:other"),
        ]
    elif step == "country":
        text = "Страна выдачи иностранного удостоверения:"
        buttons = [("Казахстан", "country:KZ"), ("Другая", "country:OTHER")]
    elif step == "category":
        text = "Выберите категорию:"
        buttons = [("B", "category:B"), ("A", "category:A")]
    else:
        await call.answer("Настройка уже завершена. Откройте /menu", show_alert=True)
        return
    await navigation_screen(call, text, keyboard(buttons + [("🏠 В меню", "nav:home")]))
    await call.answer()

@router.callback_query(F.data == "nav:home")
async def home(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state:
        await call.answer("Начните с /start", show_alert=True)
        return
    await navigation_screen(call, "🏠 Главное меню", home_keyboard(state))
    await call.answer()

@router.callback_query(F.data == "nav:tickets")
async def tickets(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state:
        await call.answer("Начните с /start", show_alert=True)
        return
    state["step"] = "ready"
    state.pop("question_message_id", None)
    save_state(call.from_user.id, state)
    await navigation_screen(call, "Выберите билет:", ticket_menu())
    await call.answer()

@router.callback_query(F.data == "nav:restart")
async def restart(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state:
        await call.answer("Начните с /start", show_alert=True)
        return
    state.update(step="goal", position=0)
    state.pop("ticket", None)
    state.pop("question_message_id", None)
    save_state(call.from_user.id, state)
    await navigation_screen(call, "Цель подготовки:", keyboard([
        ("Обмен иностранных прав", "goal:exchange"),
        ("Получение впервые", "goal:first"),
        ("Возврат после лишения", "goal:return"),
        ("Освежить знания", "goal:refresh"),
        ("Другая причина", "goal:other"),
    ]))
    await call.answer()

def question_text(ticket_number, position, question):
    """Keep full answers in message body, not on truncated Telegram buttons."""
    options = "\n".join(
        f"{index}. {choice}" for index, choice in enumerate(question["choices"], 1)
    )
    return (
        f"Билет {ticket_number} | Вопрос {position + 1}/20\n\n"
        f"{question['text']}\n\n{options}\n\nВыберите номер ответа:"
    )

async def show_question(call, state):
    q = bank.ticket(state["ticket"])[state["position"]]
    text = question_text(state["ticket"], state["position"], q)
    buttons = keyboard([(str(i), f"answer:{i-1}") for i in range(1, len(q["choices"]) + 1)] + [("🏠 В меню", "nav:home")])
    path = bank.image_path(q)
    if path and len(text) <= 1024:
        sent = await call.message.answer_photo(
            FSInputFile(path), caption=text, reply_markup=buttons
        )
    elif path:
        await call.message.answer_photo(FSInputFile(path))
        sent = await call.message.answer(text, reply_markup=buttons)
    else:
        sent = await call.message.answer(text, reply_markup=buttons)
    state["question_message_id"] = sent.message_id
    save_state(call.from_user.id, state)

def review_keyboard(index, length):
    items = []
    if index > 0:
        items.append(("◀️ Предыдущая", f"review:{index-1}"))
    if index + 1 < length:
        items.append(("Следующая ▶️", f"review:{index+1}"))
    items.append(("🏠 В меню", "nav:home"))
    return keyboard(items)

async def show_review(call, state, index):
    mistakes = state.get("last_mistakes") or []
    if index < 0 or index >= len(mistakes):
        await call.answer("Нет ошибок для разбора", show_alert=True)
        return
    ticket_number = state["last_ticket"]
    row = mistakes[index]
    question = bank.ticket(ticket_number)[row["position"]]
    answers = "\n".join(f"{i}. {answer}" for i, answer in enumerate(question["choices"], 1))
    explanation = (question.get("explanation") or "").strip()
    body = (
        f"🔎 Разбор ошибок {index+1}/{len(mistakes)}\n"
        f"Билет {ticket_number}, вопрос {row['position']+1}/20\n\n"
        f"{question['text']}\n\n{answers}\n\n"
        f"Ваш ответ: {row['answer']+1}\n"
        f"Правильный по учебной базе: {question['correct']+1}"
    )
    if explanation:
        body += f"\n\nПояснение из учебной базы:\n{explanation}"
    else:
        body += "\n\nПояснение отсутствует в источнике."
    markup = review_keyboard(index, len(mistakes))
    image = bank.image_path(question)
    if image and len(body) <= 1024:
        sent = await call.message.answer_photo(FSInputFile(image), caption=body, reply_markup=markup)
    else:
        if image:
            await call.message.answer_photo(FSInputFile(image))
        sent = await call.message.answer(body[:4096], reply_markup=markup)
    state["review_message_id"] = sent.message_id
    save_state(call.from_user.id, state)

@router.message(Command("review"))
async def review_command(message: Message):
    state = get_state(message.from_user.id)
    if not state:
        await message.answer("Сначала запустите /start")
        return
    completed = store.latest_completed_ticket(message.from_user.id)
    if completed:
        ticket_number, rows = completed
        state["last_ticket"] = ticket_number
        state["last_mistakes"] = [r for r in rows if not r["correct"]]
        save_state(message.from_user.id, state)
    mistakes = state.get("last_mistakes") or []
    state.pop("review_message_id", None)
    save_state(message.from_user.id, state)
    if not mistakes:
        await message.answer("Сохранённых ошибок завершённого билета не найдено.")
        return
    await message.answer(
        f"🔎 Найдено ошибок: {len(mistakes)}",
        reply_markup=keyboard([("Начать разбор", "review:0"), ("🏠 В меню", "nav:home")]),
    )

@router.callback_query(F.data.startswith("review:"))
async def review(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or not state.get("last_mistakes"):
        await call.answer("Ошибок для разбора нет", show_alert=True)
        return
    if state.get("review_message_id") and state["review_message_id"] != call.message.message_id:
        await call.answer("Это старый экран разбора", show_alert=True)
        return
    try:
        index = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("Неверный номер", show_alert=True)
        return
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await show_review(call, state, index)

@router.callback_query(F.data.startswith("ticket:"))
async def ticket(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "ready":
        await call.answer("Начните с /start и завершите настройку", show_alert=True)
        return
    num = int(call.data.split(":", 1)[1])
    if num not in range(1, 41):
        await call.answer("Неизвестный билет", show_alert=True)
        return
    state.update(step="training", ticket=num, position=0, mistakes=0)
    state.pop("review_message_id", None)
    save_state(call.from_user.id, state)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await show_question(call, state)

@router.callback_query(F.data.startswith("answer:"))
async def answer(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "training":
        await call.answer("Тренировка не запущена", show_alert=True)
        return
    if state.get("question_message_id") != call.message.message_id:
        await call.answer("Этот вопрос уже обработан", show_alert=True)
        return
    answer_index = int(call.data.split(":", 1)[1])
    question = bank.ticket(state["ticket"])[state["position"]]
    if answer_index not in range(len(question["choices"])):
        await call.answer("Неверный вариант", show_alert=True)
        return
    correct = answer_index == question["correct"]
    if not correct:
        state["mistakes"] += 1
    state["position"] += 1
    state.pop("question_message_id", None)
    if state["position"] == 20:
        state["step"] = "ready"
    store.record_answer(call.from_user.id, state, state["position"] - 1, answer_index, correct)
    # Clear buttons to prevent double answering the same message.
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Верно!" if correct else f"Неверно. Правильный ответ: {question['correct'] + 1}")
    if state["position"] == 20:
        errors = state["mistakes"]
        rows = store.latest_ticket_review(call.from_user.id, state["ticket"])
        state["last_ticket"] = state["ticket"]
        state["last_mistakes"] = [row for row in rows if not row["correct"]]
        state.pop("review_message_id", None)
        save_state(call.from_user.id, state)
        options = []
        if state["last_mistakes"]:
            options.append((f"🔎 Разбор ошибок ({len(state['last_mistakes'])})", "review:0"))
        options.extend([("📚 Выбрать билет", "nav:tickets"), ("🏠 В меню", "nav:home")])
        await call.message.answer(
            f"Билет завершён. Ошибок: {errors}/20\n"
            "Ответы для разбора взяты из учебной базы, не сверенной с официальными билетами.",
            reply_markup=keyboard(options),
        )
    else:
        await show_question(call, state)

@router.callback_query(F.data == "resume")
async def resume(call: CallbackQuery):
    state = get_state(call.from_user.id)
    if not state or state.get("step") != "training":
        await call.answer("Нет активной тренировки", show_alert=True)
        return
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await show_question(call, state)

async def main():
    global bank, store
    token = os.getenv("BOT_TOKEN") or os.getenv("PDD_BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN environment variable is required")
    bank = QuestionBank(os.getenv("PDD_BANK_PATH", "data/abm_candidate"))
    store = SessionStore(os.getenv("DATABASE_PATH", "data/n13-pdd.sqlite3"))
    proxy = os.getenv('TELEGRAM_PROXY_URL', '').strip()
    bot = Bot(token, session=AiohttpSession(proxy=proxy or None))
    dp = Dispatcher()
    dp.include_router(router)
    try:
        try:
            await configure_bot_menu(bot)
        except TelegramAPIError as error:
            # A temporary menu-registration failure must not stop training/polling.
            logging.getLogger(__name__).warning("Bot menu registration failed: %s", type(error).__name__)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
