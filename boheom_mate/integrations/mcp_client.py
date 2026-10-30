"""MCP 클라이언트: 외부 MCP 서버(GitHub, Slack)의 도구를 코드가 대신 호출한다.

켜는 법: .env에 토큰과 보낼 곳만 넣으면 된다(.env.example 참고). 넣은 서버만 켜지고, 없으면 기능이 숨는다.
넣은 뒤 확인(아무것도 보내지 않음): .venv/bin/python -m boheom_mate.integrations.mcp_client --check

안전 규칙(로그인 없는 방문자가 쓰는 앱이라 운영자 계정 권한을 함부로 쓰지 않게 한다):
- 행동마다 허용한 "메시지 보내기 / 이슈 만들기" 도구만 부른다. 서버가 다른 도구를 줘도 쓰지 않는다.
- LLM에게 MCP 도구를 직접 bind하지 않는다. LLM은 미리보기만 만들고(chat.prepare_action),
  사용자가 화면에서 확인하고 '보내기'를 눌렀을 때만 execute()가 보낸다.
- 보낼 곳(GitHub 저장소, Slack 채널)은 환경변수로 고정한다. LLM 인자로 받지 않는다.
- 토큰은 .env에서만 읽고, 오류 메시지·로그·화면에 넣지 않는다.

Streamlit은 동기 코드라서 MCP(비동기) 호출을 asyncio.run으로 감싼다.
"""
import asyncio
import os
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient

TIMEOUT_SECONDS = 15


class MCPError(Exception):
    """연결 실패·시간 초과·허용 안 된 도구·도구 오류. 메시지는 고정 문구만 쓴다(토큰·URL을 넣지 않는다)."""


def _http(url, token_env):
    return {"transport": "streamable_http", "url": url,
            "headers": {"Authorization": f"Bearer {os.environ[token_env]}"}, "timeout": TIMEOUT_SECONDS}


# ---------- 서버 ----------
# GitHub 공식 서버 https://github.com/github/github-mcp-server (원격, fine-grained PAT: 대상 저장소 Issues 쓰기)
# Slack 공식 서버 https://docs.slack.dev/ai/slack-mcp-server/ (Streamable HTTP, 사용자 토큰 xoxp, chat:write)
SERVERS = {
    "github": {
        "label": "GitHub",
        "env": ["GITHUB_MCP_TOKEN", "GITHUB_ISSUE_REPO"],
        "connection": lambda: _http(os.getenv("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/"), "GITHUB_MCP_TOKEN"),
    },
    "slack": {
        "label": "Slack",
        "env": ["SLACK_MCP_USER_TOKEN", "SLACK_HANDOFF_CHANNEL_ID"],
        "connection": lambda: _http(os.getenv("SLACK_MCP_URL", "https://mcp.slack.com/mcp"), "SLACK_MCP_USER_TOKEN"),
    },
}


def _github_values(draft):
    owner, repo = os.environ["GITHUB_ISSUE_REPO"].strip().split("/", 1)
    # 라벨은 넣지 않는다(저장소에 없는 라벨이면 실패할 수 있다). 제목 앞의 [답변 오류 신고]로 구분한다.
    return {"method": "create", "owner": owner, "repo": repo, "title": draft["title"], "body": draft["body"]}


def _slack_values(draft):
    return {"channel": os.environ["SLACK_HANDOFF_CHANNEL_ID"].strip(), "text": draft["text"]}


# 행동 → 서버, 부를 수 있는 도구 이름 후보(앞에서부터, 모두 '보내기/만들기' 하나의 일만 하는 도구),
#        값을 도구 입력 칸 이름에 맞추는 별칭. Slack은 도구·입력 칸 이름이 문서에 없어서 후보를 둔다.
ACTIONS = {
    "report_issue": {
        "server": "github", "title": "답변 오류 신고(GitHub 이슈)", "values": _github_values,
        "tools": ["issue_write", "create_issue"],
        "aliases": {},
    },
    "handoff": {
        "server": "slack", "title": "설계사에게 상담 요청(Slack)", "values": _slack_values,
        "tools": ["slack_send_message", "send_message", "chat_post_message", "post_message"],
        "aliases": {"channel": ["channel_id", "channel", "conversation_id"],
                    "text": ["message", "text", "content", "markdown_text"]},
    },
}


