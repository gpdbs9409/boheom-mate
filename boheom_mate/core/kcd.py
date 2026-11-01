"""질병코드(KCD) 파싱, 면책 조항의 코드 범위 추출·분류, 범위 포함 판정.

순수 함수만 둔다(Chroma·파일·네트워크 I/O 없음). 코드 범위 값은 소스에 적지 않고
면책 조항(Exclusion_Article) 원문에서 정규식으로 뽑는다(Req 10.3).
도구 없음, Rule_Set 면책 코드 범위용(policy_rules._supplements가 code_ranges를 만들 때 쓴다).
"""

import re

CODE = r"[A-Z]\d{2}(?:\.\d{1,2})?"
TILDES = "~∼～"                         # U+007E, U+223C, U+FF5E
RANGE_RE = re.compile(rf"({CODE})\s*[{TILDES}]\s*({CODE})")
SINGLE_RE = re.compile(rf"(?<![A-Z0-9.])({CODE})(?![\d.]|\s*[{TILDES}])")
# 항목 머리 번호 표지. "회사는 다음의 … 보상하지 않습니다" 같은 문단 머리도 앞 항목을 끝낸다
# (앞 항목의 조건 문구 판정에 다음 문단 머리가 섞이지 않게). 문단 머리 앞 PDF 글머리표는
# 사용자 정의 영역 문자(U+F000 등)로 들어오므로 공백과 같이 건너뛴다.
ITEM_RE = re.compile(r"^[\s\uf000-\uf8ff]*(\d+\.|[①-⑳]|[가-하]\.|\(\d+\)|회사는)", re.MULTILINE)
SENTENCE_END_RE = re.compile(r"다\.(?=\s|$)")
_CODE_TEXT_RE = re.compile(r"^([A-Z])(\d{2})(?:\.?(\d{1,2}))?$")

QUOTE_MAX = 300

# policy_rules._norm과 같은 규칙(공백 제거 + 비슷한 기호 통일). policy_rules가 이 모듈을
# 가져다 쓰므로 순환 import를 피하려고 여기에 같은 표를 둔다.
_SAME_CHARS = str.maketrans({"ㆍ": "·", "･": "·", "∼": "~", "～": "~", "‘": "'", "’": "'", "“": '"', "”": '"'})

# 조건 문구 표지(_norm 기준). 숫자가 아니라 약관 문구라서 상수로 둔다.
# 순서가 우선순위다. "비급여 의료비"가 있는 항목은 "요양급여에 해당하지 않는 비급여 의료비"처럼
# 두 표지를 함께 가질 수 있어서 더 좁은 쪽(비급여 의료비만 면책)을 먼저 본다.
CONDITION_MARKERS = (
    ("excluded", "비급여의료비", "비급여 의료비만 면책"),
    ("excluded", "요양급여에해당하지않는", "요양급여에 해당하지 않는 부분만 면책"),
    ("exception", "요양급여에해당하는의료비는보상", "요양급여 의료비는 보상"),
)
_BUT = "다만"
_PAYS = "보상합니다"


def _norm(text):
    return re.sub(r"\s+", "", (text or "").translate(_SAME_CHARS))


# ---------------------------------------------------------------- 코드 파싱·판정

def parse_code(text):
    """"N39.3" → ("N", 39, "3"), "F32" → ("F", 32, ""). 공백·소문자 정리,
    점 없는 4~5자("N393")는 3자 뒤에 점을 넣은 것으로 본다. 코드 모양이 아니면 None."""
    if isinstance(text, tuple):
        return text
    if not isinstance(text, str):
        return None
    m = _CODE_TEXT_RE.match(re.sub(r"\s+", "", text).upper())
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3) or ""


def format_code(parsed):
    """("N", 39, "3") → "N39.3"."""
    letter, n, dec = parsed
    return f"{letter}{n:02d}" + (f".{dec}" if dec else "")


def _decimal_cmp_ok(code_dec, end_dec, side):
    """끝점 자릿수로 코드 소수 부분을 잘라 비교한다. 코드 쪽 자릿수가 모자라면 불일치."""
    if len(code_dec) < len(end_dec):
        return False
    cut = code_dec[:len(end_dec)]
    return cut >= end_dec if side == "start" else cut <= end_dec


