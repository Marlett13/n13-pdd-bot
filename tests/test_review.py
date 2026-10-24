import unittest
import tempfile
from pathlib import Path
from app.storage import SessionStore

class ReviewStorageTests(unittest.TestCase):
    def test_recent_answers_order_and_wrong_answers(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = SessionStore(str(Path(tmp.name) / "progress.sqlite3"))
        user = 8143
        state = store.accept(user)
        state.update(ticket=8, step="training", position=0)
        store.save(user,state)
        for i in range(20):
            store.record_answer(user, state, i, i%3, i%2 == 0)
        rows=store.latest_ticket_review(user,8)
        self.assertEqual(len(rows),20)
        self.assertEqual([r["position"] for r in rows],list(range(20)))
        self.assertEqual(sum(not r["correct"] for r in rows),10)
        store.delete(user)
        self.assertEqual(store.latest_ticket_review(user,8),[])

if __name__=="__main__":
    unittest.main()
