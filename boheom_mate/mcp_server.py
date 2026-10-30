"""보험메이트 MCP 서버: 약관 검색·보험금 계산 도구를 다른 MCP 클라이언트(Kiro, Claude Desktop 등)에 내보낸다.

읽기 전용 도구만 둔다(저장·전송·삭제 없음). 금액은 앱과 똑같이 calculator.py가 약관에서 검증한 값으로 계산한다.
실행(stdio): .venv/bin/python -m boheom_mate.mcp_server
"""
from mcp.server.fastmcp import FastMCP

from boheom_mate.core import calculator, policy_rules, rag

DEFAULT_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"
DISCLAIMER = "참고용 안내이며 실제 지급 여부와 금액은 보험사 심사로 확정됩니다."

mcp = FastMCP("boheom-mate")


@mcp.tool()
def list_products() -> list[str]:
    """상담할 수 있는 약관(상품) 파일 이름 목록."""
    return rag.list_sources()


@mcp.tool()
def search_terms(question: str, source: str = DEFAULT_SOURCE) -> dict:
    """실손보험 약관에서 관련 조항을 찾아 근거와 함께 답한다. 근거가 없으면 found=false."""
    result = rag.answer(question, source)
    return {
        "found": result["found"],
        "answer": result["answer"],
        "evidence": [{"article": f'{e["section"]} {e["article_no"]} {e["title"]}', "highlights": e.get("highlights", [])}
                     for e in result["evidence"]],
        "disclaimer": DISCLAIMER,
    }


@mcp.tool()
def calculate_payout(care: str, hospital: str, covered_copay: int = 0, non_covered: int = 0,
                     visits: int = 1, source: str = DEFAULT_SOURCE) -> dict:
    """영수증 금액으로 예상 실손 보험금을 계산한다. care: 통원/입원, hospital: 의원/종합병원.
    약관에서 계산 기준을 검증하지 못한 상품이면 error를 돌려준다."""
    rules = policy_rules.load(source)
    if rules is None:
        return {"error": "이 상품은 약관에서 계산 기준을 확인하지 못해 계산할 수 없습니다."}
    result = calculator.calculate(care=care, hospital=hospital, covered_copay=covered_copay,
                                  non_covered=non_covered, visits=visits,
                                  rules=rules["values"], articles=rules["articles"])
    return {"total": result["total"], "items": result["items"], "notes": result["notes"], "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    mcp.run()