def in_range(code, rng):
    """rng = {"start", "end"}(단일 코드면 start == end).
    1) 알파벳이 같아야 한다. 2) 앞 두 자리 정수 n이 start_n ≤ n ≤ end_n.
    3) 끝점에 소수점 이하가 있으면 그 끝점과 n이 같을 때만 소수점 이하를 끝점의 자릿수로
       잘라 비교한다(코드의 소수 자릿수가 끝점보다 짧으면 그 끝점 쪽은 불일치)."""
    c, s, e = parse_code(code), parse_code(rng.get("start")), parse_code(rng.get("end"))
    if not (c and s and e):
        return False
    letter, n, dec = c
    if not (letter == s[0] == e[0]):
        return False
    if not (s[1] <= n <= e[1]):
        return False
    if s[2] and n == s[1] and not _decimal_cmp_ok(dec, s[2], "start"):
        return False
    if e[2] and n == e[1] and not _decimal_cmp_ok(dec, e[2], "end"):
        return False
    return True


def range_label(rng):
    """{"start": "F04", "end": "F99"} → "F04~F99", 단일 코드면 "N39.3"."""
    return rng["start"] if rng["start"] == rng["end"] else f'{rng["start"]}~{rng["end"]}'


def match(candidates, code_ranges):
    """후보 코드마다 포함된 범위 목록과 요약 kind_summary를 돌려준다.

    candidates: 코드 문자열 또는 {"code", "name"} 목록. 범위에 하나도 들지 않는 후보는 뺀다.
    반환: [{"code", "name", "kind_summary", "matches": [{"code", "range", "kind", "condition",
           "section", "article_no", "title", "quote"}]}]
    kind_summary: "excluded" | "exception" | "excluded_with_exception"(둘 다 걸림)."""
    out = []
    for cand in candidates or []:
        raw = cand.get("code") if isinstance(cand, dict) else cand
        parsed = parse_code(raw)
        if not parsed:
            continue
        code = format_code(parsed)
        hits = []
        for rng in code_ranges or []:
            if in_range(parsed, rng):
                hits.append({
                    "code": code, "range": range_label(rng), "kind": rng.get("kind"),
                    "condition": rng.get("condition"), "section": rng.get("section"),
                    "article_no": rng.get("article_no"), "title": rng.get("title"),
                    "quote": rng.get("quote"),
                })
        if not hits:
            continue
        kinds = {h["kind"] for h in hits}
        if kinds == {"exception"}:
            summary = "exception"
        elif "exception" in kinds:
            summary = "excluded_with_exception"
        else:
            summary = "excluded"
        item = {"code": code, "kind_summary": summary, "matches": hits}
        if isinstance(cand, dict) and "name" in cand:
            item["name"] = cand["name"]
        out.append(item)
    return out


# ---------------------------------------------------------------- 원문 추출·분류

def _item_bounds(text, pos):
    """pos가 속한 항목 구간 (시작, 끝). 항목은 줄 머리 번호 표지 사이 구간이다."""
    starts = [m.start(1) for m in ITEM_RE.finditer(text)]
    begin = max([s for s in starts if s <= pos], default=0)
    end = min([s for s in starts if s > pos], default=len(text))
    return begin, end


