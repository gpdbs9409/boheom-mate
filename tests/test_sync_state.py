"""주간 확인 상태 기록 테스트.

실행: .venv/bin/python tests/test_sync_state.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync_state
from sync_state import key

# 판 표시와 띄어쓰기가 달라도 같은 약관
assert key("A", "KB 실손(26.09)", "26.09") == key("A", "KB실손", "26.09")
assert key("A", "KB실손", "26.09") != key("A", "KB실손", "27.01")

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "state" / "sync.json"
    # 파일이 없으면 이미 사이트에 있는 약관을 아는 것으로 시작한다
    state = sync_state.load(path)
    assert key("KB손해보험", "KB손보 실손의료비보장보험(무배당)", "26.09") in state["known"]
    assert state["pending"] == []

    state["pending"].append({"key": "k", "status": "pending", "slack_ts": None})
    sync_state.save(state, path)
    assert sync_state.load(path) == state

    log = Path(d) / "runs.log"
    sync_state.log({"event": "a"}, path=log)
    sync_state.log({"event": "b"}, path=log)
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["event"] == "b" and "at" in json.loads(lines[0])

print("OK")
