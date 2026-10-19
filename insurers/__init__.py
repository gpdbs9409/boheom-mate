"""보험사 공시실에서 판매 중인 실손 약관 목록을 읽는다. 보험사마다 모듈 하나.

각 모듈은 COMPANY, fetch() -> list[Listing], download(listing, dest)를 가진다.
목록을 못 읽으면 반드시 ListingError를 낸다("새 약관 없음"과 구분해야 하기 때문).
"""
from dataclasses import dataclass

USER_AGENT = "boheom-mate-weekly-check (+https://boheommate.duckdns.org)"
TIMEOUT = 30


class ListingError(Exception):
    """공시실 목록이나 PDF를 읽지 못함."""


@dataclass
class Listing:
    company: str
    product: str  # 판을 뗀 상품 이름
    edition: str  # "26.09"
    pdf_url: str
    category: str = "실손의료보험"


from insurers import kb  # noqa: E402  (Listing을 먼저 정의해야 한다)

# 자동으로 확인하는 보험사
ENABLED = {kb.COMPANY: kb}
# 자동 확인 불가(자동 접근 금지, 브라우저 필요 등): 운영자가 PDF를 data/inbox/에 직접 넣는다
MANUAL = []
