"""약관 원문에서 보험금 계산에 쓸 값(보상 비율, 공제금액, 한도)을 뽑고, 원문 인용으로 검증한다.

1. 상품 약관의 보상 조항(보통약관·중증 특약·비중증 특약의 제3조) 원문을 조항 번호로 정확히 꺼낸다(검색 아님).
   약관 구조로 프로필을 정한다(select_profile): 세 구역이 모두 있으면 기본형, 보통약관 제3조만 있고
   급여+비급여를 보장대상의료비로 합치면 간편가입. 상품코드·파일명은 보지 않는다.
2. LLM이 값마다 숫자와, 그 숫자가 들어 있는 원문 구절(quote)을 함께 돌려준다.
3. 코드가 검증한다: quote가 약관 원문에 그대로 있는가, 숫자가 quote 안에 있는가, 값의 범위가 맞는가.
   필수 값이 하나라도 검증되지 않으면 그 상품은 계산기를 쓰지 않는다(지어낸 값으로 계산하지 않기 위해).
4. 결과는 state/rules/에 저장해 두고, 약관을 다시 넣어 조각 수가 바뀌면 새로 뽑는다.

금액 계산 자체는 calculator.py가 한다. LLM은 약관을 읽어 값을 찾는 일만 한다.
"""
import json
import re

from boheom_mate.core import ingest

RULES_DIR = ingest.BASE_DIR / "state" / "rules"

# 값이 어느 구역의 제3조에 있는지
GROUPS = {
    "covered": "보통약관(급여)",
    "severe": "중증 비급여 특약",
    "non_severe": "비중증 비급여 특약",
}

# 키: (구역, 종류, 필수 여부, 설명).
# 종류 pay_rate=회사가 보상하는 비율, deduct_rate=공제(빼는) 비율, won=원, count=일·회 수
FIELDS = {
    "covered_inpatient_pay_rate": ("covered", "pay_rate", True, "급여 입원 시 회사가 보상하는 비율(본인부담금 중). 자기부담 20%면 0.8"),
    "covered_outpatient_clinic_deduct": ("covered", "won", True, "급여 통원 공제금액 중 의원·병원 등(종합병원 외) 정액"),
    "covered_outpatient_general_deduct": ("covered", "won", True, "급여 통원 공제금액 중 종합병원·상급종합병원 정액"),
    "covered_outpatient_rate": ("covered", "deduct_rate", True, "급여 통원 공제 비율. 공제금액 계산에 쓰는 '보장대상의료비의 ○%'의 ○"),
    "severe_inpatient_pay_rate": ("severe", "pay_rate", True, "중증 비급여 입원 시 회사가 보상하는 비율. 자기부담 30%면 0.7"),
    "severe_outpatient_deduct": ("severe", "won", True, "중증 비급여 통원 공제 정액"),
    "severe_outpatient_rate": ("severe", "deduct_rate", True, "중증 비급여 통원 공제 비율"),
    "severe_special_deduct": ("severe", "won", True, "중증 3대 비급여(도수치료·주사·MRI) 1회당 공제 정액"),
    "severe_special_rate": ("severe", "deduct_rate", True, "중증 3대 비급여 1회당 공제 비율"),
    "severe_manual_therapy_annual_cap": ("severe", "won", False, "중증 도수치료·체외충격파 연간 보상 한도 금액"),
    "severe_injection_annual_cap": ("severe", "won", False, "중증 비급여 주사 연간 보상 한도 금액"),
    "severe_mri_annual_cap": ("severe", "won", False, "중증 비급여 MRI 연간 보상 한도 금액"),
    "non_severe_inpatient_pay_rate": ("non_severe", "pay_rate", True, "비중증 비급여 입원 시 회사가 보상하는 비율. 자기부담 50%면 0.5"),
    "non_severe_clinic_inpatient_cap": ("non_severe", "won", True, "비중증 비급여 의원·병원 입원 1회당 보상 한도 금액"),
    "non_severe_outpatient_deduct": ("non_severe", "won", True, "비중증 비급여 통원 공제 정액"),
    "non_severe_outpatient_rate": ("non_severe", "deduct_rate", True, "비중증 비급여 통원 공제 비율"),
    "non_severe_outpatient_annual_days": ("non_severe", "count", False, "비중증 비급여 통원 연간 보상 일수"),
    "non_severe_mri_deduct": ("non_severe", "won", True, "비중증 비급여 MRI 공제 정액"),
    "non_severe_mri_rate": ("non_severe", "deduct_rate", True, "비중증 비급여 MRI 공제 비율"),
    "non_severe_mri_annual_cap": ("non_severe", "won", False, "비중증 비급여 MRI 연간 보상 한도 금액"),
    "room_pay_rate": ("non_severe", "pay_rate", True, "상급병실료 차액(1·2인실) 보상 비율"),
    "room_daily_cap": ("non_severe", "won", True, "상급병실료 차액 1일 평균 보상 한도 금액"),
}

