"""Dispatcher regressions with synthetic users; no Telegram token or production DB."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendPhoto, SendMessage, EditMessageText, EditMessageReplyMarkup, SetMyCommands, SetChatMenuButton
from aiogram.types import Chat, Message, PhotoSize, Update

import app.bot as app
from app.storage import SessionStore


class TelegramStub(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.next_id = 100
        self.photos = set()

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (AnswerCallbackQuery, SetMyCommands, SetChatMenuButton)):
            return True
        if isinstance(method, EditMessageText) and method.message_id in self.photos:
            raise AssertionError('Telegram cannot editMessageText on a photo')
        self.next_id += 1
        photo = None
        if isinstance(method, SendPhoto):
            self.photos.add(self.next_id)
            photo = [PhotoSize(file_id='photo', file_unique_id='unique', width=10, height=10)]
        return Message(message_id=self.next_id, date=datetime.now(timezone.utc),
                       chat=Chat(id=42, type='private'), photo=photo,
                       text=getattr(method, 'text', None),
                       caption=getattr(method, 'caption', None),
                       reply_markup=getattr(method, 'reply_markup', None))


class TelegramReviewTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.dp = Dispatcher()
        cls.dp.include_router(app.router)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = app.store, app.bank, app.sessions
        app.store = SessionStore(Path(self.tmp.name) / 'test.sqlite3')
        app.sessions = {}
        app.bank = SimpleNamespace(
            ticket=lambda n: [{'text': f'Вопрос {i}', 'choices': ['Да', 'Нет'], 'correct': 1,
                               'explanation': 'Пояснение'} for i in range(20)],
            image_path=lambda q: Path(self.tmp.name) / 'image.png',
        )
        self.session = TelegramStub()
        self.bot = Bot('123456:synthetic-test-token', session=self.session)

    async def asyncTearDown(self):
        app.store, app.bank, app.sessions = self.old
        await self.bot.session.close()

    async def command(self, text):
        await self.dp.feed_update(self.bot, Update.model_validate({
            'update_id': 1, 'message': {'message_id': 1, 'date': 1, 'chat': {'id': 42, 'type': 'private'},
            'from': {'id': 42, 'is_bot': False, 'first_name': 'Synthetic'},
            'text': text, 'entities': [{'type': 'bot_command', 'offset': 0, 'length': len(text)}]}}))

    async def callback(self, data, message_id=None, photo=False):
        message_id = message_id or self.session.next_id
        msg = {'message_id': message_id, 'date': 1, 'chat': {'id': 42, 'type': 'private'}}
        if photo:
            msg['photo'] = [{'file_id': 'photo', 'file_unique_id': 'unique', 'width': 10, 'height': 10}]
            self.session.photos.add(message_id)
        else:
            msg['text'] = 'Screen'
        await self.dp.feed_update(self.bot, Update.model_validate({
            'update_id': 2, 'callback_query': {'id': 'cb', 'chat_instance': 'test', 'data': data,
            'from': {'id': 42, 'is_bot': False, 'first_name': 'Synthetic'}, 'message': msg}}))

    def complete_ticket8(self):
        state = app.store.accept(42)
        state.update(ticket=8, step='ready', position=20)
        for pos in range(20):
            app.store.record_answer(42, state, pos, 0 if pos < 13 else 1, pos >= 13)
        state.pop('ticket')
        state['step'] = 'country'
        app.store.save(42, state)
        return state

    async def test_review_13_errors_after_restart_and_partial_retry(self):
        state = self.complete_ticket8()
        # New partial retry must not hide the previous complete ticket.
        state.update(ticket=8, position=3)
        for pos in range(3):
            app.store.record_answer(42, state, pos, 1, True)
        await self.command('/review')
        sent = self.session.requests[-1]
        self.assertIn('13', sent.text)
        count_before = app.store.answer_count(42)
        await self.callback('review:0')
        self.assertIsInstance(self.session.requests[-1], SendPhoto)
        self.assertIn('Билет 8, вопрос 1/20', self.session.requests[-1].caption)
        await self.callback('review:1', photo=True)
        self.assertIn('вопрос 2/20', self.session.requests[-1].caption)
        await self.callback('review:0', photo=True)
        self.assertIn('вопрос 1/20', self.session.requests[-1].caption)
        await self.command('/review')
        await self.callback('review:0')
        self.assertIn('вопрос 1/20', self.session.requests[-1].caption)
        self.assertEqual(app.store.answer_count(42), count_before)

    async def test_menu_from_photo_and_start_resume(self):
        self.complete_ticket8()
        state = app.store.get(42)
        state.update(step='training', ticket=9, position=2, question_message_id=50)
        app.store.save(42, state)
        await self.command('/start')
        self.assertIn('Незавершённая', self.session.requests[-1].text)
        await self.callback('nav:home', message_id=50, photo=True)
        screens = [r for r in self.session.requests if isinstance(r, SendMessage)]
        self.assertEqual(screens[-1].text, '🏠 Главное меню')
        self.assertEqual(app.store.get(42)['position'], 2)
        await self.callback('nav:tickets')
        await self.callback('page:4')
        markup = [r for r in self.session.requests if isinstance(r, EditMessageReplyMarkup)][-1].reply_markup
        self.assertEqual(markup.inline_keyboard[3][1].callback_data, 'ticket:40')
        await self.command('/start')
        self.assertIn('Вернулись', self.session.requests[-1].text)
        self.assertEqual(app.store.answer_count(42), 20)

    async def test_review_without_completed_run_replies(self):
        app.store.accept(42)
        await self.command('/review')
        self.assertIn('не найдено', self.session.requests[-1].text)

    async def test_native_menu_registration_and_commands(self):
        await app.configure_bot_menu(self.bot)
        commands, button = self.session.requests
        self.assertIsInstance(commands, SetMyCommands)
        self.assertEqual([c.command for c in commands.commands], ['start', 'menu', 'review', 'help'])
        self.assertIsInstance(button, SetChatMenuButton)
        self.assertEqual(button.menu_button.type, 'commands')
        self.complete_ticket8()
        await self.command('/menu')
        self.assertEqual(self.session.requests[-1].text, '🏠 Главное меню')
        await self.command('/help')
        self.assertIn('/review', self.session.requests[-1].text)
        self.assertEqual(app.store.answer_count(42), 20)

    async def test_start_resumes_each_onboarding_step_without_resetting_answers(self):
        self.complete_ticket8()
        for step, expected in [('goal', 'Цель подготовки'), ('country', 'Страна выдачи'), ('category', 'Выберите категорию')]:
            state = app.store.get(42)
            state['step'] = step
            app.store.save(42, state)
            await self.command('/start')
            message = self.session.requests[-1]
            self.assertIn('nav:setup', [b.callback_data for row in message.reply_markup.inline_keyboard for b in row])
            await self.callback('nav:setup')
            screen = [r for r in self.session.requests if isinstance(r, EditMessageText)][-1]
            self.assertIn(expected, screen.text)
            self.assertEqual(app.store.get(42), state)
            self.assertEqual(app.store.answer_count(42), 20)
