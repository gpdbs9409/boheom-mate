"""주간 약관 확인의 상태: 이미 아는 약관, 승인 대기 약관, 실행 기록.

서버의 state/ 폴더가 원본이다. 코드를 배포할 때 덮어쓰지 않는다.
"""
import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import products

STATE_DIR = Path(__file__).parent / "state"
STATE_FILE = STATE_DIR / "sync.json"
LOG_FILE = STATE_DIR / "runs.log"


def key(company, product, edition):
    """같은 약관인지 가리는 이름. 판 표시와 띄어쓰기는 무시한다."""
    name = "".join(products.base_name(product).split())
    return f"{company}|{name}|{edition}"


def load(path=STATE_FILE):
    """{"known": [키], "pending": [대기 항목]}. 처음에는 이미 사이트에 있는 약관을 아는 것으로 둔다."""
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    known = [key(e["company"], e["product"], e["edition"]) for e in products.load()]
    return {"known": known, "pending": []}


def save(state, path=STATE_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def log(event, path=LOG_FILE):
    """실행 기록에 JSON 한 줄을 덧붙인다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


class Busy(Exception):
    """다른 실행이 상태 파일을 쓰는 중."""


@contextmanager
def lock(state_path=STATE_FILE, wait=True):
    """주간 확인과 승인 반영이 동시에 상태 파일을 읽고 써서 서로 덮어쓰지 않게 한다.
    wait=False이면 이미 잠겨 있을 때 기다리지 않고 Busy를 낸다."""
    path = Path(state_path).parent / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise Busy() from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
