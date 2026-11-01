"""보험금 청구 참고 기한 계산(치료일 + N년).

순수 함수만 둔다. 기간 숫자(예: 3년)는 코드에 두지 않고 약관 원문에서 뽑은 값을 받는다.
"""
import calendar
import re
from datetime import date

DEADLINE_NOTE = (
    "치료일 기준으로 계산한 참고 날짜입니다. 약관상 소멸시효는 청구권을 행사할 수 있는 때부터 "
    "진행하므로 실제 기산일은 사안마다 다를 수 있고, 실제 판단은 보험사 심사로 확정됩니다"
)

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def reference_deadline(treatment_date: date, years: int) -> date:
    """치료일 + years년. 2월 29일이고 대상 연도가 윤년이 아니면 2월 28일."""
    target = treatment_date.year + years
    day = treatment_date.day
    if treatment_date.month == 2 and day == 29 and not calendar.isleap(target):
        day = 28
    return date(target, treatment_date.month, day)


def parse_date(text: str) -> date:
    """"YYYY-MM-DD"만 받는다. 형식·달력 오류는 ValueError."""
    if not isinstance(text, str):
        raise ValueError(f"날짜는 YYYY-MM-DD 형식 문자열이어야 합니다: {text!r}")
    m = _DATE_RE.fullmatch(text.strip())
    if not m:
        raise ValueError(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {text!r}")
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError as e:
        raise ValueError(f"없는 날짜입니다: {text!r}") from e
