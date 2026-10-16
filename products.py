"""보험 상품 목록: 보험사 → 보험 종류 → 상품 이름으로 고르게 한다.

사용자가 자기 약관을 찾아 올리지 않아도 되도록, 약관은 운영자가 미리 넣어 둔다.
아직은 껍데기다. source가 있는 상품만 약관이 DB에 들어 있어 상담할 수 있고,
나머지는 "준비 중"으로 보여 준다. 약관을 넣으면 해당 상품의 source에 파일 이름을 적는다.
"""

COMPANIES = [
    "KB손해보험", "삼성화재", "현대해상", "DB손해보험", "메리츠화재",
    "한화손해보험", "롯데손해보험", "흥국화재", "NH농협손해보험",
]
CATEGORIES = ["실손의료보험", "암보험", "운전자보험", "어린이보험", "치아보험"]

# 실제로 상담할 수 있는 상품(약관이 DB에 있음)과, PDF는 받아 두었지만 아직 넣지 않은 상품
PRODUCTS = [
    {"company": "KB손해보험", "category": "실손의료보험", "name": "KB손보 실손의료비보장보험(무배당)(26.09)",
     "source": "KB_실손의료비보장보험_26.09_약관.pdf"},
    {"company": "KB손해보험", "category": "실손의료보험", "name": "KB손보 간편가입 실손의료비보장보험(26.09)",
     "source": "KB_간편가입실손의료비보장보험_26.09_약관.pdf"},
    {"company": "KB손해보험", "category": "실손의료보험", "name": "KB손보 계약전환용 실손의료비보장보험(26.09)", "source": None},
    {"company": "KB손해보험", "category": "실손의료보험", "name": "KB손보 다이렉트 실손의료비보장보험(26.09)", "source": None},
    {"company": "KB손해보험", "category": "실손의료보험", "name": "KB손보 다이렉트 간편 실손의료비보장보험(26.09)", "source": None},
]

ALL = "전체"


def search(company=ALL, category=ALL, keyword=""):
    """조건에 맞는 상품. 상담 가능한 상품이 먼저 온다. keyword는 띄어쓰기를 무시하고 이름에서 찾는다."""
    key = "".join(keyword.split()).lower()
    found = [
        p for p in PRODUCTS
        if company in (ALL, p["company"])
        and category in (ALL, p["category"])
        and key in "".join(p["name"].split()).lower()
    ]
    return sorted(found, key=lambda p: p["source"] is None)


def label(product):
    return product["name"] + ("" if product["source"] else " · 준비 중")
