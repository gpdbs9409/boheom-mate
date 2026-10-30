"""MCP 클라이언트: 외부 MCP 서버(GitHub, Slack)의 도구를 코드가 대신 호출한다.

안전 규칙(로그인 없는 방문자가 쓰는 앱이라 운영자 계정 권한을 함부로 쓰지 않게 한다):
- 서버마다 허용 목록(ALLOWED_TOOLS)에 있는 도구 하나만 부른다. 서버가 다른 도구를 줘도 쓰지 않는다.
- LLM에게 MCP 도구를 직접 bind하지 않는다. LLM은 "보낼까요?" 초안만 만들고(chat.prepare_action),
  사용자가 화면에서 미리보기를 확인하고 버튼을 눌렀을 때만 여기 execute()가 실제로 보낸다.
- 보낼 곳(GitHub 저장소, Slack 채널)은 환경변수로 고정한다. LLM 인자로 받지 않는다.
- 토큰은 .env에서만 읽고, 오류 메시지·로그·화면에 넣지 않는다.

Streamlit은 동기 코드라서 MCP(비동기) 호출을 asyncio.run으로 감싼다.
"""
import asyncio
import os

from langchain_mcp_adapters.client import MultiServerMCPClient

TIMEOUT_SECONDS = 15


class MCPError(Exception):
    """연결 실패·시간 초과·허용 안 된 도구·도구 오류. 메시지는 고정 문구만 쓴다(토큰·URL을 넣지 않는다)."""


# ---------- GitHub: 답변 오류 신고 → 이슈 만들기 ----------
# 공식 서버 https://github.com/github/github-mcp-server (원격, PAT 인증)
# 이슈 만들기 도구는 issue_write(method="create"). 저장소 읽기·코드 수정 도구는 쓰지 않는다.
def _github_connection():
    return {
        "transport": "streamable_http",
        "url": os.getenv("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/"),
        "headers": {"Authorization": f"Bearer {os.environ['GITHUB_MCP_TOKEN']}"},
        "timeout": TIMEOUT_SECONDS,
    }


def _github_args(draft):
    owner, repo = os.environ["GITHUB_ISSUE_REPO"].split("/", 1)
    return {"method": "create", "owner": owner, "repo": repo,
            "title": draft["title"], "body": draft["body"], "labels": ["답변오류"]}


GITHUB_SERVER = {
    "label": "GitHub",
    "env": ["GITHUB_MCP_TOKEN", "GITHUB_ISSUE_REPO"],
    "connection": _github_connection,
    "allowed_tools": {"issue_write"},
}


# ---------- Slack: 설계사에게 상담 요청 전달 ----------
# 공식 서버 https://mcp.slack.com/mcp (Streamable HTTP, OAuth 사용자 토큰 xoxp, chat:write 스코프 필요)
# https://docs.slack.dev/ai/slack-mcp-server/
# 제약: Slack 앱이 내부 앱(또는 마켓플레이스 앱)으로 등록되어 MCP 기능을 켜야 하고, 봇 토큰(xoxb)이 아닌
# 사용자 토큰이 필요하다. 지금 운영 Slack은 주간 약관 확인용 봇 토큰만 있어 이 조건을 못 맞춘다.
# 메시지 보내기 도구 이름은 문서에 표로만 나와 있어(“Send message”) 아래 이름은 가정이며,
# 연결할 때 서버의 실제 도구 목록에 없으면 보내지 않는다.
SLACK_SEND_TOOL = "slack_send_message"


def _slack_connection():
    return {
        "transport": "streamable_http",
        "url": "https://mcp.slack.com/mcp",
        "headers": {"Authorization": f"Bearer {os.environ['SLACK_MCP_USER_TOKEN']}"},
        "timeout": TIMEOUT_SECONDS,
    }


def _slack_args(draft):
    return {"channel_id": os.environ["SLACK_HANDOFF_CHANNEL_ID"], "message": draft["text"]}


SLACK_SERVER = {
    "label": "Slack",
    "env": ["SLACK_MCP_USER_TOKEN", "SLACK_HANDOFF_CHANNEL_ID"],
    "connection": _slack_connection,
    "allowed_tools": {SLACK_SEND_TOOL},
}


SERVERS = {
    "github": GITHUB_SERVER,
    # "slack": SLACK_SERVER,  # 사용 안 함: Slack MCP는 사용자 토큰(xoxp)과 MCP를 켠 내부 앱이 필요하다(위 제약).
    #                           준비되면 이 줄의 주석을 풀고 .env에 SLACK_MCP_USER_TOKEN, SLACK_HANDOFF_CHANNEL_ID를 넣는다.
}

# 행동 → (서버, 도구, 인자 만드는 함수). 인자는 코드가 고정 값으로 만든다.
ACTIONS = {
    "report_issue": {"server": "github", "tool": "issue_write", "args": _github_args,
                     "title": "답변 오류 신고(GitHub 이슈)"},
    "handoff": {"server": "slack", "tool": SLACK_SEND_TOOL, "args": _slack_args,
                "title": "설계사에게 상담 요청(Slack)"},
}


def _server_ready(name, servers):
    server = servers.get(name)
    return bool(server) and all(os.getenv(key) for key in server["env"])


def available_actions(servers=None):
    """지금 설정으로 쓸 수 있는 행동 이름 목록. 연결은 하지 않는다(화면을 그릴 때마다 부르므로)."""
    servers = SERVERS if servers is None else servers
    return [name for name, action in ACTIONS.items() if _server_ready(action["server"], servers)]


async def _call(connection, server_name, tool_name, allowed, args):
    if tool_name not in allowed:
        raise MCPError(f"허용되지 않은 도구: {tool_name}")
    client = MultiServerMCPClient({server_name: connection}, handle_tool_errors=False)
    tools = await client.get_tools(server_name=server_name)
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        raise MCPError(f"서버에 도구가 없음: {tool_name}")
    return await tool.ainvoke(args)


def call_tool(server_name, tool_name, args, servers=None):
    """허용 목록에 있는 도구 하나를 부른다. 실패하면 MCPError(고정 문구)."""
    servers = SERVERS if servers is None else servers
    server = servers.get(server_name)
    if not server:
        raise MCPError(f"설정되지 않은 서버: {server_name}")
    try:
        connection = server["connection"]()
        return asyncio.run(asyncio.wait_for(
            _call(connection, server_name, tool_name, server["allowed_tools"], args), TIMEOUT_SECONDS))
    except MCPError:
        raise
    except KeyError:
        raise MCPError(f"{server['label']} 설정(.env)이 없습니다") from None
    except (asyncio.TimeoutError, TimeoutError):
        raise MCPError(f"{server['label']} 응답 시간 초과") from None
    except Exception:  # 예외 문자열에 URL·헤더가 들어갈 수 있어 그대로 보여 주지 않는다
        raise MCPError(f"{server['label']} 호출에 실패했습니다") from None


def execute(action_name, draft, servers=None):
    """사용자가 화면에서 확인한 초안을 보낸다. 반환값: 서버 응답(문자열 또는 목록)."""
    servers = SERVERS if servers is None else servers
    action = ACTIONS[action_name]
    if not _server_ready(action["server"], servers):
        raise MCPError(f"{action['title']}: 설정이 없어 보낼 수 없습니다")
    return call_tool(action["server"], action["tool"], action["args"](draft), servers)
