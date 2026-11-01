"""MCP 클라이언트(Slack 상담 요청): 허용 도구만 부르는지, 채널 고정, 도구·입력 칸 이름 맞추기, 토큰 비노출,
실패 처리, --check(보내지 않음), 보험메이트 MCP 서버를 가짜·실제 stdio 서버로 확인한다."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from boheom_mate.core import outbound  # noqa: E402
from boheom_mate.integrations import mcp_client  # noqa: E402

LOG = Path(tempfile.mkdtemp()) / "calls.jsonl"
FAKE = str(ROOT / "tests" / "fixtures" / "fake_mcp_server.py")
SL = ["SLACK_MCP_USER_TOKEN", "SLACK_HANDOFF_CHANNEL_ID"]


def fake(mode="normal"):
    return {"label": "Slack", "env": SL,
            "connection": lambda: {"transport": "stdio", "command": sys.executable, "args": [FAKE],
                                   "env": {**os.environ, "FAKE_MCP_LOG": str(LOG), "FAKE_MCP_MODE": mode}}}


def calls():
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []


for k in SL:
    os.environ.pop(k, None)
passed = 0

# 1. .env가 비어 있으면 켜지지 않는다. 등록된 서버는 Slack 하나뿐이다(GitHub 신고는 없음)
assert set(mcp_client.SERVERS) == {"slack"} and set(mcp_client.ACTIONS) == {"handoff"}
assert mcp_client.available_actions() == []
passed += 1

os.environ.update({"SLACK_MCP_USER_TOKEN": "xoxp-SECRET", "SLACK_HANDOFF_CHANNEL_ID": " C0FIXED "})
assert mcp_client.available_actions({"slack": fake()}) == ["handoff"]

# 2. 상담 요청: 채널은 환경변수 값(공백 제거), 입력 칸 이름(channel_id/message)에 맞춰 보낸다. 연락처는 가린다
draft = outbound.handoff_draft("입원비 얼마? 010-1234-5678", ["가입 세대: 3세대"], "KB.pdf")
assert "010-1234-5678" not in draft["text"]
mcp_client.execute("handoff", draft, {"slack": fake()})
c = calls()[-1]
assert c["tool"] == "slack_send_message" and c["args"]["channel_id"] == "C0FIXED" and "3세대" in c["args"]["message"], c
passed += 1

# 3. 도구·입력 칸 이름이 달라도(send_message, channel/text) 후보에서 찾아 보낸다
mcp_client.execute("handoff", {"text": "안녕"}, {"slack": fake("alt")})
assert calls()[-1] == {"tool": "send_message", "args": {"channel": "C0FIXED", "text": "안녕"}}
passed += 1

# 4. 서버에 위험한 도구만 있으면 아무것도 부르지 않는다
before = len(calls())
try:
    mcp_client.execute("handoff", draft, {"slack": fake("danger")})
    raise AssertionError("보내기 도구가 없는데 실행됨")
except mcp_client.MCPError as e:
    assert "도구가 없습니다" in str(e)
assert len(calls()) == before
passed += 1

# 5. 연결 실패: 고정 문구만, 토큰·주소가 메시지에 없다
broken = {"slack": {"label": "Slack", "env": SL, "connection": lambda: {
    "transport": "streamable_http", "url": "http://127.0.0.1:9/mcp",
    "headers": {"Authorization": f"Bearer {os.environ['SLACK_MCP_USER_TOKEN']}"}, "timeout": 2}}}
try:
    mcp_client.execute("handoff", draft, broken)
    raise AssertionError
except mcp_client.MCPError as e:
    assert "SECRET" not in str(e) and "127.0.0.1" not in str(e), str(e)
passed += 1

# 6. --check: 준비됨/실패/꺼짐을 알려 주고, 아무것도 보내지 않는다
before = len(calls())
assert dict(mcp_client.check({"slack": fake()}))["handoff"].startswith("준비됨")
assert "delete_repository" in dict(mcp_client.check({"slack": fake("danger")}))["handoff"]
assert len(calls()) == before
os.environ.pop("SLACK_MCP_USER_TOKEN")
assert "꺼짐" in dict(mcp_client.check({"slack": fake()}))["handoff"]
assert mcp_client.available_actions({"slack": fake()}) == []
passed += 1

# 7. 미리보기 수정 → 초안, 고친 글도 다시 가린다
back = outbound.from_preview("handoff", outbound.preview_text("handoff", draft) + "\n연락 a@b.com 900101-1234567")
assert "[이메일 가림]" in back["text"] and "[주민번호 가림]" in back["text"]
passed += 1

# 8. 보험메이트 MCP 서버: 도구 3개, 상품 목록 도구가 실제로 답한다(stdio)
conn = {"boheom": {"transport": "stdio", "command": sys.executable, "args": ["-m", "boheom_mate.mcp_server"],
                   "cwd": str(ROOT)}}


async def own():
    tools = {t.name: t for t in await MultiServerMCPClient(conn).get_tools()}
    return set(tools), await tools["list_products"].ainvoke({})


names, products = asyncio.run(own())
assert names == {"list_products", "search_terms", "calculate_payout"}, names
assert "KB_실손의료비보장보험_26.09_약관.pdf" in str(products), products
passed += 1

print(f"{passed}/8 통과")
