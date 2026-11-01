"""KCD(질병코드) 로컬 색인: 상병마스터 행으로 SQLite 색인을 만들고, 읽기 전용으로 조회한다.
색인 파일(data/kcd/kcd_index.sqlite3)은 git에 올리지 않고 서버가 원본이다(.gitignore의 data/kcd/).
외부 호출이 없다. 병명은 이 모듈 밖(네트워크)으로 나가지 않는다(Req 10.1).
CSV 읽기·열 이름 가정은 jobs/build_kcd_index.py가 맡고, 여기서는 정리된 행만 받는다.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from boheom_mate.core import ingest, kcd

INDEX_PATH = ingest.BASE_DIR / "data" / "kcd" / "kcd_index.sqlite3"
SOURCE_NAME = "건강보험심사평가원 상병마스터(KCD 기반)"
META_KEYS = ("source_file", "downloaded", "version", "license", "built_at", "row_count")

_SCHEMA = """
CREATE TABLE kcd  (code TEXT PRIMARY KEY,
                   code_raw TEXT NOT NULL,
                   name_ko TEXT NOT NULL,
                   name_ko_norm TEXT NOT NULL,
                   name_en TEXT);
CREATE INDEX kcd_name ON kcd(name_ko_norm);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _path(path):
    # 호출 시점의 INDEX_PATH를 읽어야 테스트·평가가 모듈 변수를 바꿔 쓸 수 있다.
    return Path(path) if path is not None else Path(INDEX_PATH)


def _norm_name(text):
    return re.sub(r"\s+", "", text or "")


def _like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build(rows, *, source_file, downloaded, version, license, path=None):
    """rows: [{"code", "name_ko", "name_en"(선택)}] → 색인 파일 생성. 이용 조건(license)이 없으면 거부한다.
    코드 모양이 아닌 행·이름이 빈 행은 건너뛰고, 같은 코드가 다시 나오면 처음 것을 둔다.
    임시 파일에 다 쓴 뒤 바꿔 끼워서, 실패해도 기존 색인이 깨지지 않는다.
    반환: {"row_count", "skipped", "path"}."""
    if not (license or "").strip():
        raise ValueError("--license(운영자가 확인한 이용 조건)가 없으면 색인을 만들지 않습니다")
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    count = skipped = 0
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(_SCHEMA)
        for row in rows:
            raw = (row.get("code") or "").strip()
            name = (row.get("name_ko") or "").strip()
            parsed = kcd.parse_code(raw)
            if parsed is None or not name:
                skipped += 1
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO kcd(code, code_raw, name_ko, name_ko_norm, name_en) VALUES (?, ?, ?, ?, ?)",
                (kcd.format_code(parsed), raw, name, _norm_name(name), (row.get("name_en") or "").strip() or None),
            )
            if cur.rowcount:
                count += 1
            else:
                skipped += 1
        meta = {
            "source_file": str(source_file or ""),
            "downloaded": str(downloaded or ""),
            "version": str(version or ""),
            "license": license.strip(),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "row_count": str(count),
        }
        conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", meta.items())
        conn.commit()
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(tmp, target)
    return {"row_count": count, "skipped": skipped, "path": str(target)}


def _connect(path=None):
    """읽기 전용(mode=ro)으로 연다. 파일이 없으면 만들지 않고 sqlite3.Error를 낸다."""
    target = _path(path).resolve()
    return sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)


def meta(path=None):
    """meta 표 전체 {key: value}. 색인이 없거나 열 수 없으면 {}."""
    if not _path(path).is_file():
        return {}
    try:
        conn = _connect(path)
        try:
            return dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def available(path=None):
    """파일이 있고 읽기 전용으로 열리며 meta에 license가 있으면 True."""
    return bool(meta(path).get("license", "").strip())


def lookup(text, limit=5, path=None):
    """병명이나 코드로 후보 [{"code", "name_ko"}]를 최대 limit개 돌려준다.
    코드 모양("N39.3", "N393")이면 코드 접두 일치(코드 순). 아니면 공백 뺀 한글명으로
    완전 → 접두 → 포함 일치, 같은 등급은 이름 짧은 순 → 코드 순. 색인 오류는 sqlite3.Error로 낸다."""
    if not isinstance(text, str) or limit <= 0:
        return []
    parsed = kcd.parse_code(text)
    conn = _connect(path)
    try:
        if parsed is not None:
            prefix = _like_escape(kcd.format_code(parsed)) + "%"
            rows = conn.execute(
                "SELECT code, name_ko FROM kcd WHERE code LIKE ? ESCAPE '\\' ORDER BY code LIMIT ?",
                (prefix, limit),
            ).fetchall()
        else:
            q = _norm_name(text)
            if not q:
                return []
            esc = _like_escape(q)
            rows = conn.execute(
                "SELECT code, name_ko FROM ("
                " SELECT code, name_ko, name_ko_norm,"
                "  CASE WHEN name_ko_norm = ? THEN 0"
                "       WHEN name_ko_norm LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END AS rank"
                " FROM kcd WHERE name_ko_norm LIKE ? ESCAPE '\\')"
                " ORDER BY rank, length(name_ko_norm), code LIMIT ?",
                (q, esc + "%", "%" + esc + "%", limit),
            ).fetchall()
    finally:
        conn.close()
    return [{"code": code, "name_ko": name} for code, name in rows]


def source_note(path=None):
    """출처·받은 날짜·버전·이용 조건 한 줄. 색인이 없으면 출처 이름만."""
    m = meta(path)
    if not m:
        return SOURCE_NAME
    return (f"{SOURCE_NAME}, 받은 날짜 {m.get('downloaded') or '미상'}, "
            f"버전 {m.get('version') or '미상'}, 이용 조건 {m.get('license') or '미확인'}")
