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
