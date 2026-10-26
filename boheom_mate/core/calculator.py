"""실손 보험금 계산기. 기본 약관(KB 실손의료비보장보험 26.09) 제3조의 공식을 그대로 옮겼다.

GPT가 아니라 이 코드가 금액을 계산한다. GPT는 계산에 넣을 숫자만 모은다.
그래야 숫자가 틀리지 않고, 계산 과정을 조항과 함께 그대로 보여 줄 수 있다.

이 계산은 '최신 세대(2024년 개편, 이 약관 기준)'의 공식이다. 실손은 가입 시기에 따라
1~4세대로 자기부담률·공제·보장이 완전히 다르다(generation 참고). 지금은 최신 약관 데이터만
들어 있어서, 다른 세대는 최신 기준으로 계산한 '참고 상한'일 뿐 실제와 차이가 크다.

계산하지 않는 것(결과의 notes로 안내한다):
- 보장 여부(면책 사항 등)는 약관 검색으로 따로 판단한다
- 연간 가입금액 한도, 이미 받은 횟수·금액에 따른 연간 한도
- 국민건강보험을 적용받지 못한 경우(40% 보상), 의료비 감면을 받은 경우
- 다른 세대(1~3세대)의 공식. 세대별 약관 데이터는 추후 추가 예정
"""

# 세대 구분. 최신(이 약관)만 실제 공식으로 계산하고, 나머지는 '참고 상한'으로 안내한다.
# 세대별 약관·공식 데이터가 들어오면 여기에 분기를 추가한다(지금은 껍데기).
LATEST_GENERATION = "4세대"  # 이 약관(2024 개편 계열)이 해당하는 세대
KNOWN_GENERATIONS = ("1세대", "2세대", "3세대", "4세대")
GENERATION_WARNING = {
    "1세대": "1세대(2009년 10월 이전 가입)는 자기부담금이 없거나 매우 낮고 보장 구조가 크게 다릅니다.",
    "2세대": "2세대(2009.10~2017.3 가입)는 자기부담률이 10~20%로 낮고 급여·비급여가 분리되지 않았습니다.",
    "3세대": "3세대(2017.4~2021.6 가입, '착한실손')는 기본형과 비급여 특약(도수·주사·MRI) 구조가 지금과 다릅니다.",
}

# 병원 종류: 급여 통원 공제금액과 비중증 비급여 입원 한도가 달라진다
CLINIC = "의원"        # 의원·병원·치과·한의원·요양병원·보건소·약국 (「의료법」 제3조제2항, 종합병원 제외)
GENERAL = "종합병원"   # 종합병원·상급종합병원(대학병원)·전문요양기관
HOSPITAL_TYPES = (CLINIC, GENERAL)

ARTICLE_COVERED = "보통약관 제3조 (보장종목별 보상내용)"
ARTICLE_SEVERE = "특별약관1(중증 비급여) 제3조"
ARTICLE_NON_SEVERE = "특별약관2(비중증 비급여) 제3조"


def _won(n):
    return f"{round(n):,}원"


def _item(label, amount, how, article):
    return {"label": label, "amount": max(0, round(amount)), "how": how, "article": article}


def _covered(care, hospital, copay, nhis_paid, visits):
    """급여: 건강보험이 적용된 진료에서 내가 낸 돈(본인부담금).

    통원 공제(정액/비율)는 방문 1건당 뗀다. copay는 전체 방문 합계로 보고,
    1회 평균(copay/visits)에 공제를 적용한 뒤 횟수를 곱한다.
    """
    if care == "입원":
        return _item("건강보험 되는 진료비(급여)", copay * 0.8,
                     f"낸 돈 {_won(copay)}의 80%", ARTICLE_COVERED)

    per = copay / visits  # 1회 방문당 낸 돈
    base = 10000 if hospital == CLINIC else 20000
    candidates = {f"{_won(base)}(병원 종류 기준)": base, f"낸 돈의 20%({_won(per * 0.2)})": per * 0.2}
    if nhis_paid is not None and copay + nhis_paid > 0:
        # 약관의 '보장대상의료비 × 건강보험 본인부담률'. 본인부담률 = 본인부담금 ÷ (본인부담금 + 공단부담금)
        by_rate = per * copay / (copay + nhis_paid)
        candidates[f"낸 돈 × 본인부담률({_won(by_rate)})"] = by_rate
    label, deduct_per = max(candidates.items(), key=lambda kv: kv[1])
    paid = (per - deduct_per) * visits
    visit_note = f" × {visits}회" if visits > 1 else ""
    return _item("건강보험 되는 진료비(급여)", paid,
                 f"1회당 낸 돈 {_won(per)} − 빼는 돈 {_won(deduct_per)} (가장 큰 값: {label}){visit_note}", ARTICLE_COVERED)


