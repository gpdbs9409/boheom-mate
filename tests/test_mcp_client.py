"""MCP 클라이언트: 허용 도구만 부르는지, 보낼 곳 고정, 도구·입력 칸 이름 맞추기, 토큰 비노출, 실패 처리,
--check(보내지 않음), 보험메이트 MCP 서버를 가짜·실제 stdio 서버로 확인한다."""
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


def fake(label, env, mode="normal"):
    return {"label": label, "env": env,
            "connection": lambda: {"transport": "stdio", "command": sys.executable, "args": [FAKE],
                                   "env": {**os.environ, "FAKE_MCP_LOG": str(LOG), "FAKE_MCP_MODE": mode}}}


def calls():
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []


for k in ("GITHUB_MCP_TOKEN", "GITHUB_ISSUE_REPO", "SLACK_MCP_USER_TOKEN", "SLACK_HANDOFF_CHANNEL_ID"):
    os.environ.pop(k, None)
passed = 0

# 1. .env가 비어 있으면 아무 행동도 켜지지 않는다. 두 서버 모두 등록은 되어 있다
assert set(mcp_client.SERVERS) == {"github", "slack"} and mcp_client.available_actions() == []
passed += 1

os.environ.update({"GITHUB_MCP_TOKEN": "ghp_SECRET_TOKEN", "GITHUB_ISSUE_REPO": " gpdbs9409/boheom-mate ",
                   "SLACK_MCP_USER_TOKEN": "xoxp-SECRET", "SLACK_HANDOFF_CHANNEL_ID": "C0FIXED"})
GH = ["GITHUB_MCP_TOKEN", "GITHUB_ISSUE_REPO"]
SL = ["SLACK_MCP_USER_TOKEN", "SLACK_HANDOFF_CHANNEL_ID"]
servers = {"github": fake("GitHub", GH), "slack": fake("Slack", SL)}
assert mcp_client.available_actions(servers) == ["report_issue", "handoff"]

# 2. GitHub 신고: 저장소는 환경변수(앞뒤 공백 제거), 라벨 없음, 제목·본문만
draft = outbound.issue_draft("감기 통원 보상돼요? 010-1234-5678", "네, 보상돼요.", [
    {"section": "보통약관", "article_no": "제3조", "title": "보장종목별 보상내용"}], "KB.pdf", "금액이 틀림")
assert "010-1234-5678" not in draft["body"]
mcp_client.execute("report_issue", draft, servers)
c = calls()[-1]
assert c["tool"] == "issue_write" and c["args"]["owner"] == "gpdbs9409" and c["args"]["repo"] == "boheom-mate", c
assert c["args"]["method"] == "create" and "보통약관 제3조" in c["args"]["body"]
passed += 1

# 3. Slack: 채널은 환경변수 값, 입력 칸 이름(channel_id/message)에 맞춰 보낸다
mcp_client.execute("handoff", outbound.handoff_draft("입원비 얼마?", ["가입 세대: 3세대"], "KB.pdf"), servers)
c = calls()[-1]
assert c == {"tool": "slack_send_message", "args": {"channel_id": "C0FIXED", "message": c["args"]["message"]}}
assert "3세대" in c["args"]["message"]
passed += 1

# 4. 도구·입력 칸 이름이 달라도(send_message, channel/text) 후보에서 찾아 보낸다
alt = {"slack": fake("Slack", SL, "alt")}
mcp_client.execute("handoff", {"text": "안녕"}, alt)
assert calls()[-1] == {"tool": "send_message", "args": {"channel": "C0FIXED", "text": "안녕"}}
passed += 1

# 5. 서버에 위험한 도구만 있으면 아무것도 부르지 않는다(허용 후보 밖 도구는 쓰지 않음)
before = len(calls())
try:
    mcp_client.execute("report_issue", draft, {"github": fake("GitHub", GH, "danger")})
    raise AssertionError("보내기 도구가 없는데 실행됨")
except mcp_client.MCPError as e:
    assert "도구가 없습니다" in str(e)
assert len(calls()) == before
passed += 1

# 6. 연결 실패: 고정 문구만, 토큰·주소가 메시지에 없다
broken = {"github": {"label": "GitHub", "env": GH, "connection": lambda: {
    "transport": "streamable_http", "url": "http://127.0.0.1:9/mcp",
    "headers": {"Authorization": f"Bearer {os.environ['GITHUB_MCP_TOKEN']}"}, "timeout": 2}}}
try:
    mcp_client.execute("report_issue", draft, broken)
    raise AssertionError
except mcp_client.MCPError as e:
    assert "SECRET" not in str(e) and "127.0.0.1" not in str(e), str(e)
passed += 1

# 7. --check: 준비됨/실패/꺼짐을 알려 주고, 아무것도 보내지 않는다
before = len(calls())
report = dict(mcp_client.check({"github": fake("GitHub", GH, "danger"), "slack": fake("Slack", SL)}))
assert report["handoff"].startswith("준비됨") and "slack_send_message" in report["handoff"], report
assert report["report_issue"].startswith("실패") and "delete_repository" in report["report_issue"], report
assert len(calls()) == before
os.environ.pop("SLACK_MCP_USER_TOKEN")
assert "꺼짐" in dict(mcp_client.check(servers))["handoff"]
assert mcp_client.available_actions(servers) == ["report_issue"]
passed += 1

# 8. 미리보기 수정 → 초안 왕복, 고친 글도 다시 가린다
back = outbound.from_preview("report_issue", outbound.preview_text("report_issue", draft) + "\n연락 a@b.com")
assert back["title"] == draft["title"] and "[이메일 가림]" in back["body"]
assert outbound.scrub("900101-1234567") == "[주민번호 가림]"
passed += 1

# 9. 보험메이트 MCP 서버: 도구 3개, 상품 목록 도구가 실제로 답한다(stdio)
conn = {"boheom": {"transport": "stdio", "command": sys.executable, "args": ["-m", "boheom_mate.mcp_server"],
                   "cwd": str(ROOT)}}


async def own():
    tools = {t.name: t for t in await MultiServerMCPClient(conn).get_tools()}
    return set(tools), await tools["list_products"].ainvoke({})


names, products = asyncio.run(own())
assert names == {"list_products", "search_terms", "calculate_payout"}, names
assert "KB_실손의료비보장보험_26.09_약관.pdf" in str(products), products
passed += 1

print(f"{passed}/9 통과")