def _server_ready(name, servers):
    server = servers.get(name)
    return bool(server) and all((os.getenv(key) or "").strip() for key in server["env"])


def available_actions(servers=None):
    """지금 설정으로 쓸 수 있는 행동 이름 목록. 연결은 하지 않는다(화면을 그릴 때마다 부르므로)."""
    servers = SERVERS if servers is None else servers
    return [name for name, action in ACTIONS.items() if _server_ready(action["server"], servers)]


def _pick_tool(tools, candidates):
    by_name = {t.name: t for t in tools}
    return next((by_name[name] for name in candidates if name in by_name), None)


def _fit_args(values, aliases, params):
    """값을 도구의 실제 입력 칸 이름에 맞춘다. params가 비어 있으면(스키마 없음) 첫 별칭을 쓴다."""
    args = {}
    for key, value in values.items():
        names = aliases.get(key, [key])
        name = next((n for n in names if n in params), None) if params else names[0]
        if name is None:
            raise MCPError(f"도구 입력 칸을 찾지 못함: {key}")
        args[name] = value
    return args


async def _connect_tool(server_name, server, action):
    client = MultiServerMCPClient({server_name: server["connection"]()}, handle_tool_errors=False)
    tools = await client.get_tools(server_name=server_name)
    return tools, _pick_tool(tools, action["tools"])


def _run(coro, label):
    try:
        return asyncio.run(asyncio.wait_for(coro, TIMEOUT_SECONDS))
    except MCPError:
        raise
    except KeyError:
        raise MCPError(f"{label} 설정(.env)이 없습니다") from None
    except (asyncio.TimeoutError, TimeoutError):
        raise MCPError(f"{label} 응답 시간 초과") from None
    except Exception:  # 예외 문자열에 URL·헤더가 들어갈 수 있어 그대로 보여 주지 않는다
        raise MCPError(f"{label} 연결 또는 호출에 실패했습니다(토큰·권한을 확인하세요)") from None


def execute(action_name, draft, servers=None):
    """사용자가 화면에서 확인한 초안을 보낸다. 허용 도구 후보 밖의 도구는 절대 부르지 않는다."""
    servers = SERVERS if servers is None else servers
    action = ACTIONS[action_name]
    if not _server_ready(action["server"], servers):
        raise MCPError(f"{action['title']}: 설정이 없어 보낼 수 없습니다")
    server = servers[action["server"]]

    async def go():
        _, tool = await _connect_tool(action["server"], server, action)
        if tool is None:
            raise MCPError(f"{server['label']} 서버에 보내기 도구가 없습니다")
        args = _fit_args(action["values"](draft), action["aliases"], set(getattr(tool, "args", {}) or {}))
        return await tool.ainvoke(args)

    return _run(go(), server["label"])


def check(servers=None):
    """설정된 서버에 연결해 보내기 도구가 있는지만 확인한다(아무것도 보내지 않음). [(행동, 결과 문장)]"""
    servers = SERVERS if servers is None else servers
    report = []
    for name, action in ACTIONS.items():
        server = servers.get(action["server"])
        if not server:
            report.append((name, "서버가 등록되지 않음"))
            continue
        missing = [k for k in server["env"] if not (os.getenv(k) or "").strip()]
        if missing:
            report.append((name, f"꺼짐: .env에 {', '.join(missing)} 없음"))
            continue
        try:
            tools, tool = _run(_connect_tool(action["server"], server, action), server["label"])
        except MCPError as e:
            report.append((name, f"실패: {e}"))
            continue
        if tool is None:
            names = ", ".join(sorted(t.name for t in tools)[:30])
            report.append((name, f"실패: 보내기 도구를 못 찾음. 서버 도구 목록: {names}"))
            continue
        try:
            _fit_args(action["values"]({"title": "t", "body": "b", "text": "t"}), action["aliases"],
                      set(getattr(tool, "args", {}) or {}))
        except (MCPError, ValueError) as e:
            report.append((name, f"실패: {e}"))
            continue
        report.append((name, f"준비됨: 도구 {tool.name} 사용"))
    return report


if __name__ == "__main__" and "--check" in sys.argv:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    for action_name, line in check():
        print(f"{ACTIONS[action_name]['title']}: {line}")
