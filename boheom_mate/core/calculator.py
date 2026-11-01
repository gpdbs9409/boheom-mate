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

# KB손보 간편가입실손의료비보장보험(26.09) 보통약관 제3조를 사람이 확인한 값.
# 테스트 전용 기준값이다. calculate_simple의 기본값으로 쓰지 않는다(검증되지 않은 값으로 계산하지 않기 위해).
KB_SIMPLE_RULES = {
    "simple_inpatient_deduct": 100000,
    "simple_inpatient_rate": 0.3,
    "simple_inpatient_annual_deduct_cap": 2000000,
    "simple_inpatient_case_cap": 50000000,
    "simple_outpatient_deduct": 20000,
    "simple_outpatient_rate": 0.3,
    "simple_outpatient_per_visit_cap": 200000,
    "simple_outpatient_annual_visits": 180,
    "simple_room_pay_rate": 0.5,
    "simple_room_daily_cap": 100000,
    "simple_no_nhis_pay_rate": 0.4,
}

DISCLAIMER = "실제 지급 여부와 금액은 보험사 심사로 확정됩니다."

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
ARTICLE_SIMPLE_COVERED = "보통약관 제3조 (보상내용)"
ARTICLE_SIMPLE_EXCLUSION = "보통약관 제4조 (보상하지 않는 사항)"

# 간편가입 상품이 보상하지 않는 비급여 3항목(면책). 키는 policy_rules의 exclusion_items 키와 같다
SIMPLE_EXCLUDED_ITEMS = (("manual_therapy", "도수치료·체외충격파·증식치료"),
                         ("injection", "비급여 주사"),
                         ("mri", "MRI·MRA"))
SIMPLE_INJECTION_GUIDE = "항암제·항생제·희귀의약품 주사는 보상되므로 일반 비급여로 알려 주세요."
SIMPLE_HOSPITAL_NOTE = "이 상품은 병원 종류에 따라 공제금액이 달라지지 않습니다."


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


_BASE_ENDING = "이 계산은 참고용이며 실제 지급액은 보험사 심사로 확정됩니다."
_SIMPLE_ENDING = "이 계산은 참고용이며 " + DISCLAIMER


def _common_notes(generation, has_other_insurance, ending):
    """기본형·간편가입 공통 안내. (앞에 붙일 notes, 끝에 붙일 notes)를 돌려준다.

    ending은 마지막 면책·연간 한도 안내의 끝 문장이다. 기본형은 기존 문장(_BASE_ENDING)을 그대로 쓴다.
    """
    head = []
    # 세대 경고: 최신 외 세대는 데이터가 없어 최신 기준으로 계산한 '참고 상한'일 뿐이다(아직 껍데기)
    if generation is not None and generation != LATEST_GENERATION:
        head.append(
            f"⚠️ {generation} 기준 계산은 아직 지원하지 않습니다. {GENERATION_WARNING.get(generation, '')} "
            f"아래 금액은 최신({LATEST_GENERATION}) 약관으로 계산한 참고 상한이며 실제와 차이가 큽니다. "
            "정확한 금액은 가입하신 보험증권·약관을 확인하세요.")
    elif generation is None:
        head.append(
            "가입 세대(1~4세대)에 따라 자기부담률과 보장이 달라집니다. 아래는 최신(4세대) 약관 기준입니다.")

    tail = []
    if has_other_insurance:
        tail.append("실손보험을 2개 이상 가입한 경우, 실제 낸 의료비 한도 안에서 나눠 보상합니다(비례보상). "
                    "두 배로 받지 않으며, 합산 보험금은 본인이 낸 금액을 넘지 않습니다.")
    tail.append("연간 가입금액 한도와 이미 받은 보험금, 보상하지 않는 사항(면책: 미용·성형, 치과 보철, "
                "라식, 예방접종, 산재·자동차보험 중복 등)에 따라 실제 금액은 달라지거나 0원일 수 있습니다. "
                + ending)
    return head, tail


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
    notes, closing = _common_notes(generation, has_other_insurance, _BASE_ENDING)
    items = []

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

    notes += closing
    return {"total": sum(i["amount"] for i in items), "items": items, "notes": notes, "rules_used": sorted(r.used)}


def _quote_text(value):
    """exclusion_quotes 값은 인용문 문자열 또는 policy_rules의 {"quote", "meta"}."""
    if isinstance(value, dict):
        return value.get("quote")
    return value