def _deduct_outpatient(amount, fixed, rate, visits=1):
    """비급여 통원 공제: 정액과 비율 중 큰 금액. 공제는 방문 1건당 뗀다(visits만큼 반복)."""
    per = amount / visits
    deduct_per = max(fixed, per * rate)
    paid = (per - deduct_per) * visits
    visit_note = f" × {visits}회" if visits > 1 else ""
    return paid, f"1회당 {_won(per)} − 빼는 돈 {_won(deduct_per)} ({_won(fixed)}과 {int(rate * 100)}% 중 큰 금액){visit_note}"


def _non_covered(care, hospital, severe, general, manual_therapy, injection, mri, visits, notes):
    """비급여: 건강보험이 안 되는 진료비. 중증(암 등 산정특례 대상)이면 특약1, 아니면 특약2.

    통원 공제는 방문 1건당 뗀다(visits). 입원은 visits를 쓰지 않는다.
    """
    items = []
    if severe:
        article = ARTICLE_SEVERE
        if general:
            if care == "입원":
                items.append(_item("건강보험 안 되는 진료비(비급여)", general * 0.7,
                                   f"{_won(general)}의 70%", article))
            else:
                amount, how = _deduct_outpatient(general, 30000, 0.3, visits)
                items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
        # 3대 비급여(도수치료·체외충격파, 주사, MRI)는 따로 1회당 공제한다
        for label, value, cap in (("도수치료·체외충격파", manual_therapy, "연 350만 원·50회"),
                                  ("비급여 주사", injection, "연 250만 원·50회"),
                                  ("MRI", mri, "연 300만 원")):
            if value:
                amount, how = _deduct_outpatient(value, 30000, 0.3, visits)
                items.append(_item(label, amount, how, article))
                notes.append(f"{label}는 1년에 {cap}까지만 보상합니다.")
        notes.append("중증(암 등 산정특례 대상 질환) 치료로 보고 계산했습니다.")
        return items

    article = ARTICLE_NON_SEVERE
    # 특약2에서는 도수치료·주사도 일반 비급여에 포함되고, MRI만 따로 계산한다
    total = general + manual_therapy + injection
    if total:
        if care == "입원":
            amount = total * 0.5
            how = f"{_won(total)}의 50%"
            if hospital == CLINIC and amount > 3000000:
                amount = 3000000
                how += " (의원·병원 입원은 1회 300만 원 한도)"
            items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
        else:
            amount, how = _deduct_outpatient(total, 50000, 0.5, visits)
            items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
            notes.append("비중증 비급여 통원은 1년에 100일까지 보상합니다.")
    if mri:
        amount, how = _deduct_outpatient(mri, 50000, 0.5, visits)
        items.append(_item("MRI", amount, how, article))
        notes.append("MRI는 1년에 200만 원까지만 보상합니다.")
    return items


def _room(severe, room_fee, room_days):
    """상급병실료 차액(1인실 등): 50%, 하루 평균 10만 원 한도."""
    article = ARTICLE_SEVERE if severe else ARTICLE_NON_SEVERE
    half = room_fee * 0.5
    cap = 100000 * room_days
    if half <= cap:
        return _item("1·2인실 추가 비용(상급병실료 차액)", half, f"{_won(room_fee)}의 50%", article)
    return _item("1·2인실 추가 비용(상급병실료 차액)", cap,
                 f"{_won(room_fee)}의 50%는 {_won(half)}이지만 하루 10만 원 × {room_days}일 = {_won(cap)}까지", article)


