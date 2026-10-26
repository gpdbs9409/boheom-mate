"""실손 보험금 계산기. 기본 약관(KB 실손의료비보장보험 26.09) 제3조의 공식을 그대로 옮겼다.

GPT가 아니라 이 코드가 금액을 계산한다. GPT는 계산에 넣을 숫자만 모은다.
그래야 숫자가 틀리지 않고, 계산 과정을 조항과 함께 그대로 보여 줄 수 있다.

계산하지 않는 것(결과의 notes로 안내한다):
- 보장 여부(면책 사항 등)는 약관 검색으로 따로 판단한다
- 연간 가입금액 한도, 이미 받은 횟수·금액에 따른 연간 한도
- 국민건강보험을 적용받지 못한 경우(40% 보상), 의료비 감면을 받은 경우
"""

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


def _covered(care, hospital, copay, nhis_paid):
    """급여: 건강보험이 적용된 진료에서 내가 낸 돈(본인부담금)."""
    if care == "입원":
        return _item("건강보험 되는 진료비(급여)", copay * 0.8,
                     f"낸 돈 {_won(copay)}의 80%", ARTICLE_COVERED)

    base = 10000 if hospital == CLINIC else 20000
    candidates = {f"{_won(base)}(병원 종류 기준)": base, f"낸 돈의 20%({_won(copay * 0.2)})": copay * 0.2}
    if nhis_paid is not None and copay + nhis_paid > 0:
        # 약관의 '보장대상의료비 × 건강보험 본인부담률'. 본인부담률 = 본인부담금 ÷ (본인부담금 + 공단부담금)
        by_rate = copay * copay / (copay + nhis_paid)
        candidates[f"낸 돈 × 본인부담률({_won(by_rate)})"] = by_rate
    label, deduct = max(candidates.items(), key=lambda kv: kv[1])
    return _item("건강보험 되는 진료비(급여)", copay - deduct,
                 f"낸 돈 {_won(copay)} − 빼는 돈 {_won(deduct)} (가장 큰 값: {label})", ARTICLE_COVERED)


def _deduct_outpatient(amount, fixed, rate):
    """비급여 통원 공제: 정액과 비율 중 큰 금액."""
    deduct = max(fixed, amount * rate)
    return amount - deduct, f"{_won(amount)} − 빼는 돈 {_won(deduct)} ({_won(fixed)}과 {int(rate * 100)}% 중 큰 금액)"


def _non_covered(care, hospital, severe, general, manual_therapy, injection, mri, notes):
    """비급여: 건강보험이 안 되는 진료비. 중증(암 등 산정특례 대상)이면 특약1, 아니면 특약2."""
    items = []
    if severe:
        article = ARTICLE_SEVERE
        if general:
            if care == "입원":
                items.append(_item("건강보험 안 되는 진료비(비급여)", general * 0.7,
                                   f"{_won(general)}의 70%", article))
            else:
                amount, how = _deduct_outpatient(general, 30000, 0.3)
                items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
        # 3대 비급여(도수치료·체외충격파, 주사, MRI)는 따로 1회당 공제한다
        for label, value, cap in (("도수치료·체외충격파", manual_therapy, "연 350만 원·50회"),
                                  ("비급여 주사", injection, "연 250만 원·50회"),
                                  ("MRI", mri, "연 300만 원")):
            if value:
                amount, how = _deduct_outpatient(value, 30000, 0.3)
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
            amount, how = _deduct_outpatient(total, 50000, 0.5)
            items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
            notes.append("비중증 비급여 통원은 1년에 100일까지 보상합니다.")
    if mri:
        amount, how = _deduct_outpatient(mri, 50000, 0.5)
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
              manual_therapy=0, injection=0, mri=0, room_fee=0, room_days=0):
    """예상 보험금을 계산한다.

    care: "통원" 또는 "입원"
    hospital: "의원" 또는 "종합병원"
    covered_copay: 영수증의 급여 '본인부담금' 합계
    nhis_paid: 영수증의 '공단부담금' (모르면 None. 급여 통원 공제 계산에만 쓴다)
    non_covered: 영수증의 비급여 금액 중 아래 항목(도수치료·주사·MRI·병실료)을 뺀 나머지
    severe: 암 등 중증(산정특례 대상) 질환 치료인지
    room_fee, room_days: 입원 시 1·2인실 추가 비용과 입원 일수

    반환값: {"total": 원, "items": [{label, amount, how, article}], "notes": [안내 문구]}
    """
    if care not in ("통원", "입원"):
        raise ValueError("care는 '통원' 또는 '입원'이어야 합니다.")
    if hospital not in HOSPITAL_TYPES:
        raise ValueError("hospital은 '의원' 또는 '종합병원'이어야 합니다.")
    if any(v < 0 for v in (covered_copay, non_covered, manual_therapy, injection, mri, room_fee, room_days)):
        raise ValueError("금액과 일수는 0 이상이어야 합니다.")

    notes = []
    items = []
    if covered_copay:
        items.append(_covered(care, hospital, covered_copay, nhis_paid))
        if care == "통원" and nhis_paid is None:
            notes.append("영수증의 '공단부담금'을 알려 주시면 빼는 돈을 더 정확히 계산할 수 있습니다.")
    items += _non_covered(care, hospital, severe, non_covered, manual_therapy, injection, mri, notes)
    if care == "입원" and room_fee:
        if room_days <= 0:
            raise ValueError("1·2인실 비용이 있으면 입원 일수도 필요합니다.")
        items.append(_room(severe, room_fee, room_days))

    notes.append("연간 가입금액 한도와 이미 받은 보험금, 보상하지 않는 사항(면책)에 따라 실제 금액은 달라질 수 있습니다.")
    return {"total": sum(i["amount"] for i in items), "items": items, "notes": notes}