# 인용 구절 앞 문맥(최대 ANCHOR_WINDOW자)에 이 중 하나가 있어야 한다. 숫자는 맞는데 다른 항목의 구절을 인용하는 것을 막는다
ANCHOR_WINDOW = 400
ANCHORS = {
    "covered_inpatient_pay_rate": ["입원"],
    "covered_outpatient_clinic_deduct": ["종합병원은제외"],
    "covered_outpatient_general_deduct": ["상급종합병원"],
    "covered_outpatient_rate": ["통원", "공제금액"],
    "severe_inpatient_pay_rate": ["입원"],
    "severe_outpatient_deduct": ["통원", "공제금액"],
    "severe_outpatient_rate": ["통원", "공제금액"],
    "severe_special_deduct": ["도수", "이학요법", "체외충격파", "주사", "자기공명", "MRI", "MRA"],
    "severe_special_rate": ["도수", "이학요법", "체외충격파", "주사", "자기공명", "MRI", "MRA"],
    "severe_manual_therapy_annual_cap": ["도수", "이학요법", "체외충격파"],
    "severe_injection_annual_cap": ["주사"],
    "severe_mri_annual_cap": ["자기공명", "MRI", "MRA"],
    "non_severe_inpatient_pay_rate": ["입원"],
    "non_severe_clinic_inpatient_cap": ["입원"],
    "non_severe_outpatient_deduct": ["통원", "공제금액"],
    "non_severe_outpatient_rate": ["통원", "공제금액"],
    "non_severe_outpatient_annual_days": ["통원"],
    "non_severe_mri_deduct": ["자기공명", "MRI", "MRA"],
    "non_severe_mri_rate": ["자기공명", "MRI", "MRA"],
    "non_severe_mri_annual_cap": ["자기공명", "MRI", "MRA"],
    "room_pay_rate": ["병실"],
    "room_daily_cap": ["병실"],
}
# (더 커야 하는 값, 더 작아야 하는 값)
MUST_EXCEED = [("covered_outpatient_general_deduct", "covered_outpatient_clinic_deduct")]

