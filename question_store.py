"""채팅 대화를 저장하고 다시 불러온다.

SQLite 파일(questions.db) 하나에 저장하므로 앱을 다시 켜도 남는다.
로그인이 없으므로 메시지마다 브라우저 세션 id를 붙여, 각 방문자는 자기 세션의 대화만 본다.
하루 질문 수는 따로 센다.
(예전 버전의 질문 하나·답변 하나 기록은 같은 파일의 questions 표에 그대로 남아 있다.)
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).parent / "questions.db"
KST = ZoneInfo("Asia/Seoul")  # 서버(EC2)는 UTC라서 날짜를 한국 시간으로 맞춘다


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_messages (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               session_id TEXT NOT NULL,
               role TEXT NOT NULL,
               content TEXT NOT NULL,
               source TEXT,
               evidence TEXT,
               calculation TEXT,
               summary TEXT,
               created_at TEXT NOT NULL
           )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS daily_usage (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")
    return conn


def _now():
    return datetime.now(KST)


def add_message(session_id, role, content, source=None, evidence=None, calculation=None, summary=None):
    """role: "user" 또는 "assistant"
    evidence: 근거 조항 목록 [{"section", "article_no", "title", "text"}, ...]
    calculation: calculator.calculate 결과 + 계산에 쓴 값(inputs)"""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO chat_messages (session_id, role, content, source, evidence, calculation, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                role,
                content,
                source,
                json.dumps(evidence or [], ensure_ascii=False),
                json.dumps(calculation, ensure_ascii=False) if calculation else None,
                summary,
                _now().strftime("%Y-%m-%d %H:%M"),
            ),
        )


def list_messages(session_id):
    """이 세션의 대화를 오래된 것부터 반환한다."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    messages = [dict(r) for r in rows]
    for m in messages:
        m["evidence"] = json.loads(m["evidence"] or "[]")
        m["calculation"] = json.loads(m["calculation"]) if m["calculation"] else None
    return messages


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
