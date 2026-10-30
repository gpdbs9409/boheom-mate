"""실손 보험금 계산기. 약관 제3조(보장종목별 보상내용)의 공식으로 예상 보험금을 계산한다.

계산에 쓰는 값(보상 비율, 공제금액, 한도)은 코드에 박아 두지 않고 rules로 받는다.
상담 중에는 policy_rules가 상품 약관 원문에서 뽑고, 원문 인용으로 검증한 값을 넘긴다.
GPT가 아니라 이 코드가 금액을 계산한다. GPT는 계산에 넣을 숫자(영수증 금액 등)만 모은다.

계산하지 않는 것(결과의 notes로 안내한다):
- 보장 여부(면책 사항 등)는 약관 검색으로 따로 판단한다
- 연간 가입금액 한도, 이미 받은 횟수·금액에 따른 연간 한도
- 국민건강보험을 적용받지 못한 경우(40% 보상), 의료비 감면을 받은 경우
"""

# KB손보 실손의료비보장보험(26.09) 제3조를 사람이 확인한 값.
# 테스트의 기준값이고, policy_rules가 이 약관에서 뽑은 값이 맞는지 검증할 때 비교 대상으로 쓴다.
KB_BASE_RULES = {
    "covered_inpatient_pay_rate": 0.8,
    "covered_outpatient_clinic_deduct": 10000,
    "covered_outpatient_general_deduct": 20000,
    "covered_outpatient_rate": 0.2,
    "severe_inpatient_pay_rate": 0.7,
    "severe_outpatient_deduct": 30000,
    "severe_outpatient_rate": 0.3,
    "severe_special_deduct": 30000,
    "severe_special_rate": 0.3,
    "severe_manual_therapy_annual_cap": 3500000,
    "severe_injection_annual_cap": 2500000,
    "severe_mri_annual_cap": 3000000,
    "non_severe_inpatient_pay_rate": 0.5,
    "non_severe_clinic_inpatient_cap": 3000000,
    "non_severe_outpatient_deduct": 50000,
    "non_severe_outpatient_rate": 0.5,
    "non_severe_outpatient_annual_days": 100,
    "non_severe_mri_deduct": 50000,
    "non_severe_mri_rate": 0.5,
    "non_severe_mri_annual_cap": 2000000,
    "room_pay_rate": 0.5,
    "room_daily_cap": 100000,
}

# 세대 구분. 지금 약관(4세대)의 값으로 계산하고, 다른 세대는 '참고 상한'으로 안내한다.
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


class _Rules:
    """약관 값을 꺼낼 때 어떤 값을 썼는지 기록한다(근거 조항 표시용)."""

    def __init__(self, values, articles):
        self.values, self.articles, self.used = values, articles, set()

    def __call__(self, key):
        self.used.add(key)
        return self.values[key]

    def get(self, key):
        """없어도 되는 값(연간 한도 안내 등). 없으면 None."""
        if self.values.get(key) is None:
            return None
        return self(key)


def _won(n):
    return f"{round(n):,}원"


def _pct(rate):
    return f"{round(rate * 100)}%"


def _cap_text(n):
    return f"{n // 10000:,}만 원" if n % 10000 == 0 else _won(n)


def _item(label, amount, how, article):
    return {"label": label, "amount": max(0, round(amount)), "how": how, "article": article}