# 간편가입 프로필: 비급여 특약 없이 보통약관 제3조가 급여+비급여를 보장대상의료비로 합쳐 보상한다.
# 값은 모두 약관에서 뽑아 검증한다. 숫자는 여기 두지 않는다(테스트 기준값은 calculator.KB_SIMPLE_RULES).
PROFILE_BASE, PROFILE_SIMPLE = "기본형", "간편가입"
SIMPLE_FIELDS = {
    "simple_inpatient_deduct": ("covered", "won", True, "입원 공제금액 정액('○만원과 보장대상의료비의 ○% 중 큰 금액'의 정액)"),
    "simple_inpatient_rate": ("covered", "deduct_rate", True, "입원 공제 비율('○만원과 보장대상의료비의 ○% 중 큰 금액'의 ○)"),
    "simple_inpatient_annual_deduct_cap": ("covered", "won", True, "입원 공제금액의 연간 합계 상한(이 금액을 넘는 공제금액은 보상)"),
    "simple_inpatient_case_cap": ("covered", "won", False, "하나의 상해·질병당 보험가입금액 한도"),
    "simple_outpatient_deduct": ("covered", "won", True, "통원 1회당 공제금액 정액"),
    "simple_outpatient_rate": ("covered", "deduct_rate", True, "통원 1회당 공제 비율"),
    "simple_outpatient_per_visit_cap": ("covered", "won", True, "통원 방문 1회당 보상 한도(보험가입금액)"),
    "simple_outpatient_annual_visits": ("covered", "count", True, "통원 연간 방문 한도 횟수"),
    "simple_room_pay_rate": ("covered", "pay_rate", True, "상급병실료 차액 보상 비율"),
    "simple_room_daily_cap": ("covered", "won", True, "상급병실료 차액 1일 평균 보상 한도 금액"),
    "simple_no_nhis_pay_rate": ("covered", "pay_rate", True, "국민건강보험을 적용받지 못한 경우 회사가 보상하는 비율"),
}
SIMPLE_ANCHORS = {
    "simple_inpatient_deduct": ["입원"],
    "simple_inpatient_rate": ["입원"],
    "simple_inpatient_annual_deduct_cap": ["초과", "연간", "계약해당일"],
    "simple_inpatient_case_cap": ["보험가입금액", "하나의"],
    # 통원 보상금액 문장은 "방문 1회당"으로 시작하고, "(2) 상해통원" 제목에서 400자 넘게 떨어져 있다. "방문 1회당"은 통원 구역에만 있다
    "simple_outpatient_deduct": ["통원", "방문1회당"],
    "simple_outpatient_rate": ["통원", "방문1회당"],
    "simple_outpatient_per_visit_cap": ["통원", "1회당"],
    "simple_outpatient_annual_visits": ["통원", "방문"],
    "simple_room_pay_rate": ["병실"],
    "simple_room_daily_cap": ["병실"],
    "simple_no_nhis_pay_rate": ["국민건강보험", "적용받지"],
}
ANCHORS.update(SIMPLE_ANCHORS)
ALL_FIELDS = {**FIELDS, **SIMPLE_FIELDS}  # verify()·_ask()가 쓰는 조회표. 간편가입 키는 simple_ 접두사라 겹치지 않는다
# 간편가입 추출 프롬프트에 덧붙이는 주의. 숫자 예시는 넣지 않는다(○로 둔다)
SIMPLE_PROMPT_NOTE = """
[간편가입 약관 주의]
- quote는 약관의 한 곳에서 이어지는 짧은 구절 하나만 그대로 복사하세요. 떨어진 두 곳을 이어 붙이지 마세요. 각주 표시(주), 주1), 주2))도 원문 그대로 두세요.
- 공제 정액과 공제 비율은 '○만원과 보장대상의료비의 ○% 중 큰 금액'처럼 정액과 비율이 함께 있는 구절을 그대로 인용하세요. 이 구절은 정액 숫자를 포함해야 합니다.
- 통원 값은 '방문 1회당'으로 시작하는 통원 보상금액 문장에서, 입원 값은 입원 보상금액 문장에서 인용하세요."""
PROFILES = {
    PROFILE_BASE: {"fields": FIELDS, "groups": GROUPS, "must_exceed": MUST_EXCEED,
                   "prompt_note": "", "retry_context": False},
    PROFILE_SIMPLE: {"fields": SIMPLE_FIELDS, "groups": {"covered": "보통약관"},
                     "must_exceed": [("simple_inpatient_deduct", "simple_outpatient_deduct")],
                     "prompt_note": SIMPLE_PROMPT_NOTE, "retry_context": True},
}
RETRY_CONTEXT_CHARS = 60  # 재질문 때 보여 줄 인용 구절 앞뒤 원문 길이(_norm 기준 글자 수)

# 보통약관 제3조가 급여와 비급여를 합한 금액을 '보장대상의료비'로 정의하는 구조(_norm 적용 후 원문에 맞춘다)
SIMPLE_STRUCTURE = re.compile(r"비급여.{0,120}?합한금액\(이하\W?보장대상의료비")

# Rule_Set 파일 형식. 1은 values·quotes·failed만 있던 형식, 2는 profile·면책 3항목·소멸시효·코드 범위를 더한 형식
FORMAT_VERSION = 2

# 간편가입이 보통약관 제4조에서 보상하지 않는 비급여 3항목.
# 키: (LLM에 주는 설명, 인용문에 모두 있어야 하는 말, 인용문에 하나 이상 있어야 하는 말). 키는 calculator.SIMPLE_EXCLUDED_ITEMS와 같다
SIMPLE_EXCLUSION_ITEMS = {
    "manual_therapy": ("도수치료·체외충격파치료·증식치료로 생긴 비급여 의료비를 보상하지 않는다는 구절",
                       ["비급여"], ["도수치료", "체외충격파", "증식치료"]),
    "injection": ("비급여 주사료를 보상하지 않는다는 구절(항암제·항생제·희귀의약품 예외 포함)",
                  ["주사료", "항암제"], ["비급여"]),
    "mri": ("자기공명영상진단(MRI/MRA)으로 생긴 비급여 의료비를 보상하지 않는다는 구절",
            ["비급여"], ["자기공명영상진단", "MRI", "MRA"]),
}
EXCLUSION_ANCHOR = "보상하지"
EXCLUSION_SECTION = "보통약관"

