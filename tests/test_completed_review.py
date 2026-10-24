import tempfile
import unittest
from pathlib import Path
from app.storage import SessionStore

class CompletedReviewTests(unittest.TestCase):
    def test_completed_ticket_survives_onboarding_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(str(Path(folder) / "db.sqlite"))
            uid = 97112
            state = store.accept(uid)
            state.update(ticket=8, step="training")
            for pos in range(20):
                store.record_answer(uid, state, pos, pos % 3, pos % 2 == 0)
            state = store.get(uid)
            state.pop("ticket", None)
            state["step"] = "country"
            store.save(uid, state)
            completed = store.latest_completed_ticket(uid)
            self.assertIsNotNone(completed)
            number, rows = completed
            self.assertEqual(number, 8)
            self.assertEqual(len(rows), 20)
            self.assertEqual(sum(not item["correct"] for item in rows), 10)

    def test_partial_ticket_is_not_reviewed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(str(Path(folder) / "db.sqlite"))
            uid = 97113
            state = store.accept(uid)
            state["ticket"] = 2
            for pos in range(3):
                store.record_answer(uid, state, pos, 1, False)
            self.assertIsNone(store.latest_completed_ticket(uid))
