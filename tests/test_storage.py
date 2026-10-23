import tempfile
import unittest
from pathlib import Path
from app.storage import SessionStore

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "pdd.sqlite")
        self.store = SessionStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_profile_before_consent(self):
        self.assertIsNone(self.store.get(12))
        with self.assertRaises(ValueError):
            self.store.save(12, {"step": "ready"})
        self.assertIsNone(SessionStore(self.path).get(12))

    def test_recovery_after_restart(self):
        state = self.store.accept(12)
        self.assertEqual(state["step"], "goal")
        state.update(step="training", goal="exchange", country="KZ", category="B",
                     ticket=1, position=3, mistakes=1)
        self.store.save(12, state)
        recovered = SessionStore(self.path).get(12)
        self.assertEqual(recovered, state)

    def test_answer_history_and_erasure(self):
        state = self.store.accept(12)
        state.update(step="training", ticket=1, position=1, mistakes=0)
        self.store.record_answer(12, state, 0, 1, True)
        self.assertEqual(SessionStore(self.path).answer_count(12), 1)
        self.store.delete(12)
        self.assertEqual(self.store.answer_count(12), 0)
        self.assertIsNone(self.store.get(12))

if __name__ == "__main__":
    unittest.main()