EXCLUSION_PROMPT = """당신은 보험약관에서 구절을 정확히 옮기는 담당자입니다. 아래 [약관]은 보통약관의 '보상하지 않는 사항' 원문입니다.
[찾을 구절]의 각 키에 대해 그 내용을 적은 약관 원문 구절을 찾아 JSON으로만 답하세요.

{{"키": {{"quote": "약관 원문 구절"}}, ...}}

- quote는 약관 원문을 한 글자도 바꾸지 말고 그대로 복사하세요. 20~200자 정도로, 그 항목의 이름(치료·주사료·영상진단 등)과 '비급여'를 포함하세요.
- 약관에서 찾을 수 없으면 quote를 null로 두세요. 추측하지 마세요.

[찾을 구절]
{fields}"""

# 소멸시효 기간 문장(_norm 적용 후). 기간 숫자는 원문에서 읽는다(코드에 두지 않는다)
LIMITATION_PATTERN = re.compile(r"(\d{1,2})년간행사하지(?:않|아니하)(?:으면|면)소멸시효")
LIMITATION_BASIS = re.compile(r"행사할수있는때부터진행")
LIMITATION_YEARS_RANGE = (1, 10)
_SENTENCE_SPLIT = re.compile(r"(?<=다\.)")
_HEADING_MAX = 15  # 문장 앞에 붙은 조 제목·"부가설명" 같은 짧은 줄(_norm 기준 글자 수)은 인용문에서 뺀다

PROMPT = """당신은 보험약관에서 숫자를 정확히 옮기는 담당자입니다. 아래 [약관]은 {group}의 제3조(보장종목별 보상내용) 원문입니다.
[찾을 값]의 각 키에 대해 약관에 적힌 값을 찾아 JSON으로만 답하세요.

{{"키": {{"value": 숫자 또는 null, "quote": "그 숫자가 들어 있는 약관 원문 구절"}}, ...}}

- quote는 약관 원문을 한 글자도 바꾸지 말고 그대로 복사하세요. 20~80자 정도로, 값의 숫자를 반드시 포함하세요.
- 비율은 소수로 쓰세요(80% → 0.8). 금액은 원 단위 정수로 쓰세요(1만원 → 10000, 300만원 → 3000000).
- "회사가 보상하는 비율"을 묻는 키는 약관이 자기부담 비율로 적었으면 1에서 뺀 값으로 쓰세요(자기부담 20% → 0.8).
- "공제 비율"을 묻는 키는 약관에 적힌 빼는 비율을 그대로 쓰세요("3만원과 보장대상의료비의 30% 중 큰 금액" → 0.3).
- 약관에서 찾을 수 없으면 value와 quote를 null로 두세요. 추측하지 마세요.

[찾을 값]
{fields}"""


_SAME_CHARS = str.maketrans({"ㆍ": "·", "･": "·", "∼": "~", "～": "~", "‘": "'", "’": "'", "“": '"', "”": '"'})


def _norm(text):
    """공백을 지우고 PDF마다 다르게 들어가는 비슷한 기호를 하나로 맞춘다."""
    return re.sub(r"\s+", "", (text or "").translate(_SAME_CHARS))


def _group_of(section):
    section = section or ""
    if section == "보통약관":
        return "covered"
    if section.startswith("특별약관") and "비중증" in section:
        return "non_severe"
    if section.startswith("특별약관") and "중증" in section:
        return "severe"
    return None


def _number_forms(kind, value):
    """quote 안에서 찾을 숫자 표기들."""
    if kind == "pay_rate":
        pct = round(value * 100)
        return [f"{pct}%", f"{100 - pct}%"]  # 보상 비율(80%)이나 자기부담 비율(20%) 어느 쪽으로 적혀 있어도 된다
    if kind == "deduct_rate":
        return [f"{round(value * 100)}%"]  # 공제 비율은 약관에 적힌 숫자 그대로여야 한다
    if kind == "count":
        return [f"{value}일", f"{value}회"]
    forms = [f"{value:,}원", f"{value}원"]
    if value % 10000 == 0:
        man = value // 10000
        forms += [f"{man:,}만원", f"{man}만원", f"{man}만"]
        if man % 1000 == 0:
            forms.append(f"{man // 1000}천만원")
    return forms