def _simple_inpatient(r, C, prior_deductible, nhis_applied, notes):
    article = r.articles["covered"]
    fixed, rate = r("simple_inpatient_deduct"), r("simple_inpatient_rate")
    deduct = min(C, max(fixed, C * rate))  # 실제로 뗀 공제액(C보다 클 수 없음)
    base = C - deduct
    cap = r("simple_inpatient_annual_deduct_cap")
    prior = prior_deductible or 0
    excess = max(0, min(deduct, prior + deduct - cap))  # 이번 공제액 중 연간 상한을 넘는 부분
    pay = base + excess
    how = (f"보장대상의료비 {_won(C)} − 빼는 돈 {_won(deduct)} "
           f"({_won(fixed)}과 {_pct(rate)} 중 큰 금액)")
    if excess:
        how += f" + 연간 공제 상한 {_cap_text(cap)} 초과분 {_won(excess)}"
    if prior_deductible is None:
        notes.append(f"올해(계약해당일부터 1년) 이미 낸 입원 공제액이 있으면 연간 {_cap_text(cap)} "
                     "초과분이 더 보상될 수 있습니다.")
    if not nhis_applied:
        no_nhis = r("simple_no_nhis_pay_rate")
        pay *= no_nhis
        how += f", 국민건강보험 미적용으로 {_pct(no_nhis)}"
    case_cap = r.get("simple_inpatient_case_cap")
    if case_cap and pay > case_cap:
        pay = case_cap
        how += f" (하나의 상해·질병당 {_cap_text(case_cap)} 한도)"
        notes.append(f"하나의 상해·질병당 보험가입금액 {_cap_text(case_cap)}까지만 보상합니다.")
    return _item("입원 의료비(급여+비급여)", pay, how, article)


def _simple_outpatient(r, C, visits, prior_visits, nhis_applied, notes):
    article = r.articles["covered"]
    fixed, rate = r("simple_outpatient_deduct"), r("simple_outpatient_rate")
    per_cap = r("simple_outpatient_per_visit_cap")
    per = C / visits  # 1회 방문당 보장대상의료비
    deduct = max(fixed, per * rate)
    paid1 = per - deduct
    how = f"1회당 {_won(per)} − 빼는 돈 {_won(deduct)} ({_won(fixed)}과 {_pct(rate)} 중 큰 금액)"
    if not nhis_applied:
        no_nhis = r("simple_no_nhis_pay_rate")
        paid1 *= no_nhis
        how += f", 국민건강보험 미적용으로 {_pct(no_nhis)}"
    paid1 = min(max(paid1, 0), per_cap)
    if per - deduct > 0 and paid1 == per_cap:
        how += f" (1회 {_cap_text(per_cap)} 한도)"
    limit = r("simple_outpatient_annual_visits")
    count = min(visits, max(0, limit - prior_visits))
    if count != 1:
        how += f" × {count}회"
    if count < visits:
        notes.append(f"연간 방문 한도 {limit}회를 넘은 {visits - count}회는 보상하지 않았습니다.")
    return _item("통원 의료비(급여+비급여)", paid1 * count, how, article)


