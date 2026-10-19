"""배포판에서는 "약관 넣기" 탭이 보이지 않아야 한다. SHOW_INGEST_TAB=1일 때만 보인다.

실행: .venv/bin/python tests/test_app_tabs.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from streamlit.testing.v1 import AppTest


def tab_labels(flag):
    os.environ["SHOW_INGEST_TAB"] = flag
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    return [t.label for t in at.tabs]


assert tab_labels("0") == ["상담하기"], tab_labels("0")
assert tab_labels("1") == ["상담하기", "약관 넣기"]
print("OK")

# 상품 목록에는 있지만 DB에 약관이 없는 상품을 고르면 "준비 중" 안내(오류가 아니라)
import products

saved = products.PRODUCTS
products.PRODUCTS = [{"company": "KB손해보험", "category": "실손의료보험", "product": "없는 약관", "edition": "26.09", "source": "none.pdf"}]
try:
    os.environ["SHOW_INGEST_TAB"] = "0"
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("준비 중" in i.value for i in at.info), [i.value for i in at.info]
finally:
    products.PRODUCTS = saved
print("OK")