def _enclosing_parens(text, start, end):
    """[start, end) 구간을 감싸는 괄호들의 (여는 위치, 닫는 위치) 목록. 안쪽부터."""
    out, depth, opens = [], 0, []
    i = start - 1
    while i >= 0:
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                opens.append(i)
            else:
                depth -= 1
        i -= 1
    j, depth, closes = end, 0, []
    while j < len(text) and len(closes) < len(opens):
        ch = text[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                closes.append(j)
            else:
                depth -= 1
        j += 1
    for k, o in enumerate(opens):
        out.append((o, closes[k] if k < len(closes) else len(text)))
    return out


def classify(chunk_text, start, end):
    """(kind, condition). kind는 "excluded" | "exception".

    start, end는 chunk_text 안에서 코드(또는 범위) 표기의 위치다. 같은 항목 안에서
    (a) 코드 앞에 "다만"이 있고(그 사이에 "보상합니다"로 닫히지 않음) 코드 뒤에 "보상합니다"가 있거나,
    (b) 코드를 감싸는 괄호 중 하나에서 코드 뒤에 "보상합니다"가 있으면(그 사이에 새 "다만"이 없을 때)
    exception, 그 밖은 excluded."""
    text = chunk_text or ""
    ib, ie = _item_bounds(text, start)
    before, after = _norm(text[ib:start]), _norm(text[end:ie])
    kind = "excluded"
    but = before.rfind(_BUT)
    if but >= 0 and _PAYS not in before[but:] and _PAYS in after:
        kind = "exception"
    else:
        item_text = text[ib:ie]
        for o, c in _enclosing_parens(item_text, start - ib, end - ib):
            # 괄호 안에서 코드 뒤에 "보상합니다"가 오되, 그 사이에 새 "다만" 절이 끼면
            # 그 보상은 다른 대상 이야기다("(K00∼K08, 다만 안면부 골절 … 보상합니다)").
            tail = _norm(item_text[end - ib:c])
            pays = tail.find(_PAYS)
            if pays >= 0 and _BUT not in tail[:pays]:
                kind = "exception"
                break
    item = _norm(text[ib:ie])
    condition = next((label for k, marker, label in CONDITION_MARKERS
                      if k == kind and marker in item), None)
    return kind, condition


def _quote_span(text, start, end):
    """코드가 들어 있는 문장의 원문 구간. 항목 안에서 코드 뒤 첫 문장 끝("다.")까지,
    PDF 옆 탭(한 글자짜리 줄) 앞에서 자르고, 최대 QUOTE_MAX자로 코드 주변을 남긴다."""
    ib, ie = _item_bounds(text, start)
    m = SENTENCE_END_RE.search(text, end, ie)
    qe = m.end() if m else ie
    line_end = text.find("\n", end)
    if 0 <= line_end < qe:
        side = re.search(r"\n[ \t]*\S[ \t]*(?=\n)", text[line_end:qe])
        if side:
            qe = line_end + side.start()
    qs = ib
    while qs < start and text[qs].isspace():
        qs += 1
    while qe > end and (text[qe - 1].isspace() or "\uf000" <= text[qe - 1] <= "\uf8ff"):
        qe -= 1
    if qe - qs > QUOTE_MAX:
        room = QUOTE_MAX - (end - start)
        qs = max(qs, start - room // 2)
        qe = min(qe, qs + QUOTE_MAX)
        qs = max(qs, qe - QUOTE_MAX)
    return qs, qe


def extract_ranges(chunk):
    """한 조각에서 범위와 단일 코드를 모두 찾는다. 범위의 두 끝점은 단일 코드로 다시 세지 않는다.

    chunk: {"text", "section", "article_no", "title", ...}(Article_Locator 형식).
    반환: [{"start", "end", "notation", "kind", "condition", "quote", "section", "article_no", "title"}]
    (원문 등장 순서)."""
    text = chunk.get("text") or ""
    found, taken = [], []
    for m in RANGE_RE.finditer(text):
        found.append((m.start(), m.end(), m.group(1), m.group(2)))
        taken.append((m.start(), m.end()))
    for m in SINGLE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        found.append((m.start(), m.end(), m.group(1), m.group(1)))
    found.sort()
    out = []
    for s, e, start_code, end_code in found:
        kind, condition = classify(text, s, e)
        qs, qe = _quote_span(text, s, e)
        out.append({
            "start": start_code, "end": end_code, "notation": text[s:e],
            "kind": kind, "condition": condition, "quote": text[qs:qe],
            "section": chunk.get("section"), "article_no": chunk.get("article_no"),
            "title": chunk.get("title"),
        })
    return out


def verify_range(rng, article_text):
    """범위 하나를 검증한다. 문제가 없으면 None, 있으면 이유 문자열.
    quote가 원문에 있음 + quote에 범위 표기(물결표 정규화 후)가 있음 + 같은 알파벳·시작 ≤ 끝."""
    quote = rng.get("quote") or ""
    if not quote or _norm(quote) not in _norm(article_text):
        return "인용 구절이 약관 원문에 없음"
    if _norm(rng.get("notation") or range_label(rng)) not in _norm(quote):
        return "인용 구절에 코드 범위 표기가 없음"
    s, e = parse_code(rng.get("start")), parse_code(rng.get("end"))
    if not (s and e):
        return "코드 모양이 아님"
    if s[0] != e[0]:
        return "범위 양 끝의 알파벳이 다름"
    if s[1] > e[1] or (s[1] == e[1] and s[2] and e[2] and s[2].ljust(2, "0") > e[2].ljust(2, "0")):
        return "범위 시작이 끝보다 큼"
    return None


def collect(chunks):
    """여러 조각에서 범위를 뽑아 검증하고 중복을 없앤다. (code_ranges, failed).
    failed 키는 "code_range:<표기>@<구역>". 반환 범위에서 notation은 뺀다."""
    ranges, failed, seen = [], {}, set()
    for chunk in chunks or []:
        for rng in extract_ranges(chunk):
            reason = verify_range(rng, chunk.get("text") or "")
            if reason:
                failed[f'code_range:{_norm(rng["notation"])}@{rng.get("section")}'] = reason
                continue
            key = (rng["start"], rng["end"], rng["kind"], rng["condition"],
                   rng.get("section"), rng.get("article_no"))
            if key in seen:
                continue
            seen.add(key)
            ranges.append({k: v for k, v in rng.items() if k != "notation"})
    return ranges, failed
