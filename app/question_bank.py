"""Read structurally audited ABM dataset; no claim of official answer-key verification."""
import json
from pathlib import Path

class QuestionBank:
    def __init__(self, folder="data/abm_candidate"):
        folder = Path(folder)
        audit = json.loads((folder / "audit.json").read_text(encoding="utf-8"))
        if (audit.get("question_count") != 800 or audit.get("ticket_count") != 40
                or audit.get("missing") or audit.get("errors")):
            raise ValueError("ABM dataset has not passed structural audit")
        questions = json.loads((folder / "questions.json").read_text(encoding="utf-8"))
        if len(questions) != 800:
            raise ValueError("Question count mismatch")
        self.by_ticket = {}
        for q in questions:
            number = int(q["ticket"])
            position = int(q["position"])
            if not (1 <= number <= 40 and 1 <= position <= 20):
                raise ValueError("Invalid ticket position")
            if not q.get("text") or len(q.get("choices", [])) < 2:
                raise ValueError("Missing question/choices")
            if not 0 <= q["correct"] < len(q["choices"]):
                raise ValueError("Invalid correct answer index")
            image = q.get("image")
            if image and not (folder / image).is_file():
                raise ValueError(f"Image not found: {image}")
            self.by_ticket.setdefault(number, {})[position] = q
        if len(self.by_ticket) != 40 or any(set(rows) != set(range(1, 21)) for rows in self.by_ticket.values()):
            raise ValueError("Missing or duplicated ticket positions")
        self.folder = folder
        self.audit = audit

    def ticket(self, number):
        if number not in self.by_ticket:
            raise ValueError("Ticket must be 1..40")
        return [self.by_ticket[number][pos] for pos in range(1, 21)]

    def image_path(self, question):
        return self.folder / question["image"] if question.get("image") else None