def _covered(r, care, hospital, copay, nhis_paid, visits):
    """급여: 건강보험이 적용된 진료에서 내가 낸 돈(본인부담금).

    통원 공제(정액/비율)는 방문 1건당 뗀다. copay는 전체 방문 합계로 보고,
    1회 평균(copay/visits)에 공제를 적용한 뒤 횟수를 곱한다.
    """
    article = r.articles["covered"]
    if care == "입원":
        rate = r("covered_inpatient_pay_rate")
        return _item("건강보험 되는 진료비(급여)", copay * rate, f"낸 돈 {_won(copay)}의 {_pct(rate)}", article)

    per = copay / visits  # 1회 방문당 낸 돈
    base = r("covered_outpatient_clinic_deduct") if hospital == CLINIC else r("covered_outpatient_general_deduct")
    rate = r("covered_outpatient_rate")
    candidates = {f"{_won(base)}(병원 종류 기준)": base, f"낸 돈의 {_pct(rate)}({_won(per * rate)})": per * rate}
    if nhis_paid is not None and copay + nhis_paid > 0:
        # 약관의 '보장대상의료비 × 건강보험 본인부담률'. 본인부담률 = 본인부담금 ÷ (본인부담금 + 공단부담금)
        by_rate = per * copay / (copay + nhis_paid)
        candidates[f"낸 돈 × 본인부담률({_won(by_rate)})"] = by_rate
    label, deduct_per = max(candidates.items(), key=lambda kv: kv[1])
    paid = (per - deduct_per) * visits
    visit_note = f" × {visits}회" if visits > 1 else ""
    return _item("건강보험 되는 진료비(급여)", paid,
                 f"1회당 낸 돈 {_won(per)} − 빼는 돈 {_won(deduct_per)} (가장 큰 값: {label}){visit_note}", article)


def _deduct_outpatient(amount, fixed, rate, visits=1):
    """비급여 통원 공제: 정액과 비율 중 큰 금액. 공제는 방문 1건당 뗀다(visits만큼 반복)."""
    per = amount / visits
    deduct_per = max(fixed, per * rate)
    paid = (per - deduct_per) * visits
    visit_note = f" × {visits}회" if visits > 1 else ""
    return paid, f"1회당 {_won(per)} − 빼는 돈 {_won(deduct_per)} ({_won(fixed)}과 {_pct(rate)} 중 큰 금액){visit_note}"


def _non_covered(r, care, hospital, severe, general, manual_therapy, injection, mri, visits, notes):
    """비급여: 건강보험이 안 되는 진료비. 중증(암 등 산정특례 대상)이면 중증 특약, 아니면 비중증 특약.

    통원 공제는 방문 1건당 뗀다(visits). 입원은 visits를 쓰지 않는다.
    """
    items = []
    if severe:
        article = r.articles["severe"]
        if general:
            if care == "입원":
                rate = r("severe_inpatient_pay_rate")
                items.append(_item("건강보험 안 되는 진료비(비급여)", general * rate,
                                   f"{_won(general)}의 {_pct(rate)}", article))
            else:
                amount, how = _deduct_outpatient(general, r("severe_outpatient_deduct"), r("severe_outpatient_rate"), visits)
                items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
        # 3대 비급여(도수치료·체외충격파, 주사, MRI)는 따로 1회당 공제한다
        for label, value, cap_key in (("도수치료·체외충격파", manual_therapy, "severe_manual_therapy_annual_cap"),
                                      ("비급여 주사", injection, "severe_injection_annual_cap"),
                                      ("MRI", mri, "severe_mri_annual_cap")):
            if value:
                amount, how = _deduct_outpatient(value, r("severe_special_deduct"), r("severe_special_rate"), visits)
                items.append(_item(label, amount, how, article))
                cap = r.get(cap_key)
                if cap:
                    notes.append(f"{label}는 1년에 {_cap_text(cap)}까지만 보상합니다.")
        notes.append("중증(암 등 산정특례 대상 질환) 치료로 보고 계산했습니다.")
        return items

    article = r.articles["non_severe"]
    # 비중증 특약에서는 도수치료·주사도 일반 비급여에 포함되고, MRI만 따로 계산한다
    total = general + manual_therapy + injection
    if total:
        if care == "입원":
            rate = r("non_severe_inpatient_pay_rate")
            amount = total * rate
            how = f"{_won(total)}의 {_pct(rate)}"
            cap = r("non_severe_clinic_inpatient_cap") if hospital == CLINIC else None
            if cap and amount > cap:
                amount = cap
                how += f" (의원·병원 입원은 1회 {_cap_text(cap)} 한도)"
            items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
        else:
            amount, how = _deduct_outpatient(total, r("non_severe_outpatient_deduct"), r("non_severe_outpatient_rate"), visits)
            items.append(_item("건강보험 안 되는 진료비(비급여)", amount, how, article))
            days = r.get("non_severe_outpatient_annual_days")
            if days:
                notes.append(f"비중증 비급여 통원은 1년에 {days}일까지 보상합니다.")
    if mri:
        amount, how = _deduct_outpatient(mri, r("non_severe_mri_deduct"), r("non_severe_mri_rate"), visits)
        items.append(_item("MRI", amount, how, article))
        cap = r.get("non_severe_mri_annual_cap")
        if cap:
            notes.append(f"MRI는 1년에 {_cap_text(cap)}까지만 보상합니다.")
    return items


