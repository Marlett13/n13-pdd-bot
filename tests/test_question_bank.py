"""Integration check against the downloaded ABM candidate."""
from app.question_bank import QuestionBank

def test_candidate():
    bank = QuestionBank()
    assert len(bank.by_ticket) == 40
    assert sum(len(bank.ticket(n)) for n in range(1, 41)) == 800
    for n in range(1, 41):
        ticket = bank.ticket(n)
        assert [q["position"] for q in ticket] == list(range(1, 21))
        for q in ticket:
            assert bank.image_path(q) is None or bank.image_path(q).is_file()

if __name__ == "__main__":
    test_candidate()
    print("QuestionBank integration OK: 40 tickets, 800 questions, image paths valid")
