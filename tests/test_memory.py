"""상담 메모(core.memory) 테스트. LLM 없이 정리·병합·표시 규칙만 확인한다.

실행: .venv/bin/python tests/test_memory.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from boheom_mate.core import memory as m


def check(desc, ok):
    print(f"{'OK ' if ok else 'FAIL'} {desc}")
    return not ok


def main():
    failed = 0
    dirty = {"generation": "5세대", "care": "왕진", "hospital": "의원", "severe": "yes",
             "has_other_insurance": True, "conditions": ["감기", "감기", "  "], "facts": "문자열", "extra": 1}
    c = m.clean(dirty)
    failed += check("보기 밖 값은 버림(5세대, 왕진)", c["generation"] is None and c["care"] is None)
    failed += check("맞는 값은 남김(의원, 중복가입)", c["hospital"] == "의원" and c["has_other_insurance"] is True)
    failed += check("중증은 True일 때만 남김", c["severe"] is None and m.clean({"severe": False})["severe"] is None and m.clean({"severe": True})["severe"] is True)
    failed += check("목록은 중복·빈칸 제거, 목록이 아니면 빈 목록", c["conditions"] == ["감기"] and c["facts"] == [])
    failed += check("정해진 칸만 남김", set(c) == set(m.EMPTY))
    failed += check("목록은 최대 5개(최근 것)", m.clean({"facts": [str(i) for i in range(9)]})["facts"] == ["4", "5", "6", "7", "8"])

    merged = m.merge_calculation(m.EMPTY, {"generation": "3세대", "hospital": "종합병원", "care": "통원", "covered_copay": 30000})
    failed += check("계산기에 넣은 값은 메모에 반영",
                    merged["generation"] == "3세대" and merged["hospital"] == "종합병원" and merged["care"] == "통원")
    failed += check("빈 메모는 프롬프트에 아무것도 안 붙임", m.prompt_block(None) == "" and m.describe({}) == [])
    block = m.prompt_block(merged)
    failed += check("프롬프트에 기억한 내용과 다시 묻지 말라는 지시", "3세대" in block and "다시 묻지" in block)
    failed += check("화면 표시는 쉬운 말", "병원 종류: 종합병원·대학병원" in m.describe(merged))
    for (year, month), want in [((2018, None), "3세대"), ((2017, 3), "2세대"), ((2017, 4), "3세대"), ((2017, None), None),
                                ((2009, 9), "1세대"), ((2009, 10), "2세대"), ((2005, None), "1세대"), ((2021, 6), "3세대"),
                                ((2021, 7), "4세대"), ((2023, None), "4세대"), ((2012, 13), "2세대")]:
        failed += check(f"가입 {year}년 {month or '?'}월 → {want}", m.generation_of(year, month) == want)
    failed += check("가입 연도가 있으면 LLM이 적은 세대보다 날짜 계산을 따름",
                    m.clean({"joined_year": 2018, "generation": "2세대"})["generation"] == "3세대")
    failed += check("경계 연도에 월이 없으면 세대를 비워 둠",
                    m.clean({"joined_year": 2017, "generation": "2세대"})["generation"] == "2세대"
                    or m.clean({"joined_year": 2017})["generation"] is None)
    print(f"\n실패 {failed}개" if failed else "\nOK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
