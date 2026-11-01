"""상담 신청(Outbox_File)을 state/outbox/<접수번호>.json으로 저장하고, 오래된 파일을 지우고, 세션별 건수를 센다.
접수번호와 파일 내용은 호출자(ui/tools.py)가 함수로 넘긴다. 이 모듈은 core를 import하지 않는다.
민감정보가 들어갈 수 있어 print·logging을 쓰지 않고, 오류 메시지에도 경로·내용을 넣지 않는다.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from boheom_mate.store import question_store

# boheom_mate/store/outbox.py → 프로젝트 루트는 두 단계 위. state/는 .gitignore에 있다.
OUTBOX_DIR = Path(__file__).resolve().parents[2] / "state" / "outbox"
RETENTION_HOURS = int(os.getenv("OUTBOX_RETENTION_HOURS", str(question_store.MEMORY_TTL_HOURS)))
RECEIPT_RETRIES = 5

_SAVE_FAILED = "상담 신청을 저장하지 못했어요"
_RECEIPT_FAILED = "접수번호를 만들지 못했어요"


class OutboxError(Exception):
    """쓰기 실패·번호 재시도 소진. 메시지는 고정 문구(경로·내용을 넣지 않음)."""


def _dir(directory):
    # 호출 시점의 OUTBOX_DIR를 읽어야 평가·테스트가 모듈 변수를 바꿔 쓸 수 있다.
    return Path(directory) if directory is not None else Path(OUTBOX_DIR)


def _now(now):
    if now is None:
        return datetime.now(question_store.KST)
    if now.tzinfo is None:
        return now.replace(tzinfo=question_store.KST)
    return now


def _private_file(path, flags):
    return os.open(path, flags, 0o600)


def _read(path):
    """JSON 객체를 돌려준다. 읽을 수 없거나 객체가 아니면 None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _created_at(data):
    try:
        created = datetime.fromisoformat(data["created_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=question_store.KST)
    return created


def write(render, *, make_receipt, now=None, directory=None) -> dict:
    """purge_expired(now) 후, make_receipt()로 번호를 만들고 render(번호)로 Outbox_File 내용을 만들어
    `<번호>.json`에 배타 생성으로 쓴다. FileExistsError면 최대 RECEIPT_RETRIES번 다시 시도한다. 쓴 내용을 돌려준다.
    디렉터리는 없으면 만든다(mode 0o700), 파일은 0o600. OSError는 OutboxError로 바꾼다."""
    folder = _dir(directory)
    try:
        purge_expired(now=now, directory=folder)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise OutboxError(_SAVE_FAILED) from None

    for _ in range(RECEIPT_RETRIES):
        receipt = make_receipt()
        path = folder / f"{receipt}.json"
        try:
            f = open(path, "x", encoding="utf-8", opener=_private_file)
        except FileExistsError:
            continue
        except OSError:
            raise OutboxError(_SAVE_FAILED) from None
        try:
            with f:
                content = render(receipt)
                json.dump(content, f, ensure_ascii=False, indent=2)
        except (OSError, TypeError, ValueError):
            try:
                path.unlink()
            except OSError:
                pass
            raise OutboxError(_SAVE_FAILED) from None
        return content
    raise OutboxError(_RECEIPT_FAILED)


def purge_expired(now=None, hours=None, directory=None) -> int:
    """created_at이 now − hours보다 이른 파일을 지운다. JSON을 읽을 수 없는 파일은 mtime으로 판단한다. 지운 수를 돌려준다."""
    folder = _dir(directory)
    if not folder.is_dir():
        return 0
    cutoff = _now(now) - timedelta(hours=RETENTION_HOURS if hours is None else hours)
    removed = 0
    for path in folder.glob("*.json"):
        data = _read(path)
        created = _created_at(data) if data is not None else None
        try:
            if created is None:
                expired = path.stat().st_mtime < cutoff.timestamp()
            else:
                expired = created < cutoff
            if expired:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def count_for_session(session_id, directory=None) -> int:
    """session_id가 같은 Outbox_File 수. 읽을 수 없는 파일은 세지 않는다."""
    folder = _dir(directory)
    if not folder.is_dir():
        return 0
    count = 0
    for path in folder.glob("*.json"):
        data = _read(path)
        if data is not None and data.get("session_id") == session_id:
            count += 1
    return count
