"""사용자가 입력한 질문(과 답변)을 저장하고 다시 불러온다.

SQLite 파일(questions.db) 하나에 저장하므로 앱을 다시 켜도 남는다.
로그인이 없으므로 질문마다 브라우저 세션 id를 붙여, 각 방문자는 자기 세션의 질문만 본다.
하루 질문 수는 따로 센다(질문을 삭제해도 줄지 않도록).
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).parent / "questions.db"
KST = ZoneInfo("Asia/Seoul")  # 서버(EC2)는 UTC라서 날짜를 한국 시간으로 맞춘다

# 처음 만든 뒤에 추가된 칸. 예전 questions.db에도 자동으로 붙인다.
EXTRA_COLUMNS = {"source": "TEXT", "answer": "TEXT", "found": "INTEGER", "evidence": "TEXT", "summary": "TEXT", "session_id": "TEXT"}


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
    conn.execute("CREATE TABLE IF NOT EXISTS daily_usage (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")
    return conn


def _now():
    return datetime.now(KST)


def add(text, session_id, source=None, answer=None, found=None, evidence=None, summary=None):
    """evidence: 근거 조항 목록 [{"section", "article_no", "title", "text"}, ...]
    summary: GPT가 파악한 질문 요지 한 줄"""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO questions (text, created_at, source, answer, found, evidence, summary, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                text,
                _now().strftime("%Y-%m-%d %H:%M"),
                source,
                answer,
                found,
                json.dumps(evidence or [], ensure_ascii=False),
                summary,
                session_id,
            ),
        )


def list_mine(session_id):
    """이 세션의 질문만, 최근에 저장한 것이 먼저 오도록 반환한다."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM questions WHERE session_id = ? ORDER BY id DESC", (session_id,)
        ).fetchall()
    questions = [dict(r) for r in rows]
    for q in questions:
        q["evidence"] = json.loads(q["evidence"] or "[]")
    return questions


def delete(question_id, session_id):
    """자기 세션의 질문만 지울 수 있다."""
    with _connect() as conn:
        conn.execute("DELETE FROM questions WHERE id = ? AND session_id = ?", (question_id, session_id))


def use_daily_quota(limit):
    """오늘(한국 시간) 질문 수를 1 늘리고 True를 반환한다. 이미 한도에 닿았으면 늘리지 않고 False."""
    day = _now().strftime("%Y-%m-%d")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO daily_usage (day, count) VALUES (?, 1)
               ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?""",
            (day, limit),
        )
        return cur.rowcount == 1


def used_today():
    day = _now().strftime("%Y-%m-%d")
    with _connect() as conn:
        row = conn.execute("SELECT count FROM daily_usage WHERE day = ?", (day,)).fetchone()
    return row["count"] if row else 0
