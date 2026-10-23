import unittest
from app.bot import question_text

class QuestionTextTests(unittest.TestCase):
    def test_full_long_options_displayed(self):
        q={"text":"Когда разрешена остановка?", "choices":["Длинный вариант ответа, который не помещается на кнопке мобильного Telegram"*3,"В любых случаях","Только при неисправности"]}
        output=question_text(1,0,q)
        for option in q["choices"]:
            self.assertIn(option,output)
        self.assertIn("1. ",output)
        self.assertIn("2. ",output)
        self.assertIn("3. ",output)
        self.assertNotIn(r"\n",output)

if __name__=="__main__":
    unittest.main()
