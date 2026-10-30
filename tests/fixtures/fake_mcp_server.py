"""테스트용 가짜 MCP 서버(stdio). 받은 호출을 FAKE_MCP_LOG 파일에 JSON 줄로 적는다.
FAKE_MCP_MODE: normal(공식 서버와 같은 이름) / alt(다른 도구·입력 칸 이름) / danger(위험한 도구만)."""
import json
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")
MODE = os.environ.get("FAKE_MCP_MODE", "normal")


def _log(name, args):
    with open(os.environ["FAKE_MCP_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": name, "args": args}, ensure_ascii=False) + "\n")


@mcp.tool()
def delete_repository(owner: str, repo: str) -> str:
    _log("delete_repository", {"owner": owner, "repo": repo})
    return "deleted"


if MODE == "normal":
    @mcp.tool()
    def issue_write(method: str, owner: str, repo: str, title: str = "", body: str = "") -> str:
        _log("issue_write", {"method": method, "owner": owner, "repo": repo, "title": title, "body": body})
        return "created issue #1"

    @mcp.tool()
    def slack_send_message(channel_id: str, message: str) -> str:
        _log("slack_send_message", {"channel_id": channel_id, "message": message})
        return "sent"

if MODE == "alt":
    @mcp.tool()
    def send_message(channel: str, text: str) -> str:
        _log("send_message", {"channel": channel, "text": text})
        return "sent"


if __name__ == "__main__":
    mcp.run()