def calculate_simple(care, covered_copay=0, non_covered=0, manual_therapy=0, injection=0, mri=0,
                     room_fee=0, room_days=0, visits=1, prior_deductible=None, prior_visits=0,
                     nhis_applied=True, hospital=None, generation=None, has_other_insurance=False,
                     *, rules=None, articles=None, exclusion_quotes=None):
    """간편가입 프로필의 예상 보험금을 계산한다.

    이 상품은 보통약관 제3조가 급여 본인부담금과 비급여를 합한 금액(보장대상의료비)을 보상한다.
    care: "통원" 또는 "입원"
    covered_copay, non_covered: 급여 본인부담금, 비급여 금액(도수치료·주사·MRI·병실료 제외). 처방조제비는 넣지 않는다
    manual_therapy, injection, mri: 보상하지 않는 비급여 3항목. 0원 항목으로 표시만 한다
    room_fee, room_days: 입원 시 상급병실료 차액과 입원 일수
    visits: 통원 방문 횟수. prior_visits: 올해 이미 보상받은 통원 횟수
    prior_deductible: 올해(계약해당일부터 1년) 이미 낸 입원 공제액(모르면 None → 0으로 계산하고 안내)
    nhis_applied: 국민건강보험을 적용받았는지(아니면 미적용 보상 비율을 곱한다)
    hospital: 받으면 공제에 쓰지 않고 안내만 한다
    rules: 검증된 약관 값 {키: 숫자}. 필수(없으면 ValueError). KB_SIMPLE_RULES를 기본값으로 쓰지 않는다
    articles: 근거 표시용 조항 이름 {"covered"}. exclusion_quotes: 면책 3항목 인용문 {키: 문자열 또는 {"quote", "meta"}}

    반환값: {"total": 원, "items": [{label, amount, how, article}], "notes": [안내 문구], "rules_used": [쓴 키]}
    """
    if not rules:
        raise ValueError("간편가입 계산에는 검증된 약관 값(rules)이 필요합니다.")
    if care not in ("통원", "입원"):
        raise ValueError("care는 '통원' 또는 '입원'이어야 합니다.")
    if hospital is not None and hospital not in HOSPITAL_TYPES:
        raise ValueError("hospital은 '의원' 또는 '종합병원'이거나 None이어야 합니다.")
    if any(v < 0 for v in (covered_copay, non_covered, manual_therapy, injection, mri, room_fee, room_days)):
        raise ValueError("금액과 일수는 0 이상이어야 합니다.")
    if not isinstance(visits, int) or visits < 1:
        raise ValueError("visits(방문 횟수)는 1 이상의 정수여야 합니다.")
    if prior_deductible is not None and (not isinstance(prior_deductible, int) or prior_deductible < 0):
        raise ValueError("prior_deductible(올해 낸 입원 공제액)은 None 또는 0 이상의 정수여야 합니다.")
    if prior_visits is None:
        prior_visits = 0
    if not isinstance(prior_visits, int) or prior_visits < 0:
        raise ValueError("prior_visits(올해 통원 횟수)는 0 이상의 정수여야 합니다.")
    if generation is not None and generation not in KNOWN_GENERATIONS:
        raise ValueError(f"generation은 {KNOWN_GENERATIONS} 중 하나이거나 None이어야 합니다.")
    if care == "입원" and room_fee and room_days <= 0:
        raise ValueError("1·2인실 비용이 있으면 입원 일수도 필요합니다.")

    r = _Rules(rules, {"covered": ARTICLE_SIMPLE_COVERED, **(articles or {})})
    quotes = exclusion_quotes or {}
    notes, closing = _common_notes(generation, has_other_insurance, _SIMPLE_ENDING)
    items = []
    used_extra = set()

    C = covered_copay + non_covered  # 보장대상의료비. 면책 3항목과 병실료는 넣지 않는다
    if C:
        if care == "입원":
            items.append(_simple_inpatient(r, C, prior_deductible, nhis_applied, notes))
        else:
            items.append(_simple_outpatient(r, C, visits, prior_visits, nhis_applied, notes))

    if care == "입원" and room_fee:
        rate, daily = r("simple_room_pay_rate"), r("simple_room_daily_cap")
        paid, cap = room_fee * rate, daily * room_days
        if paid <= cap:
            how = f"{_won(room_fee)}의 {_pct(rate)}"
        else:
            how = f"{_won(room_fee)}의 {_pct(rate)}는 {_won(paid)}이지만 하루 {_cap_text(daily)} × {room_days}일 = {_won(cap)}까지"
        items.append(_item("1·2인실 추가 비용(상급병실료 차액)", min(paid, cap), how, r.articles["covered"]))

    excluded = [(key, label) for key, label in SIMPLE_EXCLUDED_ITEMS
                if {"manual_therapy": manual_therapy, "injection": injection, "mri": mri}[key] > 0]
    for key, label in excluded:
        items.append(_item(label, 0, "이 상품은 보상하지 않는 항목", ARTICLE_SIMPLE_EXCLUSION))
        used_extra.add(f"exclusion:{key}")
        quote = _quote_text(quotes.get(key))
        if quote:
            notes.append(f"{label}: 약관 {ARTICLE_SIMPLE_EXCLUSION} \"{quote}\"")
        else:
            notes.append(f"{label}는 이 상품에서 보상하지 않습니다({ARTICLE_SIMPLE_EXCLUSION}).")
    if excluded:
        notes.append(SIMPLE_INJECTION_GUIDE)

    if hospital is not None:
        notes.append(SIMPLE_HOSPITAL_NOTE)

    notes += closing
    return {"total": sum(i["amount"] for i in items), "items": items, "notes": notes,
            "rules_used": sorted(r.used | used_extra)}


