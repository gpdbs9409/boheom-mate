"""KB손해보험 공시실(상품목록(약관)). 서버가 HTML로 목록을 그려 주므로 브라우저 없이 읽는다.

1) 목록(CG802030001.ec)에서 판매 중이고 이름에 "실손의료비"가 들어간 상품을 찾고
2) 상품마다 상세(CG802030002.ec)에서 판매 중인 가장 최근 판의 약관 PDF 주소를 읽는다.
화면 글자는 EUC-KR(cp949)이다.
"""
import re
import time
from pathlib import Path

from boheom_mate.integrations.insurers import TIMEOUT, USER_AGENT, Listing, ListingError
from boheom_mate.store.products import base_name

COMPANY = "KB손해보험"
BASE_URL = "https://www.kbinsure.co.kr"
LIST_URL = BASE_URL + "/CG802030001.ec"
DETAIL_URL = BASE_URL + "/CG802030002.ec"
SEARCH_WORD = "실손의료비"
PAGE_SIZE = 10
MAX_PAGES = 10
REQUEST_GAP = 1.0  # 요청 사이 쉬는 초. 사람이 보는 정도로만 접근한다

LIST_TABLE = re.compile(r'<table class="tb_list".*?</table>', re.S)
ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
DETAIL_LINK = re.compile(r"detail\('([^']*)','([^']*)','([^']*)'\);\">([^<]+)<")
VERSION_TABLE = re.compile(r'<table class="tb_default04".*?</table>', re.S)
DATE_CELL = re.compile(r'<td class="vfont_s02">([^<]*)</td>')
PDF_LINK = re.compile(r'href="(/CG802030003\.ec\?fileNm=[^"]+)"')


def _list_rows(html):
    """목록 표의 모든 줄: [{"status", "code", "gubun", "seq", "name"}]. 표가 없으면 ListingError."""
    table = LIST_TABLE.search(html)
    if not table:
        raise ListingError("KB 공시실 목록 표를 찾지 못함(화면이 바뀌었을 수 있음)")
    rows = []
    for row in ROW.findall(table.group(0)):
        link = DETAIL_LINK.search(row)
        cells = CELL.findall(row)
        if link and cells:
            code, gubun, seq, name = (x.strip() for x in link.groups())
            rows.append({"status": cells[0].strip(), "code": code, "gubun": gubun, "seq": seq, "name": name})
    return rows


def _is_target(row):
    """판매 중인 실손의료보험 상품. 실손 특약(gubun j), 여행·체류 실손, 단체·연계용은 뺀다(설계 문서의 실손 범위)."""
    name = row["name"]
    return (
        row["status"].startswith("판매중")
        and row["gubun"] != "j"
        and "실손의료비보장보험" in name
        and "단체" not in name
        and "연계용" not in name
    )


def unique_by_name(rows):
    """같은 상품이 판매 경로(보험종류)만 달리해 두 번 올라오면 처음 것 하나만 남긴다."""
    seen = set()
    return [r for r in rows if not (r["name"] in seen or seen.add(r["name"]))]


def parse_list(html):
    return [{k: r[k] for k in ("code", "gubun", "seq", "name")} for r in _list_rows(html) if _is_target(r)]


def parse_detail(html, name):
    """상세 화면에서 판매 중인 가장 최근 판. 판은 판매시작일(YYYYMMDD)의 YY.MM."""
    table = VERSION_TABLE.search(html)
    versions = []
    for row in ROW.findall(table.group(0) if table else ""):
        dates = DATE_CELL.findall(row)
        pdf = PDF_LINK.search(row)  # 첫 PDF가 보험약관(그다음은 사업방법서, 상품요약서)
        if len(dates) >= 2 and pdf and re.fullmatch(r"\d{8}", dates[0].strip()):
            versions.append((dates[0].strip(), dates[1].strip(), pdf.group(1)))
    if not versions:
        raise ListingError(f"KB 상세 화면에서 약관 PDF를 찾지 못함: {name}")
    on_sale = [v for v in versions if not v[1]] or versions
    start, _, href = max(on_sale)
    return Listing(company=COMPANY, product=base_name(name), edition=f"{start[2:4]}.{start[4:6]}", pdf_url=BASE_URL + href)


def _post(session, url, data):
    import requests

    time.sleep(REQUEST_GAP)
    try:
        resp = session.post(url, data=data, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ListingError(f"KB 공시실 접속 실패: {e}") from e
    return resp.content.decode("cp949", errors="replace")


def fetch():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    word = SEARCH_WORD.encode("cp949")
    found = []
    for page in range(MAX_PAGES):
        html = _post(session, LIST_URL, {
            "devonTargetRow": page * PAGE_SIZE + 1, "onsaleYn": "Y", "search_onsale_yn": "Y",
            "gubun": " ", "search_gubun": " ", "goodsNm": word, "search_goods_nm": word,
        })
        rows = _list_rows(html)
        found += [{k: r[k] for k in ("code", "gubun", "seq", "name")} for r in rows if _is_target(r)]
        if len(rows) < PAGE_SIZE:
            break
    if not found:
        raise ListingError("KB 공시실에서 판매 중인 실손 상품을 하나도 찾지 못함(화면이 바뀌었을 수 있음)")
    return [
        parse_detail(_post(session, DETAIL_URL, {"bojongNo": r["code"], "gubun": r["gubun"], "bojongSeq": r["seq"], "devonTargetRow": 1}), r["name"])
        for r in unique_by_name(found)
    ]


def download(listing, dest):
    import requests

    dest = Path(dest)
    try:
        resp = requests.get(listing.pdf_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ListingError(f"PDF 내려받기 실패: {e}") from e
    if not resp.content.startswith(b"%PDF"):
        raise ListingError(f"PDF가 아닌 응답: {listing.pdf_url}")
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)