def _room(r, severe, room_fee, room_days):
    """상급병실료 차액(1인실 등): 비율만큼, 하루 평균 한도까지."""
    article = r.articles["severe" if severe else "non_severe"]
    rate, daily = r("room_pay_rate"), r("room_daily_cap")
    paid = room_fee * rate
    cap = daily * room_days
    if paid <= cap:
        return _item("1·2인실 추가 비용(상급병실료 차액)", paid, f"{_won(room_fee)}의 {_pct(rate)}", article)
    return _item("1·2인실 추가 비용(상급병실료 차액)", cap,
                 f"{_won(room_fee)}의 {_pct(rate)}는 {_won(paid)}이지만 하루 {_cap_text(daily)} × {room_days}일 = {_won(cap)}까지",
                 article)


def calculate(care, hospital, covered_copay=0, nhis_paid=None, non_covered=0, severe=False,
              manual_therapy=0, injection=0, mri=0, room_fee=0, room_days=0,
              visits=1, generation=None, has_other_insurance=False, rules=None, articles=None):
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
    rules: 약관 값 {키: 숫자}. 없으면 KB_BASE_RULES(테스트 기준값)
    articles: 근거 표시용 조항 이름 {"covered", "severe", "non_severe"}

    반환값: {"total": 원, "items": [{label, amount, how, article}], "notes": [안내 문구], "rules_used": [쓴 약관 값 키]}
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

    r = _Rules(rules or KB_BASE_RULES, articles or {
        "covered": ARTICLE_COVERED, "severe": ARTICLE_SEVERE, "non_severe": ARTICLE_NON_SEVERE})
    notes = []
    items = []
    # 세대 경고: 최신 외 세대는 데이터가 없어 최신 기준으로 계산한 '참고 상한'일 뿐이다(아직 껍데기)
    if generation is not None and generation != LATEST_GENERATION:
        notes.append(
            f"⚠️ {generation} 기준 계산은 아직 지원하지 않습니다. {GENERATION_WARNING.get(generation, '')} "
            f"아래 금액은 최신({LATEST_GENERATION}) 약관으로 계산한 참고 상한이며 실제와 차이가 큽니다. "
            "정확한 금액은 가입하신 보험증권·약관을 확인하세요.")
    elif generation is None:
        notes.append(
            "가입 세대(1~4세대)에 따라 자기부담률과 보장이 달라집니다. 아래는 최신(4세대) 약관 기준입니다.")

    if covered_copay:
        items.append(_covered(r, care, hospital, covered_copay, nhis_paid, visits if care == "통원" else 1))
        if care == "통원" and nhis_paid is None:
            notes.append("영수증의 '공단부담금'을 알려 주시면 빼는 돈을 더 정확히 계산할 수 있습니다.")
    items += _non_covered(r, care, hospital, severe, non_covered, manual_therapy, injection, mri,
                          visits if care == "통원" else 1, notes)
    if care == "입원" and room_fee:
        if room_days <= 0:
            raise ValueError("1·2인실 비용이 있으면 입원 일수도 필요합니다.")
        items.append(_room(r, severe, room_fee, room_days))

    if has_other_insurance:
        notes.append("실손보험을 2개 이상 가입한 경우, 실제 낸 의료비 한도 안에서 나눠 보상합니다(비례보상). "
                     "두 배로 받지 않으며, 합산 보험금은 본인이 낸 금액을 넘지 않습니다.")
    notes.append("연간 가입금액 한도와 이미 받은 보험금, 보상하지 않는 사항(면책: 미용·성형, 치과 보철, "
                 "라식, 예방접종, 산재·자동차보험 중복 등)에 따라 실제 금액은 달라지거나 0원일 수 있습니다. "
                 "이 계산은 참고용이며 실제 지급액은 보험사 심사로 확정됩니다.")
    return {"total": sum(i["amount"] for i in items), "items": items, "notes": notes, "rules_used": sorted(r.used)}
