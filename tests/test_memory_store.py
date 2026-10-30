"""상담 메모 저장소: 저장·지우기·하루 지난 메모 자동 삭제를 확인한다."""
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boheom_mate.store import question_store  # noqa: E402

question_store.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"

question_store.save_memory("a", {"joined_year": 2018})
question_store.save_memory("b", {"joined_year": 2022})
assert question_store.get_memory("a") == {"joined_year": 2018}

question_store.clear_memory("a")  # "새 대화"·"기억 지우기"
assert question_store.get_memory("a") is None
assert question_store.get_memory("b") is not None

# 23시간 뒤에는 남고, 25시간 뒤에는 지워진다
now = question_store._now()
assert question_store.purge_old_memory(now=now + timedelta(hours=23)) == 0
assert question_store.purge_old_memory(now=now + timedelta(hours=25)) == 1
assert question_store.get_memory("b") is None
print("OK")
