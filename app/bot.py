"""Telegram presentation layer for the N13 PDD candidate question bank.

Telegram identifiers and training answers are persisted only after consent.
This is a training interface, not a certified GIBDD examination.
"""
import asyncio
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.question_bank import QuestionBank
from app.storage import SessionStore

router = Router()
bank = None
# Ephemeral per-user state: lost on restart by design, until consent storage is implemented.
sessions = {}
store = None

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
            await message.answer("Незавершённая тренировка найдена.", reply_markup=keyboard([("Продолжить", "resume")]))
        elif previous.get("step") == "ready":
            await message.answer("Вернулись к билетам.", reply_markup=ticket_menu())
        else:
            await message.answer("Настройка уже начата. Продолжайте с /start после завершения или удаления профиля.")
        return
    await message.answer(
        "N13 ПДД 🚘\\nДля обучения нужно согласие на обработку Telegram ID "
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
    state["step"] = "country"
    save_state(call.from_user.id, state)
    await call.message.edit_text(
        "Страна выдачи удостоверения (для обмена) или выберите «Не применимо».",
        reply_markup=keyboard([("Казахстан", "country:KZ"), ("Другая", "country:OTHER"),
                               ("Не применимо", "country:NA")]),
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
        "База пока не сверена с актуальным официальным сборником.\\n"
        "Выберите тренировочный билет:",
        reply_markup=ticket_menu(),
    )
    await call.answer()

def ticket_menu():
    return keyboard([(f"Билет {i}", f"ticket:{i}") for i in range(1, 41)])

async def show_question(call, state):
    questions = bank.ticket(state["ticket"])
    pos = state["position"]
    q = questions[pos]
    answer_buttons = [(f"{n+1}. {value}", f"answer:{n}") for n, value in enumerate(q["choices"])]
    text = f"Билет {state['ticket']} | Вопрос {pos+1}/20\\n\\n{q['text']}"
    path = bank.image_path(q)
    if path:
        sent = await call.message.answer_photo(FSInputFile(path), caption=text[:1024], reply_markup=keyboard(answer_buttons))
    else:
        sent = await call.message.answer(text, reply_markup=keyboard(answer_buttons))
    state["question_message_id"] = sent.message_id
    save_state(call.from_user.id, state)

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
    save_state(call.from_user.id, state)
    await call.answer()
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
        await call.message.answer(
            f"Билет завершён. Ошибок: {errors}/20",
            reply_markup=keyboard([("Пройти ещё раз", f"ticket:{state['ticket']}"),
                                  ("Билет 1", "ticket:1")]),
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
    await show_question(call, state)

async def main():
    global bank, store
    token = os.getenv("BOT_TOKEN") or os.getenv("PDD_BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN environment variable is required")
    bank = QuestionBank(os.getenv("PDD_BANK_PATH", "data/abm_candidate"))
    store = SessionStore(os.getenv("DATABASE_PATH", "data/n13-pdd.sqlite3"))
    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