# remaining_limit의 프로필 이름. policy_rules.PROFILE_BASE·PROFILE_SIMPLE과 같은 값이다
# (calculator는 다른 모듈을 import하지 않는다)
_PROFILE_BASE, _PROFILE_SIMPLE = "기본형", "간편가입"

LIMIT_UNVERIFIED = "약관에서 한도를 확인하지 못함"
LIMIT_NEEDS_ITEM_RECEIVED = "항목별로 받은 금액을 알아야 계산할 수 있음"

# (라벨, 한도 키, 사용량 인자 이름, 단위). design.md 3.2 표
_SIMPLE_LIMITS = (
    ("연간 입원 공제 상한까지 남은 공제액", "simple_inpatient_annual_deduct_cap", "prior_deductible", "원"),
    ("남은 통원 방문 횟수", "simple_outpatient_annual_visits", "prior_visits", "회"),
    ("같은 상해·질병의 남은 가입금액(참고)", "simple_inpatient_case_cap", "received", "원"),
)
_BASE_LIMITS = (
    ("비중증 비급여 통원 남은 일수", "non_severe_outpatient_annual_days", "prior_visits", "일"),
)
# 기본형 3대 비급여 연간 한도. 메모에 항목별 기수령액이 없어 남은 금액은 계산하지 않는다
_BASE_ITEM_CAPS = (
    ("도수치료·체외충격파 연간 한도(중증)", "severe_manual_therapy_annual_cap"),
    ("비급여 주사 연간 한도(중증)", "severe_injection_annual_cap"),
    ("MRI 연간 한도(중증)", "severe_mri_annual_cap"),
    ("MRI 연간 한도(비중증)", "non_severe_mri_annual_cap"),
)


def remaining_limit(profile, rules, received=None, prior_deductible=None, prior_visits=None):
    """올해(Policy_Year) 남은 한도를 계산한다.

    profile: "기본형" 또는 "간편가입"
    rules: 검증된 약관 값 {키: 숫자}. 없는 키(또는 None)는 미검증 한도로 보고 limit·remaining을 None으로 둔다
    received: 올해 받은 보험금 합계(원), prior_deductible: 올해 낸 입원 공제액(원),
    prior_visits: 올해 통원 방문 횟수. 모두 None(모름) 또는 0 이상 정수. None이면 그 항목의 remaining은 None

    반환값: {"items": [{"label", "limit", "used", "remaining", "unit", "key"}], "notes": [...], "rules_used": [...]}
    remaining = max(0, limit − used)
    """
    if profile not in (_PROFILE_BASE, _PROFILE_SIMPLE):
        raise ValueError(f"profile은 '{_PROFILE_BASE}' 또는 '{_PROFILE_SIMPLE}'이어야 합니다.")
    used_by = {"received": received, "prior_deductible": prior_deductible, "prior_visits": prior_visits}
    for name, value in used_by.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"{name}은 None 또는 0 이상의 정수여야 합니다.")

    r = _Rules(rules or {}, {})
    items, notes = [], []

    def add(label, key, used, unit):
        limit = r.get(key)
        if limit is None:
            remaining = None
            notes.append(f"{label}: {LIMIT_UNVERIFIED}")
        else:
            remaining = None if used is None else max(0, limit - used)
        items.append({"label": label, "limit": limit, "used": used, "remaining": remaining,
                      "unit": unit, "key": key})

    if profile == _PROFILE_SIMPLE:
        for label, key, arg, unit in _SIMPLE_LIMITS:
            add(label, key, used_by[arg], unit)
    else:
        for label, key, arg, unit in _BASE_LIMITS:
            add(label, key, used_by[arg], unit)
        for label, key in _BASE_ITEM_CAPS:
            limit = r.get(key)
            items.append({"label": label, "limit": limit, "used": None, "remaining": None,
                          "unit": "원", "key": key})
            notes.append(f"{label}: {LIMIT_UNVERIFIED if limit is None else LIMIT_NEEDS_ITEM_RECEIVED}")

    return {"items": items, "notes": notes, "rules_used": sorted(r.used)}
