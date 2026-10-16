"""사용자가 입력한 질문(과 답변)을 저장하고 다시 불러온다.

SQLite 파일(questions.db) 하나에 저장하므로 새로고침하거나 앱을 다시 켜도 남는다.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "questions.db"

# 처음 만든 뒤에 추가된 칸. 예전 questions.db에도 자동으로 붙인다.
EXTRA_COLUMNS = {"source": "TEXT", "answer": "TEXT", "found": "INTEGER", "evidence": "TEXT", "summary": "TEXT"}


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS questions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               text TEXT NOT NULL,
               created_at TEXT NOT NULL
           )"""
    )
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(questions)")}
    for name, sql_type in EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE questions ADD COLUMN {name} {sql_type}")
    return conn


def add(text, source=None, answer=None, found=None, evidence=None, summary=None):
    """evidence: 근거 조항 목록 [{"section", "article_no", "title", "text"}, ...]
    summary: GPT가 파악한 질문 요지 한 줄"""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO questions (text, created_at, source, answer, found, evidence, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                text,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                source,
                answer,
                found,
                json.dumps(evidence or [], ensure_ascii=False),
                summary,
            ),
        )


def list_all():
    """최근에 저장한 것이 먼저 오도록 반환한다."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM questions ORDER BY id DESC").fetchall()
    questions = [dict(r) for r in rows]
    for q in questions:
        q["evidence"] = json.loads(q["evidence"] or "[]")
    return questions


def delete(question_id):
    with _connect() as conn:
        conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
