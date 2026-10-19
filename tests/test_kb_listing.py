"""KB손해보험 공시실 목록 읽기 테스트. 저장해 둔 공시실 화면(tests/fixtures)으로 네트워크 없이 시험한다.

실행: .venv/bin/python tests/test_kb_listing.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import products
from insurers import Listing, ListingError
from insurers import kb

FIX = ROOT / "tests" / "fixtures"

# 목록: 판매 중 "실손의료비보장보험" 상품만. 여행·해외체류 실손, 개인실손, 실손 특약(2페이지)은 뺀다
rows = kb.parse_list((FIX / "kb_list.html").read_text())
names = [r["name"] for r in rows]
assert "KB손보 실손의료비보장보험(무배당)(26.09)" in names
assert "KB손보 다이렉트실손의료비보장보험(계약전환용)(갱신형)(무배당)" in names
assert len(rows) == 6, names
assert not any("여행" in n or "체류" in n or n == "KB개인실손의료비보험" for n in names)
assert {"code": "25483", "gubun": "g", "seq": "1"}.items() <= next(r for r in rows if "25483" == r["code"]).items()
assert kb.parse_list((FIX / "kb_list_page2.html").read_text()) == []  # 특별약관(j)만 있는 페이지

# 연계용(단체실손 → 개인실손)은 설계 문서의 실손 범위에 없어 뺀다
row = lambda code, name: f"<tr><td>판매중</td><td>x</td><td>{code}</td><td><a href=\"javascript:detail('{code}','g','1');\">{name}</a></td></tr>"
html = '<table class="tb_list"><tbody>' + row("25484", "KB손보 실손의료비보장보험(연계용)(무배당)") + row("25479", "KB손보 노후실손의료비보장보험(무배당)") + "</tbody></table>"
assert [r["code"] for r in kb.parse_list(html)] == ["25479"]

# 같은 상품이 판매 경로만 달리해 두 번 올라오면(25477, 25478) 처음 것 하나만
dup = [{"code": "25478", "name": "A(26.09)"}, {"code": "25477", "name": "A(26.09)"}, {"code": "25479", "name": "B"}]
assert [r["code"] for r in kb.unique_by_name(dup)] == ["25478", "25479"]

# 공시실 화면이 바뀌어 목록 표를 못 찾으면 "없음"이 아니라 실패
for broken in ["<html><body>점검 중입니다</body></html>", ""]:
    try:
        kb.parse_list(broken)
        assert False, "ListingError가 나야 한다"
    except ListingError:
        pass

# 상세: 판매 중인(판매종료일이 빈) 가장 최근 판. 판은 판매시작일 20260901 → "26.09"
name = "KB손보 실손의료비보장보험(계약전환용)(갱신형)(무배당)"
item = kb.parse_detail((FIX / "kb_detail_25483.html").read_text(), name)
assert item == Listing(company="KB손해보험", product=name, edition="26.09",
                       pdf_url="https://www.kbinsure.co.kr/CG802030003.ec?fileNm=25483_1_1.pdf"), item

two_rows = """<table class="tb_default04"><tbody>
<tr><td class="vfont_s02">20250401</td><td class="vfont_s02">20260831</td><td><a href="/CG802030003.ec?fileNm=1_1_1.pdf"></a></td></tr>
<tr><td class="vfont_s02">20260901</td><td class="vfont_s02">        </td><td><a href="/CG802030003.ec?fileNm=1_2_1.pdf"></a></td></tr>
</tbody></table>"""
item = kb.parse_detail(two_rows, "X(26.09)")
assert item.edition == "26.09" and item.pdf_url.endswith("1_2_1.pdf") and item.product == "X"
try:
    kb.parse_detail("<html></html>", "X")
    assert False
except ListingError:
    pass

# 시드 상품 이름이 공시실 표기(판 뗀 이름)와 같아야 이미 있는 약관을 다시 알리지 않는다
listed = {products.base_name(n) for n in names}
for entry in products.load(products.SEED_FILE):
    assert entry["product"] in listed, entry["product"]

print("OK")
