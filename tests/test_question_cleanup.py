"""Training screen lifecycle regressions; no live Telegram or user data."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage

import app.bot as app
from app.storage import SessionStore


def api_error():
    return TelegramAPIError(method=SendMessage(chat_id=42, text='test'), message='synthetic failure')


class QuestionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = app.store, app.bank, app.sessions
        app.store = SessionStore(Path(self.tmp.name) / 'test.sqlite3')
        app.sessions = {}
        self.state = app.store.accept(42)
        self.state.update(step='training', ticket=1, position=0, mistakes=0, question_message_id=10)
        app.store.save(42, self.state)
        self.question = {'text': 'Вопрос', 'choices': ['Да', 'Нет'], 'correct': 1}
        app.bank = SimpleNamespace(ticket=lambda n: [self.question] * 20, image_path=lambda q: None)
        self.events = []

        async def send(*args, **kwargs):
            self.events.append('send')
            return SimpleNamespace(message_id=20)

        async def photo(*args, **kwargs):
            self.events.append('photo')
            return SimpleNamespace(message_id=19)

        async def delete(**kwargs):
            self.events.append(('delete', kwargs['message_id']))
            return True

        self.call = SimpleNamespace(
            from_user=SimpleNamespace(id=42), data='answer:0',
            bot=SimpleNamespace(delete_message=AsyncMock(side_effect=delete)),
            answer=AsyncMock(),
            message=SimpleNamespace(message_id=10, chat=SimpleNamespace(id=42), photo=None,
                                    answer=AsyncMock(side_effect=send),
                                    answer_photo=AsyncMock(side_effect=photo),
                                    edit_reply_markup=AsyncMock()),
        )

    async def asyncTearDown(self):
        app.store, app.bank, app.sessions = self.old

    async def test_text_transition_preserves_answer_and_rejects_duplicate(self):
        await app.answer(self.call)
        self.assertEqual(self.events, ['send', ('delete', 10)])
        state = app.store.get(42)
        self.assertEqual(state['question_message_ids'], [20])
        self.assertEqual(state['position'], 1)
        self.assertEqual(app.store.answer_count(42), 1)
        await app.answer(self.call)
        self.assertEqual(app.store.answer_count(42), 1)
        self.assertEqual(self.call.answer.call_args.args[0], 'Этот вопрос уже обработан')

    async def test_long_photo_tracks_and_deletes_both_parts_after_restart(self):
        app.bank.image_path = lambda q: Path(self.tmp.name) / 'image.png'
        self.question['text'] = 'Длинный вопрос ' * 100
        await app.show_question(self.call, self.state)
        self.assertEqual(app.store.get(42)['question_message_ids'], [19, 20])
        self.assertEqual(self.events, ['photo', 'send', ('delete', 10)])
        self.events.clear()
        app.bank.image_path = lambda q: None
        await app.show_question(self.call, app.store.get(42))
        self.assertEqual(self.events, ['send', ('delete', 19), ('delete', 20)])

    async def test_photo_caption_is_tracked_as_one_message(self):
        app.bank.image_path = lambda q: Path(self.tmp.name) / 'image.png'
        await app.show_question(self.call, self.state)
        self.assertEqual(app.store.get(42)['question_message_ids'], [19])
        self.assertEqual(self.events, ['photo', ('delete', 10)])

    async def test_failed_send_preserves_previous_screen_and_saved_progress(self):
        self.call.message.answer.side_effect = api_error()
        with self.assertRaises(TelegramAPIError):
            await app.answer(self.call)
        self.call.bot.delete_message.assert_not_called()
        state = app.store.get(42)
        self.assertEqual(state['question_message_ids'], [10])
        self.assertNotIn('question_message_id', state)
        self.assertEqual(state['position'], 1)
        self.assertEqual(app.store.answer_count(42), 1)
        # Resuming sends the next unanswered question, without recording twice.
        self.call.message.answer.side_effect = None
        self.call.message.answer.return_value = SimpleNamespace(message_id=20)
        await app.resume(self.call)
        self.assertEqual(app.store.answer_count(42), 1)
        self.call.bot.delete_message.assert_awaited_once_with(chat_id=42, message_id=10)

    async def test_partial_photo_failure_only_removes_incomplete_new_photo(self):
        app.bank.image_path = lambda q: Path(self.tmp.name) / 'image.png'
        self.question['text'] = 'Long ' * 300
        self.call.message.answer.side_effect = api_error()
        with self.assertRaises(TelegramAPIError):
            await app.show_question(self.call, self.state)
        self.assertEqual(self.events, ['photo', ('delete', 19)])
        self.assertEqual(app.store.get(42)['question_message_id'], 10)

    async def test_delete_and_keyboard_and_ack_failures_do_not_stop_progress(self):
        self.call.bot.delete_message.side_effect = api_error()
        self.call.message.edit_reply_markup.side_effect = api_error()
        self.call.answer.side_effect = api_error()
        await app.answer(self.call)
        self.assertEqual(app.store.get(42)['question_message_id'], 20)
        self.assertEqual(app.store.answer_count(42), 1)

    async def test_final_result_sent_before_cleanup_and_review_preserved(self):
        self.state['position'] = 19
        self.state['question_message_ids'] = [9, 10]
        for position in range(19):
            app.store.record_answer(42, self.state, position, 0, False)
        app.store.save(42, self.state)
        await app.answer(self.call)
        self.assertEqual(self.events, ['send', ('delete', 9), ('delete', 10)])
        state = app.store.get(42)
        self.assertEqual(state['step'], 'ready')
        self.assertEqual(len(state['last_mistakes']), 20)
        self.assertEqual(app.store.answer_count(42), 20)
        self.assertNotIn('question_message_ids', state)

    async def test_failed_final_result_keeps_previous_screen(self):
        self.state['position'] = 19
        app.store.save(42, self.state)
        self.call.message.answer.side_effect = api_error()
        with self.assertRaises(TelegramAPIError):
            await app.answer(self.call)
        self.call.bot.delete_message.assert_not_called()
        self.assertEqual(app.store.get(42)['question_message_ids'], [10])
        self.assertEqual(app.store.answer_count(42), 1)

    async def test_navigation_keeps_menu_out_of_question_cleanup(self):
        await app.home(self.call)
        self.call.message.answer.assert_awaited_once()
        self.assertEqual(app.store.get(42)['question_message_id'], 10)
        self.events.clear()
        await app.resume(self.call)
        self.assertEqual(self.events, ['send', ('delete', 10)])

    async def test_concurrent_answer_and_resume_are_serialized(self):
        entered, release = asyncio.Event(), asyncio.Event()
        next_id = 20

        async def send(*args, **kwargs):
            nonlocal next_id
            message_id = next_id
            next_id += 1
            if message_id == 20:
                entered.set()
                await release.wait()
            self.events.append(('send', message_id))
            return SimpleNamespace(message_id=message_id)

        self.call.message.answer.side_effect = send
        answer_task = asyncio.create_task(app.answer(self.call))
        await entered.wait()
        resume_task = asyncio.create_task(app.resume(self.call))
        await asyncio.sleep(0)
        self.assertEqual(next_id, 21)
        release.set()
        await asyncio.gather(answer_task, resume_task)
        self.assertEqual(self.events, [('send', 20), ('delete', 10), ('send', 21), ('delete', 20)])
        self.assertEqual(app.store.get(42)['question_message_ids'], [21])
        self.assertEqual(app.store.answer_count(42), 1)