def verify(key, value, quote, article_text):
    """값 하나를 검증한다. 문제가 없으면 None, 있으면 이유 문자열."""
    _, kind, _, _ = ALL_FIELDS[key]
    if value is None or not quote:
        return "약관에서 값을 찾지 못함"
    if kind in ("pay_rate", "deduct_rate") and not (0 < value <= 1):
        return f"비율 범위 밖: {value}"
    if kind in ("won", "count") and (not isinstance(value, int) or value <= 0):
        return f"금액·횟수가 양의 정수가 아님: {value}"
    text, q = _norm(article_text), _norm(quote)
    pos = text.find(q)
    if pos < 0:
        return "인용 구절이 약관 원문에 없음"
    window = text[max(0, pos - ANCHOR_WINDOW):pos + len(q)]
    if key in ANCHORS and not any(_norm(a) in window for a in ANCHORS[key]):
        return f"인용 구절 앞뒤에 이 항목을 가리키는 말({'/'.join(ANCHORS[key])})이 없음"
    if not any(re.search(r"(?<![\d,.])" + re.escape(_norm(f)), q) for f in _number_forms(kind, value)):
        return f"인용 구절에 숫자가 없음: {value}"
    return None


def _articles_by_group(source):
    """구역별 제3조 원문과 조항 이름. {"covered": {"text", "label", "section", "title"}, ...}"""
    from boheom_mate.core import rag

    data = rag._collection().get(where={"source": source}, include=["documents", "metadatas"])
    rows = sorted(zip(data["ids"], data["documents"], data["metadatas"]),
                  key=lambda r: int(r[0].rsplit("-", 1)[-1]) if r[0].rsplit("-", 1)[-1].isdigit() else 0)
    groups = {}
    for _, doc, meta in rows:
        group = _group_of(meta.get("section"))
        if group and meta.get("article_no") == "제3조":
            g = groups.setdefault(group, {"parts": [], "section": meta["section"], "title": meta.get("title", "")})
            g["parts"].append(doc)
    for g in groups.values():
        g["text"] = "\n".join(g.pop("parts"))
        g["label"] = f'{g["section"]} 제3조 ({g["title"]})' if g["title"] else f'{g["section"]} 제3조'
    return groups, len(data["ids"])


def _ask(group_name, article_text, keys, hint="", note=""):
    from boheom_mate.core import rag

    fields = "\n".join(f'- {k}: {ALL_FIELDS[k][3]}' for k in keys)
    return rag._ask_json(PROMPT.format(group=group_name, fields=fields) + note + hint, f"[약관]\n{article_text}")


def _quote_context(quote, article_text):
    """인용 구절이 원문에 있으면 그 앞뒤 RETRY_CONTEXT_CHARS자를 붙인 원문 구간, 없으면 None."""
    text, q = _norm(article_text), _norm(quote)
    pos = text.find(q) if q else -1
    if pos < 0:
        return None
    return _original_span(article_text, max(0, pos - RETRY_CONTEXT_CHARS), min(len(text), pos + len(q) + RETRY_CONTEXT_CHARS))


def _original_span(text, start, end):
    """_norm(text)의 [start, end) 구간에 해당하는 원문 구간. _norm은 공백만 지우고 글자를 1:1로 바꾸므로 위치를 되짚을 수 있다."""
    kept = [i for i, ch in enumerate(text) if not ch.isspace()]
    return text[kept[start]:kept[end - 1] + 1]


def _simple_structure_quote(groups):
    """보통약관 제3조에서 SIMPLE_STRUCTURE가 맞는 원문 구간. 없으면 None."""
    if "covered" not in groups:
        return None
    text = groups["covered"]["text"]
    m = SIMPLE_STRUCTURE.search(_norm(text))
    return _original_span(text, m.start(), m.end()) if m else None


def select_profile(groups):
    """(프로필, 판정 이유). 약관 구조만 본다. 상품코드·파일명은 보지 않는다."""
    if {"covered", "severe", "non_severe"} <= groups.keys():
        return PROFILE_BASE, "보통약관·중증·비중증 비급여 특약 제3조가 모두 있음"
    if groups.keys() == {"covered"} and _simple_structure_quote(groups):
        return PROFILE_SIMPLE, "비급여 특약 없이 보통약관 제3조가 급여+비급여를 보장대상의료비로 합산"
    return None, "약관 구조를 판별하지 못함"


def profile_basis(groups):
    """Rule_Set의 profile_basis: {"reason", "quote"}. quote는 간편가입 판정에 쓴 원문 구간(기본형·판별 불가는 None)."""
    profile, reason = select_profile(groups)
    return {"reason": reason, "quote": _simple_structure_quote(groups) if profile == PROFILE_SIMPLE else None}


