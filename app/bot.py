"""Telegram presentation layer for the N13 PDD candidate question bank.

No Telegram personal identifiers are persisted in this prototype.
This is a training interface, not a certified GIBDD examination.
"""
import asyncio
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.question_bank import QuestionBank

router = Router()
bank = None
# Ephemeral per-user state: lost on restart by design, until consent storage is implemented.
sessions = {}

def keyboard(buttons):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=value)]
        for label, value in buttons
    ])

@router.message(CommandStart())
async def start(message: Message):
    sessions.pop(message.from_user.id, None)
    await message.answer(
        "N13 ПДД 🚘\\nДля обучения нужно согласие на обработку Telegram ID "
        "и ответов в рамках текущей сессии. Сейчас данные не сохраняются в БД. "
        "При отказе обучение не начнётся.",
        reply_markup=keyboard([("Согласен", "consent:yes"), ("Не согласен", "consent:no")]),
    )

@router.callback_query(F.data == "consent:no")
async def decline(call: CallbackQuery):
    sessions.pop(call.from_user.id, None)
    await call.message.edit_text("Вы отказались. Для нового запуска используйте /start.")
    await call.answer()

@router.callback_query(F.data == "consent:yes")
async def accept(call: CallbackQuery):
    sessions[call.from_user.id] = {"step": "goal"}
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
    state = sessions.get(call.from_user.id)
    if not state or state.get("step") != "goal":
        await call.answer("Начните с /start", show_alert=True)
        return
    state["goal"] = call.data.split(":", 1)[1]
    state["step"] = "country"
    await call.message.edit_text(
        "Страна выдачи удостоверения (для обмена) или выберите «Не применимо».",
        reply_markup=keyboard([("Казахстан", "country:KZ"), ("Другая", "country:OTHER"),
                               ("Не применимо", "country:NA")]),
    )
    await call.answer()

@router.callback_query(F.data.startswith("country:"))
async def country(call: CallbackQuery):
    state = sessions.get(call.from_user.id)
    if not state or state.get("step") != "country":
        await call.answer("Начните с /start", show_alert=True)
        return
    state["country"] = call.data.split(":", 1)[1]
    state["step"] = "category"
    await call.message.edit_text(
        "Выберите категорию:",
        reply_markup=keyboard([("B", "category:B"), ("A", "category:A")]),
    )
    await call.answer()

@router.callback_query(F.data.startswith("category:"))
async def category(call: CallbackQuery):
    state = sessions.get(call.from_user.id)
    if not state or state.get("step") != "category":
        await call.answer("Начните с /start", show_alert=True)
        return
    state["category"] = call.data.split(":", 1)[1]
    state["step"] = "ready"
    await call.message.edit_text(
        "Настройки приняты. Условия обмена и допуска уточняйте в Госавтоинспекции. "
        "База пока не сверена с актуальным официальным сборником.\\n"
        "Выберите тренировочный билет:",
        reply_markup=keyboard([(f"Билет {i}", f"ticket:{i}") for i in range(1, 41)]),
    )
    await call.answer()

async def show_question(call, state):
    questions = bank.ticket(state["ticket"])
    pos = state["position"]
    q = questions[pos]
    answer_buttons = [(f"{n+1}. {value}", f"answer:{n}") for n, value in enumerate(q["choices"])]
    text = f"Билет {state['ticket']} | Вопрос {pos+1}/20\\n\\n{q['text']}"
    path = bank.image_path(q)
    if path:
        await call.message.answer_photo(FSInputFile(path), caption=text[:1024], reply_markup=keyboard(answer_buttons))
    else:
        await call.message.answer(text, reply_markup=keyboard(answer_buttons))

@router.callback_query(F.data.startswith("ticket:"))
async def ticket(call: CallbackQuery):
    state = sessions.get(call.from_user.id)
    if not state or state.get("step") != "ready":
        await call.answer("Начните с /start и завершите настройку", show_alert=True)
        return
    num = int(call.data.split(":", 1)[1])
    if num not in range(1, 41):
        await call.answer("Неизвестный билет", show_alert=True)
        return
    state.update(step="training", ticket=num, position=0, mistakes=0)
    await call.answer()
    await show_question(call, state)

@router.callback_query(F.data.startswith("answer:"))
async def answer(call: CallbackQuery):
    state = sessions.get(call.from_user.id)
    if not state or state.get("step") != "training":
        await call.answer("Тренировка не запущена", show_alert=True)
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
    # Clear buttons to prevent double answering the same message.
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Верно!" if correct else f"Неверно. Правильный ответ: {question['correct'] + 1}")
    if state["position"] == 20:
        errors = state["mistakes"]
        state["step"] = "ready"
        await call.message.answer(
            f"Билет завершён. Ошибок: {errors}/20",
            reply_markup=keyboard([("Пройти ещё раз", f"ticket:{state['ticket']}"),
                                  ("Билет 1", "ticket:1")]),
        )
    else:
        await show_question(call, state)

async def main():
    global bank
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN environment variable is required")
    bank = QuestionBank()
    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
