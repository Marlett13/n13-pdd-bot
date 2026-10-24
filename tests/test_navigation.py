import unittest
from app.bot import ticket_menu, home_keyboard

class NavigationTests(unittest.TestCase):
    def test_pages_are_bounded(self):
        first = ticket_menu(0)
        last = ticket_menu(4)
        self.assertEqual(len(first.inline_keyboard), 6)
        self.assertEqual(first.inline_keyboard[0][0].callback_data, "ticket:1")
        self.assertEqual(last.inline_keyboard[3][1].callback_data, "ticket:40")
    def test_training_menu_can_resume(self):
        labels = [x[0].text for x in home_keyboard({"step":"training"}).inline_keyboard]
        self.assertIn("▶️ Продолжить", labels)
        self.assertIn("🔄 Начать сначала", labels)

if __name__ == "__main__":
    unittest.main()