def _extract_values(profile, groups):
    """프로필의 값을 구역별 제3조에서 뽑아 검증한다. (values, quotes, failed)."""
    spec = PROFILES[profile]
    fields, must_exceed = spec["fields"], spec["must_exceed"]
    values, quotes, failed = {}, {}, {}
    for group, group_name in spec["groups"].items():
        keys = [k for k, f in fields.items() if f[0] == group]
        if group not in groups:
            failed.update({k: f"{group_name} 제3조를 찾지 못함" for k in keys})
            continue
        text = groups[group]["text"]
        todo, hint, bad_quotes = keys, "", {}
        for _ in range(3):  # 검증에 실패한 값만 이유를 알려 주고 최대 두 번 더 묻는다
            answer = _ask(group_name, text, todo, hint, spec["prompt_note"])
            retry = []
            for k in todo:
                item = answer.get(k) or {}
                value, quote = item.get("value"), item.get("quote")
                if fields[k][1] in ("won", "count") and isinstance(value, float) and value.is_integer():
                    value = int(value)
                reason = verify(k, value, quote, text)
                if reason:
                    failed[k] = reason
                    bad_quotes[k] = quote
                    retry.append(k)
                else:
                    failed.pop(k, None)
                    values[k], quotes[k] = value, quote
            for big, small in must_exceed:
                if big in values and small in values and values[big] <= values[small] and big not in retry:
                    failed[big] = f"{big}({values[big]})가 {small}({values[small]})보다 커야 함"
                    del values[big], quotes[big]
                    retry.append(big)
            if not retry:
                break
            todo = retry
            lines = []
            for k in retry:
                line = f"- {k}: 앞 답이 검증에 실패함({failed[k]}). 원문 구절을 그대로 복사하세요."
                context = _quote_context(bad_quotes.get(k), text) if spec["retry_context"] else None
                if context:  # 앞 답이 인용한 곳의 원문을 보여 주고, 그 근처에서 숫자가 든 이어지는 구절을 다시 고르게 한다
                    line += f" 앞 답이 인용한 곳의 원문(앞뒤 포함): \"{context}\""
                lines.append(line)
            hint = "\n[다시 확인할 값]\n" + "\n".join(lines)
    return values, quotes, failed


def _chunk_meta(chunk):
    return {"section": chunk.get("section"), "article_no": chunk.get("article_no"), "title": chunk.get("title")}


def verify_exclusion_item(key, quote, exclusion_text):
    """면책 항목 하나의 인용문을 검증한다. 문제가 없으면 None, 있으면 이유 문자열.
    규칙: quote가 원문(_norm 기준)에 있음, 필수 말 모두 포함, 선택 말 하나 이상 포함,
    quote 앞뒤 ANCHOR_WINDOW자 안에 '보상하지' 포함."""
    _, required, any_of = SIMPLE_EXCLUSION_ITEMS[key]
    if not quote or not isinstance(quote, str):
        return "약관에서 구절을 찾지 못함"
    text, q = _norm(exclusion_text), _norm(quote)
    pos = text.find(q)
    if pos < 0:
        return "인용 구절이 약관 원문에 없음"
    missing = [w for w in required if _norm(w) not in q]
    if missing:
        return f"인용 구절에 꼭 있어야 하는 말({'/'.join(missing)})이 없음"
    if not any(_norm(w) in q for w in any_of):
        return f"인용 구절에 이 항목을 가리키는 말({'/'.join(any_of)})이 없음"
    window = text[max(0, pos - ANCHOR_WINDOW):pos + len(q) + ANCHOR_WINDOW]
    if EXCLUSION_ANCHOR not in window:
        return f"인용 구절 앞뒤 {ANCHOR_WINDOW}자 안에 '{EXCLUSION_ANCHOR}'가 없음"
    return None


def _exclusion_quote_meta(quote, chunks):
    """인용문이 들어 있는 조각의 조항 정보. 조각 경계에 걸치면 첫 조각의 정보를 쓴다."""
    q = _norm(quote)
    for chunk in chunks:
        if q in _norm(chunk.get("text")):
            return _chunk_meta(chunk)
    return _chunk_meta(chunks[0])


