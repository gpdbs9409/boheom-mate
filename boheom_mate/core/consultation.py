"""상담 신청(이메일 mock)용 순수 함수: 이메일 검증, 접수번호, 요약 정리, 메일 제목·본문, Slack 알림 글.
파일·네트워크 I/O를 하지 않는다(Outbox 쓰기는 store/outbox.py, 알림은 integrations/mcp_client.py).
회신 이메일이 새지 않도록 print·logging을 쓰지 않는다.
"""
import os
import re
import secrets
from zoneinfo import ZoneInfo

from boheom_mate.core.outbound import scrub

KST = ZoneInfo("Asia/Seoul")

CONSULT_INBOX_DEFAULT = "consult@boheom-mate.example"
CONSULTATION_LIMIT = 3  # 세션당 접수 건수
MOCK_NOTICE = "실제 메일은 보내지 않는 데모입니다"
LIMIT_MESSAGE = "이 대화에서는 상담 신청을 3번까지 할 수 있어요"
MAX_SUMMARY_CHARS = 1000  # Scrubber 뒤 길이. 넘으면 자르고 "…"를 붙인다
MAX_PREFERRED_TIME_CHARS = 100
NOTICE_SUMMARY_CHARS = 80  # Slack 알림 요약 첫 줄 길이

# Email_Validator 기준. ASCII만 받는다(국제화 도메인·따옴표 로컬 파트는 받지 않음)
EMAIL_MAX_LENGTH = 254
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"  # 로컬 파트: 점으로 시작·끝·연속 금지
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}")  # 도메인: 라벨 1~63자, 최상위 2~24글자
LOCAL_MAX_LENGTH = 64  # 로컬 파트 전체 길이(점 포함). 정규식과 따로 확인

RECEIPT_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 0·O·1·I를 뺀 32자
RECEIPT_PATTERN = re.compile(r"C-\d{8}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}")
_RECEIPT_RANDOM_CHARS = 6

_EMAIL_BEFORE = r"(?<![A-Za-z0-9._%+-])"
_EMAIL_AFTER = r"(?![A-Za-z0-9-]|\.[A-Za-z0-9])"


def consult_inbox():
    """받는 사람 주소. 환경변수 CONSULT_INBOX_ADDRESS(앞뒤 공백 제거), 비면 기본값. LLM 인자로 받지 않는다."""
    return os.environ.get("CONSULT_INBOX_ADDRESS", "").strip() or CONSULT_INBOX_DEFAULT


def is_valid_email(value):
    """str이 아니면 False. 공백을 지우지 않는다. fullmatch라서 끝 줄바꿈도 거부한다."""
    if not isinstance(value, str) or len(value) > EMAIL_MAX_LENGTH:
        return False
    if not EMAIL_PATTERN.fullmatch(value):
        return False
    return len(value.rsplit("@", 1)[0]) <= LOCAL_MAX_LENGTH


def email_in_messages(email, user_messages):
    """사용자 메시지 원문 중 하나에 email이 단독 주소로 그대로(대소문자 포함) 있으면 True."""
    if not isinstance(email, str) or not email:
        return False
    pattern = re.compile(_EMAIL_BEFORE + re.escape(email) + _EMAIL_AFTER)
    return any(isinstance(m, str) and pattern.search(m) for m in user_messages or [])


def clean_text(text, limit):
    """None → "". 앞뒤 공백 제거 → scrub → limit자 초과면 자르고 "…"."""
    if text is None:
        return ""
    cleaned = scrub(str(text).strip()).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "…"
    return cleaned


def product_name(source):
    """"KB_실손의료비보장보험_26.09_약관.pdf" → "KB 실손의료비보장보험 26.09"."""
    name = source or ""
    if name.endswith(".pdf"):
        name = name[: -len(".pdf")]
    if name.endswith("_약관"):
        name = name[: -len("_약관")]
    return name.replace("_", " ").strip()


def _kst(now):
    return now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)


def new_receipt(now, choice=secrets.choice):
    """C-YYYYMMDD-XXXXXX. 날짜는 KST. choice는 테스트에서 충돌을 일으키려고 바꿔 끼운다."""
    suffix = "".join(choice(RECEIPT_ALPHABET) for _ in range(_RECEIPT_RANDOM_CHARS))
    return f"C-{_kst(now):%Y%m%d}-{suffix}"


def mail_subject(receipt, product):
    return f"[보험메이트 상담 신청] {receipt} {product}"


def mail_body(request):
    """고정 형식 본문. 회신 주소는 `회신 주소:` 줄에만 들어간다."""
    created = request["created_at"]
    when = f"{created[:10]} {created[11:16]} (KST)"
    return "\n".join([
        f"보험메이트 상담 신청이 접수되었습니다. ({MOCK_NOTICE})",
        "",
        f"접수번호: {request['receipt_number']}",
        f"상품: {request['product_name']}",
        f"선호 시간대: {request['preferred_time'] or '없음'}",
        f"회신 주소: {request['reply_email']}",
        f"접수 시각: {when}",
        "",
        "문의 요약:",
        request["summary"],
    ])


def build_request(*, summary, reply_email, preferred_time, source, session_id, now, receipt):
    """Outbox_File 내용. summary·preferred_time은 clean_text를 거친 값, reply_email은 그대로."""
    product = product_name(source)
    request = {
        "format_version": 1,
        "receipt_number": receipt,
        "created_at": _kst(now).isoformat(timespec="seconds"),
        "session_id": session_id,
        "to": consult_inbox(),
        "subject": mail_subject(receipt, product),
        "body": "",
        "source": source,
        "product_name": product,
        "summary": summary,
        "preferred_time": preferred_time or None,
        "reply_email": reply_email,
    }
    request["body"] = mail_body(request)
    return request


def notice_text(receipt, product, summary):
    """Slack 알림 글. summary는 이미 Scrubber를 거친 값. 회신 이메일·선호 시간대는 받지 않는다."""
    first = next((line.strip() for line in (summary or "").splitlines() if line.strip()), "")
    return "\n".join([
        f"*새 상담 접수* {receipt}",
        f"상품: {product}",
        f"요약: {first[:NOTICE_SUMMARY_CHARS]}",
    ])
