"""채팅 에이전트의 prepare_action: MCP 설정이 없으면 도구·프롬프트가 예전과 같고,
있으면 미리보기만 만들 뿐 아무것도 보내지 않는지 확인한다(LLM 호출 없음)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boheom_mate.ui import chat  # noqa: E402

# 설정이 없을 때: 프롬프트에 [외부로 보내기]가 없다
assert "[외부로 보내기]" not in chat._system_prompt("x", True)
assert "[외부로 보내기]" in chat._system_prompt("x", True, ["report_issue"])

# 도구 스키마는 쓸 수 있는 행동만 enum으로 준다
tool = chat._action_tool(["report_issue"])
assert tool["function"]["parameters"]["properties"]["action"]["enum"] == ["report_issue"]

# 실행: 미리보기 요청만 기록하고 보냈다고 말하지 않는다
turn = {"pending_action": None}
out = chat._run_prepare({"action": "report_issue", "reason": "금액이 달라요"}, ["report_issue"], turn)
assert turn["pending_action"] == {"action": "report_issue", "reason": "금액이 달라요"}
assert out["prepared"] and "아직 보내지 않았다" in out["note"]

# 쓸 수 없는 행동(꺼 둔 Slack 등)은 거절한다
try:
    chat._run_prepare({"action": "handoff"}, ["report_issue"], {"pending_action": None})
    raise AssertionError
except ValueError:
    pass
print("OK")
