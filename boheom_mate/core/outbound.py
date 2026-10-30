"""밖으로 보낼 글(GitHub 이슈, Slack 상담 요청) 초안을 만든다. 외부 호출은 하지 않는다.

초안은 화면에서 사용자가 읽고 고친 뒤에만 보낸다(integrations.mcp_client.execute).
연락처·주민번호처럼 눈에 띄는 개인정보는 여기서 가린다. 병명은 상담에 필요할 수 있어 가리지 않고,
화면에서 "병명 등 민감정보는 지워 주세요" 안내를 띄운다.
"""
import re

MAX_SENDS_PER_SESSION = 3
FOOTER = "\n\n_보험메이트 사용자가 내용을 확인하고 보낸 글입니다._"

_PATTERNS = [
    (re.compile(r"\d{6}\s*-\s*[1-8]\d{6}"), "[주민번호 가림]"),
    (re.compile(r"01[016789][\s.-]?\d{3,4}[\s.-]?\d{4}"), "[전화번호 가림]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[이메일 가림]"),
]


def scrub(text):
    """주민번호·휴대전화·이메일을 가린다."""
    for pattern, mask in _PATTERNS:
        text = pattern.sub(mask, text or "")
    return text


def _labels(evidence):
    return [f'{e.get("section", "")} {e.get("article_no", "")} {e.get("title", "")}'.strip() for e in evidence or []]


def issue_draft(question, answer, evidence, source, reason=""):
    """답변 오류 신고용 GitHub 이슈 초안: {"title", "body"}."""
    q = scrub(question).strip()
    body = [
        f"**상품**: {source}",
        f"**질문**: {q}",
        f"**답변**:\n{scrub(answer).strip()}",
        "**근거 조항**: " + (", ".join(_labels(evidence)) or "없음"),
    ]
    if reason:
        body.append(f"**신고 이유**: {scrub(reason).strip()}")
    return {"title": f"[답변 오류 신고] {q[:40]}", "body": "\n\n".join(body) + FOOTER}


def handoff_draft(question, remembered, source, reason=""):
    """설계사 상담 요청용 Slack 메시지 초안: {"text"}. remembered는 memory.describe 결과(줄 목록)."""
    lines = [f"*상담 요청* ({source})", f"질문: {scrub(question).strip()}"]
    if remembered:
        lines.append("대화에서 확인한 정보:\n" + "\n".join(f"• {scrub(r)}" for r in remembered))
    if reason:
        lines.append(f"요청 내용: {scrub(reason).strip()}")
    return {"text": "\n".join(lines) + FOOTER}


def preview_text(action_name, draft):
    """화면 미리보기·수정 칸에 넣을 글."""
    if action_name == "report_issue":
        return f'{draft["title"]}\n\n{draft["body"]}'
    return draft["text"]


def from_preview(action_name, text):
    """사용자가 고친 미리보기 글을 다시 초안으로. 고친 글도 한 번 더 가린다."""
    text = scrub(text).strip()
    if action_name == "report_issue":
        title, _, body = text.partition("\n")
        return {"title": title.strip()[:120] or "[답변 오류 신고]", "body": body.strip()}
    return {"text": text}
