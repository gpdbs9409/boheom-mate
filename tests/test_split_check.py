"""조항 나누기 점검 테스트.

실행: .venv/bin/python tests/test_split_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ingest
import split_check
from split_check import check


def a(section, no):
    return {"section": section, "article_no": no, "title": "t", "text": f"{no} 본문"}


MAIN = "보통약관"
SP = "특별약관1(상급병실료차액)"
ORIGINAL_MIN = split_check.MIN_ARTICLES
split_check.MIN_ARTICLES = 3  # 작은 예제로 시험하려고 낮춘다

base = [a(MAIN, f"제{i}조") for i in range(1, 6)] + [a(SP, f"제{i}조") for i in range(1, 4)]
r = check(base)
assert r["ok"] and r["reasons"] == [] and r["notes"] == [] and r["sections"] == {MAIN: 5, SP: 3}, r

# 관련 법규·별표·제도성 특약은 원래 번호가 띄엄띄엄이라 번호 점검을 하지 않는다
law = [a("관련 법규: 민법", "제27조"), a("관련 법규: 민법", "제755조"), a("관련 법규: 민법", "제651조")]
assert check(base + law)["ok"]

# 핵심 구역에 본문 인용이 조금 끼거나 번호 하나가 빠진 정도는 통과, 참고로 알린다
long_main = [a(MAIN, f"제{i}조") for i in range(1, 21) if i != 15]
long_main.insert(10, a(MAIN, "제3조"))  # 본문 인용 "제3조(...)에서 정한"이 줄 맨 앞에 온 경우
r = check(long_main + base[5:])
assert r["ok"], r
assert "보통약관: 제14조 다음 제16조" in r["notes"] and "보통약관: 제3조 겹침" in r["notes"], r["notes"]

# 안내자료는 구역 수와 번호 점검에서 빠진다
guide = [{"section": ingest.GUIDE, "article_no": "머리말", "title": "", "text": "표지"}]
assert check(guide + base)["ok"]

assert "텍스트를 추출하지 못함" in check([])["reasons"]
assert "구역을 찾지 못함" in check([a(MAIN, f"제{i}조") for i in range(1, 9)])["reasons"]
assert "구역을 찾지 못함" in check([a(SP, f"제{i}조") for i in range(1, 4)] + [a("별표: x", "별표")])["reasons"]

gap = [a(MAIN, "제1조"), a(MAIN, "제2조"), a(MAIN, "제4조")] + base[5:]
r = check(gap)
assert not r["ok"] and "보통약관: 제2조 다음 제4조" in r["reasons"], r
assert r["suspects"] and len(r["suspects"]) <= 5

dup = [a(MAIN, "제1조"), a(MAIN, "제2조"), a(MAIN, "제3조"), a(MAIN, "제2조")] + base[5:]
assert any("제2조 겹침" in x for x in check(dup)["reasons"]), check(dup)

rev = [a(MAIN, "제1조"), a(MAIN, "제3조"), a(MAIN, "제2조")] + base[5:]
assert any("순서 뒤집힘" in x for x in check(rev)["reasons"]), check(rev)

# 긴 조항을 나눈 조각(같은 번호 연달아)과 "제N조의M"은 정상
cont = [a(MAIN, "제1조"), a(MAIN, "제1조"), a(MAIN, "제2조"), a(MAIN, "제2조의2"), a(MAIN, "제3조")] + base[5:]
assert check(cont)["ok"], check(cont)

split_check.MIN_ARTICLES = 100
assert any("조항 수가 너무 적음" in x for x in check(base)["reasons"])

# 실제 KB 기본 약관(있을 때만)은 통과해야 한다
split_check.MIN_ARTICLES = ORIGINAL_MIN
pdf = ingest.DATA_DIR / "KB_실손의료비보장보험_26.09_약관.pdf"
if pdf.exists():
    real = check(ingest.split_articles(ingest.extract_text(pdf))[0])
    assert real["ok"], real["reasons"]
    assert "보통약관: 제52조 다음 제54조" in real["notes"], real["notes"]  # 제53조가 제52조에 붙어 있다

print("OK")