def _extract_exclusion_items(exclusion_chunks):
    """간편가입 면책 3항목을 보통약관 Exclusion_Article에서 LLM으로 인용받아 검증한다. (items, failed).
    failed 키는 "exclusion_item:<키>"."""
    from boheom_mate.core import rag

    chunks = [c for c in exclusion_chunks or [] if c.get("section") == EXCLUSION_SECTION]
    keys = list(SIMPLE_EXCLUSION_ITEMS)
    if not chunks:
        return {}, {f"exclusion_item:{k}": f"{EXCLUSION_SECTION} 보상하지 않는 사항을 찾지 못함" for k in keys}
    text = "\n".join(c["text"] for c in chunks)
    items, reasons, todo, hint = {}, {}, keys, ""
    for _ in range(3):  # _extract_values와 같이 실패한 항목만 이유를 알려 주고 최대 두 번 더 묻는다
        fields = "\n".join(f"- {k}: {SIMPLE_EXCLUSION_ITEMS[k][0]}" for k in todo)
        answer = rag._ask_json(EXCLUSION_PROMPT.format(fields=fields) + hint, f"[약관]\n{text}")
        retry = []
        for k in todo:
            item = answer.get(k) if isinstance(answer, dict) else None
            quote = item.get("quote") if isinstance(item, dict) else None
            reason = verify_exclusion_item(k, quote, text)
            if reason:
                reasons[k] = reason
                retry.append(k)
            else:
                reasons.pop(k, None)
                items[k] = {"quote": quote, "meta": _exclusion_quote_meta(quote, chunks)}
        if not retry:
            break
        todo = retry
        hint = "\n[다시 확인할 구절]\n" + "\n".join(
            f"- {k}: 앞 답이 검증에 실패함({reasons[k]}). 원문 구절을 그대로 복사하세요." for k in retry)
    return items, {f"exclusion_item:{k}": r for k, r in reasons.items()}


def _sentences(text):
    """문장("다." 기준) 목록. 문장 앞에 붙은 짧은 줄(조 제목, '부가설명' 머리)은 떼어 낸다."""
    out = []
    for raw in _SENTENCE_SPLIT.split(text or ""):
        lines = [ln for ln in raw.split("\n") if ln.strip()]
        while len(lines) > 1 and len(_norm(lines[0])) <= _HEADING_MAX:
            lines.pop(0)
        sentence = "\n".join(lines).strip()
        if sentence:
            out.append(sentence)
    return out


def extract_limitation(limitation_chunks):
    """Limitation_Article에서 소멸시효 기간을 뽑아 검증한다.
    {"years": int|None, "quote": str|None, "basis_quote": str|None, "meta": {...}|None, "failed": str|None}.
    기간 숫자는 원문 문장에서 읽는다(LIMITATION_PATTERN)."""
    empty = {"years": None, "quote": None, "basis_quote": None, "meta": None}
    chunks = list(limitation_chunks or [])
    if not chunks:
        return {**empty, "failed": "소멸시효 조항을 찾지 못함"}
    for chunk in chunks:
        original = chunk.get("text") or ""
        sentences = _sentences(original)
        for sentence in sentences:
            m = LIMITATION_PATTERN.search(_norm(sentence))
            if not m:
                continue
            years, q = int(m.group(1)), _norm(sentence)
            lo, hi = LIMITATION_YEARS_RANGE
            if q not in _norm(original):
                reason = "인용 구절이 약관 원문에 없음"
            elif f"{years}년" not in q:
                reason = f"인용 구절에 기간 숫자가 없음: {years}"
            elif "소멸시효" not in q or "청구권" not in q:
                reason = "인용 구절에 '소멸시효'와 '청구권'이 함께 있지 않음"
            elif not lo <= years <= hi:
                reason = f"소멸시효 기간 범위 밖: {years}"
            else:
                reason = None
            if reason:
                return {**empty, "quote": sentence, "meta": _chunk_meta(chunk), "failed": reason}
            basis = next((s for s in sentences if LIMITATION_BASIS.search(_norm(s))), None)
            return {"years": years, "quote": sentence, "basis_quote": basis, "meta": _chunk_meta(chunk),
                    "failed": None}
    return {**empty, "failed": "소멸시효 기간 문장을 찾지 못함"}


def _supplements(source):
    """LLM 없이 채우는 부가 정보: (limitation, code_ranges, code_range_failed, exclusion_chunks)."""
    from boheom_mate.core import articles, kcd

    exclusion_chunks = articles.locate(source, articles.EXCLUSION)
    code_ranges, code_failed = kcd.collect(exclusion_chunks)
    limitation = extract_limitation(articles.locate(source, articles.LIMITATION))
    return limitation, code_ranges, code_failed, exclusion_chunks


