"""계산기 테스트. 기대값은 약관 제3조 공식으로 손으로 계산한 값이다.

실행: .venv/bin/python tests/test_calculator.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from boheom_mate.core import calculator as c

CASES = [
    # (설명, 입력, 기대 총액)
    ("감기, 동네 의원 급여 15,000원 → 15,000 − 1만", dict(care="통원", hospital="의원", covered_copay=15000), 5000),
    ("대학병원 급여 15,000원 → 공제 2만이 더 커서 0원", dict(care="통원", hospital="종합병원", covered_copay=15000), 0),
    ("의원 급여 100,000원 → 20%(2만)가 1만보다 큼", dict(care="통원", hospital="의원", covered_copay=100000), 80000),
    ("본인부담률 30%(본인 3만, 공단 7만) → 3만×30%=9천 < 1만", dict(care="통원", hospital="의원", covered_copay=30000, nhis_paid=70000), 20000),
    ("본인부담률 60%(본인 15만, 공단 10만) → 15만×60%=9만이 20%(3만)보다 큼", dict(care="통원", hospital="종합병원", covered_copay=150000, nhis_paid=100000), 60000),
    ("급여 입원 100만 원 → 80%", dict(care="입원", hospital="종합병원", covered_copay=1000000), 800000),
    ("비중증 도수치료 10만 원 통원 → 10만 − max(5만, 5만)", dict(care="통원", hospital="의원", manual_therapy=100000), 50000),
    ("비중증 비급여 통원 4만 원 → 공제 5만이 더 커서 0원", dict(care="통원", hospital="의원", non_covered=40000), 0),
    ("비중증 비급여 통원 30만 원 → 30만 − 15만(50%)", dict(care="통원", hospital="의원", non_covered=300000), 150000),
    ("비중증 비급여 입원 200만 원 → 50%", dict(care="입원", hospital="종합병원", non_covered=2000000), 1000000),
    ("비중증 비급여 입원 의원 800만 원 → 50%=400만이지만 300만 한도", dict(care="입원", hospital="의원", non_covered=8000000), 3000000),
    ("비중증 비급여 입원 종합병원 800만 원 → 한도 없음 400만", dict(care="입원", hospital="종합병원", non_covered=8000000), 4000000),
    ("비중증 MRI 60만 원 → 60만 − 30만(50%)", dict(care="통원", hospital="종합병원", mri=600000), 300000),
    ("중증 비급여 통원 20만 원 → 20만 − 6만(30%)", dict(care="통원", hospital="종합병원", non_covered=200000, severe=True), 140000),
    ("중증 비급여 통원 5만 원 → 5만 − 3만", dict(care="통원", hospital="종합병원", non_covered=50000, severe=True), 20000),
    ("중증 비급여 입원 100만 원 → 70%", dict(care="입원", hospital="종합병원", non_covered=1000000, severe=True), 700000),
    ("중증 도수치료 10만 원 → 10만 − 3만(3만과 30% 중 큰 금액)", dict(care="통원", hospital="의원", manual_therapy=100000, severe=True), 70000),
    ("1인실 5일 140만 원 → 50%=70만이지만 하루 10만×5=50만", dict(care="입원", hospital="종합병원", room_fee=1400000, room_days=5), 500000),
    ("1인실 5일 60만 원 → 50%=30만", dict(care="입원", hospital="종합병원", room_fee=600000, room_days=5), 300000),
    ("통원 급여 2만 + 비중증 비급여 10만 → 1만 + 5만",
     dict(care="통원", hospital="의원", covered_copay=20000, non_covered=100000), 60000),
]


# 방문 횟수(visits): 통원 공제는 방문 1건당 뗀다
VISIT_CASES = [
    # 의원 비급여 통원 15만 원을 3번 나눠 방문(회당 5만) → 회당 max(5만, 2.5만)=5만 공제 → 회당 0원 → 총 0
    ("비급여 통원 15만(3회 방문) → 회당 5만, 공제 5만 → 0원",
     dict(care="통원", hospital="의원", non_covered=150000, visits=3), 0),
    # 같은 15만을 1번 방문 → 15만 − max(5만, 7.5만)=7.5만 → 7.5만
    ("비급여 통원 15만(1회 방문) → 15만 − 7.5만",
     dict(care="통원", hospital="의원", non_covered=150000, visits=1), 75000),
    # 급여 통원 의원 9만을 3번(회당 3만) → 회당 max(1만, 6천)=1만 공제 → 회당 2만 × 3 = 6만
    ("급여 통원 9만(3회 방문) → 회당 3만−1만=2만 × 3",
     dict(care="통원", hospital="의원", covered_copay=90000, visits=3), 60000),
    # 입원은 visits 무시: 급여 입원 100만 × 80% (visits 넣어도 동일)
    ("입원은 visits 무시: 급여 입원 100만 → 80%",
     dict(care="입원", hospital="종합병원", covered_copay=1000000, visits=5), 800000),
]


def main():
    failed = 0
    for desc, kwargs, expected in CASES + VISIT_CASES:
        got = c.calculate(**kwargs)["total"]
        ok = got == expected
        failed += not ok
        print(f"{'OK ' if ok else 'FAIL'} {desc}: {got:,} (기대 {expected:,})")

    # 세대 경고: 최신 외 세대는 참고 상한 경고가 notes에 들어간다
    for gen in ("1세대", "2세대", "3세대"):
        notes = c.calculate(care="통원", hospital="의원", covered_copay=15000, generation=gen)["notes"]
        if any("참고 상한" in n for n in notes):
            print(f"OK  {gen} 경고 표시됨")
        else:
            print(f"FAIL {gen} 경고 없음"); failed += 1
    # 최신 세대(4세대)는 참고 상한 경고가 없다
    notes4 = c.calculate(care="통원", hospital="의원", covered_copay=15000, generation="4세대")["notes"]
    if any("참고 상한" in n for n in notes4):
        print("FAIL 4세대에 불필요한 경고"); failed += 1
    else:
        print("OK  4세대는 참고 상한 경고 없음")
    # 중복가입(비례보상) 안내
    notes_dup = c.calculate(care="통원", hospital="의원", covered_copay=15000, has_other_insurance=True)["notes"]
    if any("비례보상" in n for n in notes_dup):
        print("OK  중복가입 비례보상 안내됨")
    else:
        print("FAIL 중복가입 안내 없음"); failed += 1
    for bad in (dict(care="왕진", hospital="의원"), dict(care="통원", hospital="약국"),
                dict(care="통원", hospital="의원", covered_copay=-1),
                dict(care="입원", hospital="의원", room_fee=100000),
                dict(care="통원", hospital="의원", visits=0),
                dict(care="통원", hospital="의원", covered_copay=10000, visits=-2),
                dict(care="통원", hospital="의원", generation="5세대")):
        try:
            c.calculate(**bad)
            print(f"FAIL 잘못된 입력을 막지 못함: {bad}"); failed += 1
        except ValueError:
            pass
    print(f"\n실패 {failed}개" if failed else f"\n{len(CASES)}/{len(CASES)} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
