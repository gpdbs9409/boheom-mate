"""상담 신청 요약·알림에 쓰는 개인정보 가리기. 외부 호출은 하지 않는다.

연락처·주민번호·이메일처럼 눈에 띄는 개인정보를 가린다. 병명은 상담에 필요할 수 있어 가리지 않는다.
"""
import re

RRN_PATTERN = re.compile(r"\d{6}\s*-\s*[1-8]\d{6}")
PHONE_PATTERN = re.compile(r"01[016789][\s.-]?\d{3,4}[\s.-]?\d{4}")
# 로컬 파트는 상담 회신 이메일 검증(consultation.is_valid_email)이 받는 문자까지 가린다
EMAIL_TEXT_PATTERN = re.compile(r"[\w!#$%&'*+/=?^`{|}~.-]+@[\w-]+(?:\.[\w-]+)+")
_PATTERNS = [
    (RRN_PATTERN, "[주민번호 가림]"),
    (PHONE_PATTERN, "[전화번호 가림]"),
    (EMAIL_TEXT_PATTERN, "[이메일 가림]"),
]


def scrub(text):
    """주민번호·휴대전화·이메일을 가린다."""
    for pattern, mask in _PATTERNS:
        text = pattern.sub(mask, text or "")
    return text
