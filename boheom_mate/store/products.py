"""보험 상품 목록: 보험사 → 보험 종류 → 상품 이름 → 판으로 고르게 한다.

사용자가 자기 약관을 찾아 올리지 않아도 되도록, 약관은 운영자가 미리 넣어 둔다.
목록은 코드가 아니라 데이터 파일이다. 서버에서는 주간 약관 확인이 승인된 약관을
state/products.json에 추가하고, 파일이 없으면 저장소의 products_seed.json을 읽는다.
실손은 가입한 시점의 약관이 적용되므로 새 판이 나와도 옛 판을 지우지 않는다.
"""
import json
import os
import re
from pathlib import Path

# boheom_mate/store/products.py → 프로젝트 루트는 두 단계 위(store → boheom_mate → 루트)
BASE_DIR = Path(__file__).resolve().parents[2]
SEED_FILE = BASE_DIR / "products_seed.json"
DATA_FILE = BASE_DIR / "state" / "products.json"

COMPANIES = [
    "KB손해보험", "삼성화재", "현대해상", "DB손해보험", "메리츠화재",
    "한화손해보험", "롯데손해보험", "흥국화재", "NH농협손해보험",
]
CATEGORIES = ["실손의료보험", "암보험", "운전자보험", "어린이보험", "치아보험"]

ALL = "전체"

EDITION_SUFFIX = re.compile(r"\s*\(\d{2}\.\d{2}\)\s*$")


def base_name(name):
    """상품 이름 끝의 "(26.09)" 같은 판 표시를 뗀다."""
    return EDITION_SUFFIX.sub("", name.strip()).strip()


def load(path=DATA_FILE):
    path = Path(path)
    if not path.exists():
        path = SEED_FILE
    return json.loads(path.read_text(encoding="utf-8"))


PRODUCTS = load()


def _same_product(a, b):
    return a["company"] == b["company"] and a["product"] == b["product"]


def editions(entry, items=None):
    """같은 상품의 판들. 최신 판이 먼저 온다("YY.MM"이라 문자열 순서가 곧 날짜 순서다)."""
    items = PRODUCTS if items is None else items
    return sorted((e for e in items if _same_product(e, entry)), key=lambda e: e["edition"], reverse=True)


def search(company=ALL, category=ALL, keyword="", items=None):
    """조건에 맞는 상품. 상품마다 최신 판 하나만 돌려준다. keyword는 띄어쓰기를 무시하고 이름에서 찾는다."""
    items = PRODUCTS if items is None else items
    key = "".join(keyword.split()).lower()
    latest = {}
    for e in items:
        if (
            company in (ALL, e["company"])
            and category in (ALL, e["category"])
            and key in "".join(e["product"].split()).lower()
        ):
            k = (e["company"], e["product"])
            if k not in latest or e["edition"] > latest[k]["edition"]:
                latest[k] = e
    return list(latest.values())


def add(entry, path=DATA_FILE):
    """상품(판)을 추가한다. 같은 회사·상품·판이 있으면 source만 바꾼다."""
    path = Path(path)
    items = load(path)
    for e in items:
        if _same_product(e, entry) and e["edition"] == entry["edition"]:
            e["source"] = entry["source"]
            break
    else:
        items.append(dict(entry))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def label(entry):
    return entry["product"]
