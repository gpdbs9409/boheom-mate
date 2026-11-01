"""건강보험심사평가원 병원정보서비스(공공데이터포털 15001698) getHospBasisList 클라이언트.
병원 이름만 보내 기관명·종별·주소를 받는다. G1(파라미터·응답 필드)·G2(종별 코드표) 가정값은 이 파일 한 곳에 모은다.
서비스키는 환경변수 HIRA_SERVICE_KEY에서만 읽고, 예외 문구·결과·로그에 키나 요청 URL을 넣지 않는다.
"""
import json
import os
import xml.etree.ElementTree as ET

import requests

ENDPOINT = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
TIMEOUT_SECONDS = 5
ENV_KEY = "HIRA_SERVICE_KEY"
PARAMS = {"key": "ServiceKey", "name": "yadmNm", "rows": "numOfRows", "page": "pageNo", "type": "_type"}  # G1 가정
RESPONSE = {"name": "yadmNm", "class_code": "clCd", "class_name": "clCdNm", "addr": "addr"}            # G1 가정
CLASS_CODES = {"01": "상급종합병원", "11": "종합병원", "21": "병원", "28": "요양병원", "31": "의원",
               "41": "치과병원", "51": "치과의원", "92": "한방병원", "93": "한의원"}                  # G2 가정, clCdNm이 없을 때만 씀
ROWS = 10  # 후보는 최대 5개만 돌려주되, 총 건수(totalCount)로 "정확히 1건"을 판단한다
SOURCE_NOTE = "건강보험심사평가원 병원정보서비스(공공누리 제1유형)"


class HiraError(Exception):
    """키 없음·시간 초과·오류 응답·형식 오류. 메시지에 키나 URL을 넣지 않는다."""


def _key():
    return os.environ.get(ENV_KEY, "").strip()


def has_key() -> bool:
    return bool(_key())


def _text(value):
    return "" if value is None else str(value).strip()


def _item(raw):
    """응답 한 건 → {"name", "class_code", "class_name", "addr"}. 종별 이름이 없으면 코드표(G2)로 채운다."""
    code = _text(raw.get(RESPONSE["class_code"]))
    class_name = _text(raw.get(RESPONSE["class_name"])) or CLASS_CODES.get(code, "")
    return {"name": _text(raw.get(RESPONSE["name"])), "class_code": code,
            "class_name": class_name, "addr": _text(raw.get(RESPONSE["addr"]))}


def _total(value, items):
    try:
        return int(_text(value))
    except ValueError:
        return len(items)


def _parse_json(data):
    if not isinstance(data, dict):
        raise HiraError("형식 오류")
    error = data.get("OpenAPI_ServiceResponse")
    if isinstance(error, dict):
        code = _text((error.get("cmmMsgHeader") or {}).get("returnReasonCode"))
        raise HiraError(f"오류 응답 코드 {code}" if code.isdigit() else "오류 응답")
    response = data.get("response")
    if not isinstance(response, dict):
        raise HiraError("형식 오류")
    result = _text((response.get("header") or {}).get("resultCode"))
    if result != "00":
        raise HiraError(f"오류 응답 코드 {result}" if result.isdigit() else "오류 응답")
    body = response.get("body") or {}
    if not isinstance(body, dict):
        raise HiraError("형식 오류")
    items = body.get("items")
    raw = items.get("item") if isinstance(items, dict) else None  # 0건이면 items가 빈 문자열로 오기도 한다
    if raw is None:
        raw = []
    elif isinstance(raw, dict):  # 한 건이면 dict, 여러 건이면 list
        raw = [raw]
    elif not isinstance(raw, list):
        raise HiraError("형식 오류")
    parsed = [_item(r) for r in raw if isinstance(r, dict)]
    return {"total": _total(body.get("totalCount"), parsed), "items": parsed}


def _parse_xml(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise HiraError("형식 오류") from None
    if root.tag == "OpenAPI_ServiceResponse" or root.find(".//returnReasonCode") is not None:
        code = _text(root.findtext(".//returnReasonCode"))
        raise HiraError(f"오류 응답 코드 {code}" if code.isdigit() else "오류 응답")
    result = root.findtext("./header/resultCode")
    if result is None:
        raise HiraError("형식 오류")
    result = _text(result)
    if result != "00":
        raise HiraError(f"오류 응답 코드 {result}" if result.isdigit() else "오류 응답")
    parsed = [_item({child.tag: child.text for child in node}) for node in root.findall("./body/items/item")]
    return {"total": _total(root.findtext("./body/totalCount"), parsed), "items": parsed}


def search(name: str) -> dict:
    """병원 이름으로 조회 → {"total": int, "items": [{"name", "class_code", "class_name", "addr"}]}.
    요청 파라미터는 PARAMS의 다섯 개(이름·키·행 수·페이지·_type)뿐이다. 실패는 모두 고정 문구의 HiraError."""
    key = _key()
    if not key:
        raise HiraError("키 없음")
    name = _text(name)
    if not name:
        raise HiraError("병원 이름 없음")
    params = {PARAMS["key"]: key, PARAMS["name"]: name, PARAMS["rows"]: ROWS,
              PARAMS["page"]: 1, PARAMS["type"]: "json"}
    # requests 예외 문자열에는 키가 든 URL이 들어가므로 그대로 쓰지 않고 고정 문구로 바꾼다
    try:
        resp = requests.get(ENDPOINT, params=params, timeout=TIMEOUT_SECONDS)
    except requests.Timeout:
        raise HiraError("시간 초과") from None
    except requests.RequestException:
        raise HiraError("연결 실패") from None
    if resp.status_code != 200:
        raise HiraError(f"HTTP 상태 {resp.status_code}")
    text = (resp.text or "").strip()
    if not text:
        raise HiraError("형식 오류")
    if text.startswith("<"):
        return _parse_xml(text)
    try:
        data = json.loads(text)
    except ValueError:
        raise HiraError("형식 오류") from None
    return _parse_json(data)
