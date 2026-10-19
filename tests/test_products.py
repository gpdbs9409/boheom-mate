"""상품 목록 테스트: 상품마다 최신 판 하나, 판 목록, 데이터 파일에 추가.

실행: .venv/bin/python tests/test_products.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import products

items = [
    {"company": "KB손해보험", "category": "실손의료보험", "product": "A", "edition": "26.09", "source": "a1.pdf"},
    {"company": "KB손해보험", "category": "실손의료보험", "product": "A", "edition": "27.01", "source": "a2.pdf"},
    {"company": "삼성화재", "category": "실손의료보험", "product": "B", "edition": "26.10", "source": "b.pdf"},
]

# 검색 결과에는 상품마다 최신 판 하나만
assert [e["edition"] for e in products.search(items=items) if e["product"] == "A"] == ["27.01"]
assert len(products.search(items=items)) == 2
assert [e["product"] for e in products.search(company="삼성화재", items=items)] == ["B"]
# 판 목록은 최신 판부터, 옛 판도 남아 있다
assert [e["edition"] for e in products.editions(items[0], items=items)] == ["27.01", "26.09"]
assert products.base_name(" KB손보 실손의료비보장보험(무배당)(26.09) ") == "KB손보 실손의료비보장보험(무배당)"

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "state" / "products.json"
    # 파일이 없으면 시드를 읽는다
    assert products.load(path) == products.load(products.SEED_FILE)
    # 없는 파일에 add → 시드 + 새 항목
    products.add(dict(items[2]), path=path)
    assert len(products.load(path)) == len(products.load(products.SEED_FILE)) + 1
    # 같은 회사·상품·판을 다시 add → 개수 그대로, source만 바뀜
    products.add({**items[2], "source": "b2.pdf"}, path=path)
    assert [e["source"] for e in products.load(path) if e["product"] == "B"] == ["b2.pdf"]
    # 새 판은 옛 판 옆에 추가된다
    products.add({**items[2], "edition": "27.01", "source": "b3.pdf"}, path=path)
    assert [e["edition"] for e in products.editions(items[2], items=products.load(path))] == ["27.01", "26.10"]

print("OK")