def calculate(care, hospital, covered_copay=0, nhis_paid=None, non_covered=0, severe=False,
              manual_therapy=0, injection=0, mri=0, room_fee=0, room_days=0,
              visits=1, generation=None, has_other_insurance=False):
    """예상 보험금을 계산한다.

    care: "통원" 또는 "입원"
    hospital: "의원" 또는 "종합병원"
    covered_copay: 영수증의 급여 '본인부담금' 합계(통원이면 전체 방문의 합계)
    nhis_paid: 영수증의 '공단부담금' (모르면 None. 급여 통원 공제 계산에만 쓴다)
    non_covered: 영수증의 비급여 금액 중 아래 항목(도수치료·주사·MRI·병실료)을 뺀 나머지
    severe: 암 등 중증(산정특례 대상) 질환 치료인지
    manual_therapy, injection, mri: 비급여 도수치료·주사·MRI 금액
    room_fee, room_days: 입원 시 1·2인실 추가 비용과 입원 일수
    visits: 통원 방문 횟수(기본 1). 통원 공제는 방문 1건당 떼므로 횟수만큼 공제가 반복된다
    generation: 가입 세대("1세대"~"4세대" 또는 None). 최신(4세대) 외에는 참고 상한임을 경고한다
    has_other_insurance: 실손을 2개 이상 가입했는지(비례보상 안내용)

    반환값: {"total": 원, "items": [{label, amount, how, article}], "notes": [안내 문구]}
    """
    if care not in ("통원", "입원"):
        raise ValueError("care는 '통원' 또는 '입원'이어야 합니다.")
    if hospital not in HOSPITAL_TYPES:
        raise ValueError("hospital은 '의원' 또는 '종합병원'이어야 합니다.")
    if any(v < 0 for v in (covered_copay, non_covered, manual_therapy, injection, mri, room_fee, room_days)):
        raise ValueError("금액과 일수는 0 이상이어야 합니다.")
    if not isinstance(visits, int) or visits < 1:
        raise ValueError("visits(방문 횟수)는 1 이상의 정수여야 합니다.")
    if generation is not None and generation not in KNOWN_GENERATIONS:
        raise ValueError(f"generation은 {KNOWN_GENERATIONS} 중 하나이거나 None이어야 합니다.")

    notes = []
    items = []
    # 세대 경고: 최신 외 세대는 데이터가 없어 최신 기준으로 계산한 '참고 상한'일 뿐이다(아직 껍데기)
    if generation is not None and generation != LATEST_GENERATION:
        notes.append(
            f"⚠️ {generation} 기준 계산은 아직 지원하지 않습니다. {GENERATION_WARNING.get(generation, '')} "
            f"아래 금액은 최신({LATEST_GENERATION}) 약관으로 계산한 참고 상한이며 실제와 차이가 큽니다. "
            "정확한 금액은 가입하신 보험증권·약관을 확인하세요. (세대별 계산은 추후 추가 예정)")
    elif generation is None:
        notes.append(
            "가입 세대(1~4세대)에 따라 자기부담률과 보장이 달라집니다. 아래는 최신(4세대) 약관 기준입니다.")

    if covered_copay:
        items.append(_covered(care, hospital, covered_copay, nhis_paid, visits if care == "통원" else 1))
        if care == "통원" and nhis_paid is None:
            notes.append("영수증의 '공단부담금'을 알려 주시면 빼는 돈을 더 정확히 계산할 수 있습니다.")
    items += _non_covered(care, hospital, severe, non_covered, manual_therapy, injection, mri,
                          visits if care == "통원" else 1, notes)
    if care == "입원" and room_fee:
        if room_days <= 0:
            raise ValueError("1·2인실 비용이 있으면 입원 일수도 필요합니다.")
        items.append(_room(severe, room_fee, room_days))

    if has_other_insurance:
        notes.append("실손보험을 2개 이상 가입한 경우, 실제 낸 의료비 한도 안에서 나눠 보상합니다(비례보상). "
                     "두 배로 받지 않으며, 합산 보험금은 본인이 낸 금액을 넘지 않습니다.")
    notes.append("연간 가입금액 한도와 이미 받은 보험금, 보상하지 않는 사항(면책: 미용·성형, 치과 보철, "
                 "라식, 예방접종, 산재·자동차보험 중복 등)에 따라 실제 금액은 달라지거나 0원일 수 있습니다. "
                 "이 계산은 참고용이며 실제 지급액은 보험사 심사로 확정됩니다.")
    return {"total": sum(i["amount"] for i in items), "items": items, "notes": notes}
