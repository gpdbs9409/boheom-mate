"""약관 값 검증(policy_rules.verify) 테스트. LLM 없이 검증 규칙만 확인한다.

실행: .venv/bin/python tests/test_policy_rules.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from boheom_mate.core import policy_rules as pr

COVERED = ("＜표1＞ 통원항목별 공제금액 「의료법」 제3조제2항에 의한 의료기관(동법 제3조의3에 의한 종합병원은 제외) 1만원, "
           "보장대상 의료비의 20%, 보장대상의료비에 건강보험 본인부담률을 곱한 금액 중 큰 금액 "
           "「의료법」 제3조의4에 의한 상급종합병원, 종합병원에서의 외래 2만원, 보장대상 의료비의 20% 중 큰 금액. "
           "입원 본인부담금의 80%에 해당하는 금액")

CASES = [
    # (설명, 키, 값, 인용, 통과해야 하나)
    ("의원 공제 1만원", "covered_outpatient_clinic_deduct", 10000, "종합병원은 제외) 1만원", True),
    ("종합병원 공제 2만원", "covered_outpatient_general_deduct", 20000, "종합병원에서의 외래 2만원", True),
    ("종합병원 공제에 의원 구절 인용 → 막음", "covered_outpatient_general_deduct", 10000, "종합병원은 제외) 1만원", False),
    ("입원 보상 80%", "covered_inpatient_pay_rate", 0.8, "본인부담금의 80%에 해당하는 금액", True),
    ("공제 비율 20%", "covered_outpatient_rate", 0.2, "보장대상 의료비의 20%, 보장대상의료비에", True),
    ("공제 비율을 0.8로 잘못 뽑음 → 막음", "covered_outpatient_rate", 0.8, "보장대상 의료비의 20%, 보장대상의료비에", False),
    ("약관에 없는 구절 → 막음", "covered_outpatient_clinic_deduct", 10000, "의원 공제금액은 1만원입니다", False),
    ("숫자가 다른 값 → 막음", "covered_outpatient_clinic_deduct", 15000, "종합병원은 제외) 1만원", False),
    ("1만원이 11만원 안의 숫자로 맞지 않게", "covered_outpatient_clinic_deduct", 10000, "외래 2만원", False),
    ("값 없음 → 막음", "covered_outpatient_clinic_deduct", None, None, False),
    ("비율 범위 밖 → 막음", "covered_inpatient_pay_rate", 80, "본인부담금의 80%에 해당하는 금액", False),
]


def main():
    failed = 0
    for desc, key, value, quote, should_pass in CASES:
        reason = pr.verify(key, value, quote, COVERED)
        ok = (reason is None) == should_pass
        failed += not ok
        print(f"{'OK ' if ok else 'FAIL'} {desc}" + ("" if ok else f" (이유: {reason})"))
    # 필수 값 구분: 연간 한도 안내 같은 값은 없어도 계산할 수 있다
    optional = [k for k, f in pr.FIELDS.items() if not f[2]]
    if not all("cap" in k or "days" in k for k in optional):
        print("FAIL 필수가 아닌 값은 연간 한도·일수 안내용이어야 함", optional); failed += 1
    print(f"\n실패 {failed}개" if failed else f"\n{len(CASES)}/{len(CASES)} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
