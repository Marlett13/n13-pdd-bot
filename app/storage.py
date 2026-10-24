"""SQLite-backed session repository; only written after explicit consent."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CONSENT_VERSION = "n13-pdd-prototype-2026-10-23"

class SessionStore:
    def __init__(self, path="data/n13-pdd.sqlite3"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                consent_version TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS answers (
                user_id INTEGER NOT NULL,
                ticket INTEGER NOT NULL,
                position INTEGER NOT NULL,
                answer INTEGER NOT NULL,
                correct INTEGER NOT NULL,
                answered_at TEXT NOT NULL
            )""")

    def connect(self):
        return sqlite3.connect(self.path)

    def get(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT state_json FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def accept(self, user_id):
        now = datetime.now(timezone.utc).isoformat()
        state = {"step": "goal"}
        with self.connect() as db:
            db.execute("""INSERT INTO sessions(user_id,state_json,consent_version,consent_at,updated_at)
                          VALUES (?,?,?,?,?)
                          ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json,
                          consent_version=excluded.consent_version,
                          consent_at=excluded.consent_at,updated_at=excluded.updated_at""",
                       (user_id, json.dumps(state), CONSENT_VERSION, now, now))
        return state

    def save(self, user_id, state):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute("UPDATE sessions SET state_json=?,updated_at=? WHERE user_id=?",
                                (json.dumps(state), now, user_id))
            if cursor.rowcount != 1:
                raise ValueError("Consent required before storing state")

    def record_answer(self, user_id, state, position, answer, correct):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE user_id=?", (user_id,)).fetchone():
                raise ValueError("Consent required")
            db.execute("INSERT INTO answers VALUES (?,?,?,?,?,?)",
                       (user_id, state["ticket"], position, answer, int(correct), now))
            db.execute("UPDATE sessions SET state_json=?,updated_at=? WHERE user_id=?",
                       (json.dumps(state), now, user_id))

    def delete(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM answers WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def answer_count(self, user_id):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM answers WHERE user_id=?", (user_id,)).fetchone()[0]

    def latest_ticket_review(self, user_id, ticket, count=20):
        """Return newest attempts for one finished ticket, in question order."""
        with self.connect() as db:
            rows = db.execute(
                """SELECT position, answer, correct FROM answers
                   WHERE user_id=? AND ticket=? ORDER BY rowid DESC LIMIT ?""",
                (user_id, ticket, count),
            ).fetchall()
        return [{"position": pos, "answer": ans, "correct": bool(ok)} for pos, ans, ok in reversed(rows)]

    def latest_completed_ticket(self, user_id):
        """Find the latest run with a complete ordered 0..19 sequence, even after profile reset."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT ticket, position, answer, correct FROM answers WHERE user_id=? ORDER BY rowid DESC",
                (user_id,),
            ).fetchall()
        # A run is complete when question positions 19..0 appear consecutively in reverse.
        for start, row in enumerate(rows):
            if row[1] != 19:
                continue
            part = rows[start:start + 20]
            if len(part) == 20 and all(
                item[0] == row[0] and item[1] == 19 - i
                for i, item in enumerate(part)
            ):
                return row[0], [
                    {"position": item[1], "answer": item[2], "correct": bool(item[3])}
                    for item in reversed(part)
                ]
        return None
