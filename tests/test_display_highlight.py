"""근거 원문 강조 테스트: 핵심 문장만 칠하고, '보상합니다' 같은 단어만으로는 칠하지 않는다.

실행: .venv/bin/python tests/test_display_highlight.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from boheom_mate.ui import display

TEXT = ("▸ 회사는 피보험자가 질병으로 인하여 의료기관에 통원하여 치료를 받은 경우에는\n"
        "급여의료비를 다음과 같이 보상합니다.\n"
        "▸ 피보험자가 임신, 출산으로 입원한 경우에는 보상하지 않습니다.")
CORE = "회사는 피보험자가 질병으로 인하여 의료기관에 통원하여 치료를 받은 경우에는 급여의료비를 다음과 같이 보상합니다."


def check(desc, ok):
    print(f"{'OK ' if ok else 'FAIL'} {desc}")
    return not ok


def main():
    failed = 0
    md = display.to_markdown(TEXT, [CORE])
    failed += check("핵심 문장 하나만 강조", md.count(":red[") == 1)
    failed += check("줄바꿈으로 끊긴 핵심 문장도 찾음", "다음과 같이 보상합니다.**]" in md)
    failed += check("무관한 문장(임신)은 칠하지 않음", ":red[**피보험자가 임신" not in md)
    failed += check("핵심 문장이 없으면 아무것도 칠하지 않음", display.to_markdown(TEXT).count(":red[") == 0)
    failed += check("원문에 없는 문장은 칠하지 않음", display.to_markdown(TEXT, ["없는 문장입니다."]).count(":red[") == 0)
    glyph_text = "\uf000 회사는 질병으로 통원하여\n치료를 받은 경우 보상합니다.\n\uf000 임신으로 입원하면 보상하지 않습니다."
    glyph_md = display.to_markdown(glyph_text, ["\uf000 회사는 질병으로 통원하여 치료를 받은 경우 보상합니다."])
    failed += check("PDF 항목 기호가 붙은 핵심 문장도 찾음", glyph_md.count(":red[") == 1 and ":red[**임신" not in glyph_md)
    groups = display.group_evidence([
        {"section": "보통약관", "article_no": "제3조", "title": "보장종목별 보상내용", "text": TEXT, "highlights": [CORE]},
        {"section": "보통약관", "article_no": "제3조", "title": "보장종목별 보상내용", "text": "다른 조각"},
    ])
    failed += check("같은 조항의 조각마다 핵심 문장을 따로 들고 있음",
                    len(groups) == 1 and groups[0]["highlights"] == [[CORE], []])
    print(f"\n실패 {failed}개" if failed else "\nOK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