def extract(source):
    """약관 구조로 프로필을 정하고, 그 프로필의 값을 뽑아 검증한 결과(Rule_Set v2).
    필수 값이 모두 검증되고, 간편가입이면 면책 3항목까지 검증되면 ok=True.
    소멸시효(limitation)와 코드 범위(code_ranges)는 ok에 영향을 주지 않는다."""
    groups, doc_count = _articles_by_group(source)
    profile, reason = select_profile(groups)
    limitation, code_ranges, code_failed, exclusion_chunks = _supplements(source)
    exclusion_items = {}
    if profile is None:
        values, quotes, failed, missing = {}, {}, {"profile": reason}, ["profile"]
    else:
        values, quotes, failed = _extract_values(profile, groups)
        missing = [k for k, f in PROFILES[profile]["fields"].items() if f[2] and k not in values]
        if profile == PROFILE_SIMPLE:
            exclusion_items, item_failed = _extract_exclusion_items(exclusion_chunks)
            failed.update(item_failed)
            missing += list(item_failed)
    failed.update(code_failed)
    return {
        "format_version": FORMAT_VERSION,
        "source": source,
        "doc_count": doc_count,
        "profile": profile,
        "profile_basis": profile_basis(groups),
        "ok": not missing,
        "values": values,
        "quotes": quotes,
        "failed": failed,
        "articles": {g: groups[g]["label"] for g in groups},
        "evidence_meta": {g: {"section": groups[g]["section"], "article_no": "제3조", "title": groups[g]["title"]}
                          for g in groups},
        "exclusion_items": exclusion_items,
        "limitation": limitation,
        "code_ranges": code_ranges,
    }


def _upgrade_v1(source, data, groups):
    """기본형 v1 캐시를 v2로 올린다. values·quotes·failed의 기존 항목은 그대로 두고 LLM 없이 새 칸만 채운다.
    코드 범위 검증 실패가 있으면 failed에 "code_range:…" 키로 더한다(기존 키는 건드리지 않는다)."""
    limitation, code_ranges, code_failed, _ = _supplements(source)
    upgraded = {
        **data,
        "format_version": FORMAT_VERSION,
        "profile": PROFILE_BASE,
        "profile_basis": profile_basis(groups),
        "exclusion_items": {},
        "limitation": limitation,
        "code_ranges": code_ranges,
    }
    upgraded["failed"] = {**data.get("failed", {}), **{k: v for k, v in code_failed.items()
                                                        if k not in data.get("failed", {})}}
    return upgraded


def _path(source):
    return RULES_DIR / (re.sub(r"[^\w.-]", "_", source) + ".json")


def load_rule_set(source):
    """ok 여부와 관계없이 전체 Rule_Set(v2)을 돌려준다.
    파일이 없거나 약관 조각 수가 바뀌었으면 새로 뽑는다. v1 파일은 ok인 기본형이면 LLM 없이 올리고,
    그 밖(간편가입 v1 등)은 새로 뽑는다. 올리거나 새로 뽑으면 같은 파일 이름으로 다시 쓴다."""
    from boheom_mate.core import rag

    path = _path(source)
    doc_count = rag._doc_count(source)
    data = None
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("doc_count") != doc_count:
            data = None
    if data is not None and data.get("format_version") == FORMAT_VERSION:
        return data
    if data is not None:
        groups, _ = _articles_by_group(source)
        data = _upgrade_v1(source, data, groups) if data.get("ok") and select_profile(groups)[0] == PROFILE_BASE else None
    if data is None:
        data = extract(source)
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load(source):
    """계산에 쓸 수 있는(ok=True) Rule_Set만 돌려준다. 아니면 None."""
    data = load_rule_set(source)
    return data if data.get("ok") else None


def evidence(rules, used_keys):
    """계산에 쓴 값의 약관 인용 구절을 근거 조항 형태로. 같은 조항끼리는 화면에서 묶인다.
    "exclusion:<키>"는 exclusion_items의 면책 인용문을 근거로 만든다."""
    out, seen = [], set()
    for key in used_keys:
        if key.startswith("exclusion:"):
            item = (rules.get("exclusion_items") or {}).get(key.split(":", 1)[1]) or {}
            quote, meta = item.get("quote"), item.get("meta")
        else:
            quote = rules["quotes"].get(key)
            meta = rules["evidence_meta"].get(ALL_FIELDS[key][0]) if key in ALL_FIELDS else None
        if quote and meta and quote not in seen:
            seen.add(quote)
            out.append({**meta, "text": quote, "highlights": [quote]})
    return out
