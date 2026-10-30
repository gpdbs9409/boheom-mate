"""약관 원문에서 보험금 계산에 쓸 값(보상 비율, 공제금액, 한도)을 뽑고, 원문 인용으로 검증한다.

1. 상품 약관의 보상 조항(보통약관·중증 특약·비중증 특약의 제3조) 원문을 조항 번호로 정확히 꺼낸다(검색 아님).
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
    _, kind, _, _ = FIELDS[key]
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


def _ask(group_name, article_text, keys, hint=""):
    from boheom_mate.core import rag

    fields = "\n".join(f'- {k}: {FIELDS[k][3]}' for k in keys)
    return rag._ask_json(PROMPT.format(group=group_name, fields=fields) + hint, f"[약관]\n{article_text}")


def extract(source):
    """약관에서 값을 뽑아 검증한 결과. 필수 값이 모두 검증되면 ok=True."""
    groups, doc_count = _articles_by_group(source)
    values, quotes, failed = {}, {}, {}
    for group, group_name in GROUPS.items():
        keys = [k for k, f in FIELDS.items() if f[0] == group]
        if group not in groups:
            failed.update({k: f"{group_name} 제3조를 찾지 못함" for k in keys})
            continue
        text = groups[group]["text"]
        todo, hint = keys, ""
        for _ in range(3):  # 검증에 실패한 값만 이유를 알려 주고 최대 두 번 더 묻는다
            answer = _ask(group_name, text, todo, hint)
            retry = []
            for k in todo:
                item = answer.get(k) or {}
                value, quote = item.get("value"), item.get("quote")
                if FIELDS[k][1] in ("won", "count") and isinstance(value, float) and value.is_integer():
                    value = int(value)
                reason = verify(k, value, quote, text)
                if reason:
                    failed[k] = reason
                    retry.append(k)
                else:
                    failed.pop(k, None)
                    values[k], quotes[k] = value, quote
            for big, small in MUST_EXCEED:
                if big in values and small in values and values[big] <= values[small] and big not in retry:
                    failed[big] = f"{big}({values[big]})가 {small}({values[small]})보다 커야 함"
                    del values[big], quotes[big]
                    retry.append(big)
            if not retry:
                break
            todo = retry
            hint = "\n[다시 확인할 값]\n" + "\n".join(f"- {k}: 앞 답이 검증에 실패함({failed[k]}). 원문 구절을 그대로 복사하세요." for k in retry)
    missing = [k for k, f in FIELDS.items() if f[2] and k not in values]
    return {
        "source": source,
        "doc_count": doc_count,
        "ok": not missing,
        "values": values,
        "quotes": quotes,
        "failed": failed,
        "articles": {g: groups[g]["label"] for g in groups},
        "evidence_meta": {g: {"section": groups[g]["section"], "article_no": "제3조", "title": groups[g]["title"]}
                          for g in groups},
    }


def _path(source):
    return RULES_DIR / (re.sub(r"[^\w.-]", "_", source) + ".json")


def load(source):
    """저장된 값을 쓰고, 없거나 약관 조각 수가 바뀌었으면 새로 뽑는다. 계산에 못 쓰면 None."""
    from boheom_mate.core import rag

    path = _path(source)
    doc_count = len(rag._collection().get(where={"source": source}, include=[])["ids"])
    data = None
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("doc_count") != doc_count:
            data = None
    if data is None:
        data = extract(source)
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data if data["ok"] else None


def evidence(rules, used_keys):
    """계산에 쓴 값의 약관 인용 구절을 근거 조항 형태로. 같은 조항끼리는 화면에서 묶인다."""
    out, seen = [], set()
    for key in used_keys:
        quote = rules["quotes"].get(key)
        meta = rules["evidence_meta"].get(FIELDS[key][0]) if key in FIELDS else None
        if quote and meta and quote not in seen:
            seen.add(quote)
            out.append({**meta, "text": quote, "highlights": [quote]})
    return out
