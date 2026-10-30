"""MCP 클라이언트: 허용 목록, 고정된 보낼 곳, 토큰 비노출, 실패 처리, 보험메이트 MCP 서버를 가짜·실제 stdio 서버로 확인한다."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from boheom_mate.core import outbound  # noqa: E402
from boheom_mate.integrations import mcp_client  # noqa: E402

LOG = Path(tempfile.mkdtemp()) / "calls.jsonl"
FAKE = str(ROOT / "tests" / "fixtures" / "fake_mcp_server.py")


def fake_server(label, env, allowed):
    return {"label": label, "env": env, "allowed_tools": allowed,
            "connection": lambda: {"transport": "stdio", "command": sys.executable, "args": [FAKE],
                                   "env": {**os.environ, "FAKE_MCP_LOG": str(LOG)}}}


def calls():
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []


os.environ.update({"GITHUB_MCP_TOKEN": "ghp_SECRET_TOKEN", "GITHUB_ISSUE_REPO": "gpdbs9409/boheom-mate",
                   "SLACK_MCP_USER_TOKEN": "xoxp-SECRET", "SLACK_HANDOFF_CHANNEL_ID": "C0FIXED"})
servers = {"github": fake_server("GitHub", ["GITHUB_MCP_TOKEN", "GITHUB_ISSUE_REPO"], {"issue_write"}),
           "slack": fake_server("Slack", ["SLACK_MCP_USER_TOKEN", "SLACK_HANDOFF_CHANNEL_ID"], {mcp_client.SLACK_SEND_TOOL})}
passed = 0

# 1. 기본 설정: Slack은 주석 처리되어 있어 행동 목록에 없다
assert "slack" not in mcp_client.SERVERS and "handoff" not in mcp_client.available_actions(), mcp_client.available_actions()
passed += 1

# 2. GitHub 신고: 저장소·라벨은 환경변수로 고정, 초안 제목·본문만 들어간다
draft = outbound.issue_draft("감기 통원 보상돼요? 010-1234-5678", "네, 보상돼요.", [
    {"section": "보통약관", "article_no": "제3조", "title": "보장종목별 보상내용"}], "KB.pdf", "금액이 틀림")
assert "[전화번호 가림]" in draft["body"] and "010-1234-5678" not in draft["body"]
mcp_client.execute("report_issue", draft, servers)
c = calls()[-1]
assert c["tool"] == "issue_write" and c["args"]["owner"] == "gpdbs9409" and c["args"]["repo"] == "boheom-mate", c
assert c["args"]["method"] == "create" and c["args"]["labels"] == ["답변오류"] and "보통약관 제3조" in c["args"]["body"]
passed += 1

# 3. Slack 상담 요청: 채널은 환경변수 값으로 고정
mcp_client.execute("handoff", outbound.handoff_draft("입원비 얼마?", ["가입 세대: 3세대"], "KB.pdf"), servers)
c = calls()[-1]
assert c["tool"] == mcp_client.SLACK_SEND_TOOL and c["args"]["channel_id"] == "C0FIXED" and "3세대" in c["args"]["message"]
passed += 1

# 4. 허용 목록 밖 도구는 서버에 있어도 부르지 않는다
before = len(calls())
try:
    mcp_client.call_tool("github", "delete_repository", {"owner": "a", "repo": "b"}, servers)
    raise AssertionError("허용 안 된 도구가 실행됨")
except mcp_client.MCPError as e:
    assert "허용되지 않은" in str(e)
assert len(calls()) == before
passed += 1

# 5. 서버에 없는 도구 이름(허용 목록에는 있음)이면 보내지 않는다
bad = {"github": fake_server("GitHub", ["GITHUB_MCP_TOKEN", "GITHUB_ISSUE_REPO"], {"no_such_tool"})}
try:
    mcp_client.call_tool("github", "no_such_tool", {}, bad)
    raise AssertionError
except mcp_client.MCPError as e:
    assert "도구가 없음" in str(e)
passed += 1

# 6. 연결 실패: 고정 문구만, 토큰이 메시지에 없다
broken = {"github": {**servers["github"], "connection": lambda: {
    "transport": "streamable_http", "url": "http://127.0.0.1:9/mcp",
    "headers": {"Authorization": f"Bearer {os.environ['GITHUB_MCP_TOKEN']}"}, "timeout": 2}}}
try:
    mcp_client.execute("report_issue", draft, broken)
    raise AssertionError
except mcp_client.MCPError as e:
    assert "SECRET" not in str(e) and "127.0.0.1" not in str(e), str(e)
passed += 1

# 7. 설정(.env)이 없으면 보내지 않는다
os.environ.pop("GITHUB_MCP_TOKEN")
assert "report_issue" not in mcp_client.available_actions(servers)
try:
    mcp_client.execute("report_issue", draft, servers)
    raise AssertionError
except mcp_client.MCPError:
    pass
passed += 1

# 8. 미리보기 수정 → 초안 왕복, 고친 글도 다시 가린다
text = outbound.preview_text("report_issue", draft) + "\n연락 주세요 a@b.com"
back = outbound.from_preview("report_issue", text)
assert back["title"] == draft["title"] and "[이메일 가림]" in back["body"]
assert outbound.scrub("900101-1234567") == "[주민번호 가림]"
passed += 1

# 9. 보험메이트 MCP 서버: 도구 3개가 보이고, 목록 도구가 실제로 답한다(stdio)
own = {"boheom": {"label": "보험메이트", "env": [], "allowed_tools": {"list_products"},
                  "connection": lambda: {"transport": "stdio", "command": sys.executable,
                                         "args": ["-m", "boheom_mate.mcp_server"], "cwd": str(ROOT)}}}
import asyncio  # noqa: E402
from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402
names = {t.name for t in asyncio.run(MultiServerMCPClient({"boheom": own["boheom"]["connection"]()}).get_tools())}
assert names == {"list_products", "search_terms", "calculate_payout"}, names
products = mcp_client.call_tool("boheom", "list_products", {}, own)
assert "KB_실손의료비보장보험_26.09_약관.pdf" in str(products), products
passed += 1

print(f"{passed}/9 통과")
