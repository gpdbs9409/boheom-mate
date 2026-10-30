"""근거 조항 원문을 화면에서 읽기 좋게 다듬는다.

PDF에서 뽑은 원문에는 다음과 같은 문제가 있다.
- 문장 중간에서 줄이 끊긴다 ("통원하\\n여")
- 페이지 머리말·꼬리말이 끼어 있다 ("KB손보 실손의료비보장보험(무배당)(26.09) 53")
- 옆 띠지 글자가 한 줄에 한 글자씩 들어 있다 ("공\\n통\\n사\\n항")
- "용 어 풀 이"처럼 글자 사이가 벌어진 소제목이 있다

저장된 원문은 그대로 두고, 화면에 보여줄 때만 고친다.
"""
import re

# 페이지 꼬리말: "KB손보 ...(26.09) 53" 또는 "54 KB손보 ...(26.09)"
FOOTER = re.compile(r"^\s*(\d+\s+)?KB손보.*\(\d{2}\.\d{2}\)\s*\d*\s*$")
# 조각 맨 앞의 조항 제목 줄. 제목은 펼치기 버튼에 따로 보여준다.
HEADER = re.compile(r"^\s*제\s*\d+\s*조(\s*의\s*\d+)?\s*[(（].*[)）](\s*\(이어서\))?\s*$")
# 새 문단으로 시작해야 하는 줄: ① (1) 1. 가. 주) ※ - ∙ ▶ ■ ☞ <표 【 등
NEW_ITEM = re.compile(
    r"^(\(\d{1,2}\)|\d{1,2}\.\s|[①-⑳]|[가-하]\.\s|주\)|※|[-•∙·▶▸■☞○◦]|[<＜]표|【|\[)"
)
# "용 어 풀 이" 같은 벌어진 소제목. 줄 중간에 붙어 나오기도 한다.
SPACED_LABEL = re.compile(r"^([가-힣]\s){2,}[가-힣]$")
INLINE_LABEL = re.compile(r"\s*((?:[가-힣] ){3,}[가-힣])\s+")
# PDF 전용 글꼴의 항 번호(①② 등) 기호가 깨져서 들어온 글자. 항목 시작 표시로 바꾼다.
PRIVATE_GLYPH = re.compile(r"[-]")
# 마크다운 기호로 잘못 해석될 수 있는 글자
MD_SPECIAL = re.compile(r"([\\`*_#|~<>\[\]])")


def _is_sidebar(line):
    """옆 띠지 글자(한 줄에 한 글자)인지."""
    s = line.strip()
    return len(s) == 1 and (re.match(r"[가-힣ㆍ]", s) is not None)


def to_paragraphs(text):
    """원문을 문단 목록으로 바꾼다."""
    text = PRIVATE_GLYPH.sub("\n▸ ", text)
    text = INLINE_LABEL.sub(lambda m: "\n" + m.group(1) + "\n", text)
    lines = [l for l in text.split("\n") if l.strip() and not FOOTER.match(l)]
    lines = [l for l in lines if not _is_sidebar(l)]
    if lines and HEADER.match(lines[0]):
        lines = lines[1:]

    paragraphs = []
    prev_raw = ""
    for raw in lines:
        line = raw.strip()
        if SPACED_LABEL.match(line):
            paragraphs.append({"label": line.replace(" ", "")})
            prev_raw = ""
            continue

        last = paragraphs[-1] if paragraphs else None
        starts_new = (
            last is None
            or "label" in last
            or NEW_ITEM.match(line)
            or last["text"].endswith(("다.", ".", ":", "다)"))
        )
        if starts_new:
            paragraphs.append({"text": line})
        else:
            # PDF는 단어 사이에서 끊긴 줄 끝에 공백을 남긴다. 공백이 없으면 단어 중간에서 끊긴 것이다.
            joiner = " " if prev_raw.endswith(" ") else ""
            last["text"] += joiner + line
        prev_raw = raw
    return paragraphs


def _norm_map(text):
    """공백을 지운 글과, 지운 글의 각 글자가 원래 글의 몇 번째 글자인지."""
    chars, index = [], []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            index.append(i)
    return "".join(chars), index


def _highlight_spans(text, highlights):
    """text 안에서 highlights 문장이 차지하는 (시작, 끝) 구간. 공백 차이는 무시하고 찾는다.
    문단이 핵심 문장의 일부이면(핵심 문장이 여러 문단에 걸친 경우) 문단 전체를 칠한다."""
    norm, index = _norm_map(text)
    if not norm:
        return []
    # 화면용 문단은 PDF 기호를 "▸"로 바꾸고 그 자리에서 문단을 나눈다. 핵심 문장도 같은 자리에서 나눠 찾는다
    pieces = []
    for h in highlights or []:
        pieces += [part.replace("▸", "") for part in PRIVATE_GLYPH.split(h)]
    spans = []
    for h in pieces:
        h_norm = "".join(h.split())
        if not h_norm:
            continue
        pos = norm.find(h_norm)
        if pos >= 0:
            spans.append((index[pos], index[pos + len(h_norm) - 1] + 1))
        elif len(norm) >= 10 and norm in h_norm:
            spans.append((0, len(text)))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _escape(text):
    return MD_SPECIAL.sub(r"\\\1", text)


def to_markdown(text, highlights=()):
    """원문을 읽기 좋은 마크다운으로 바꾼다.

    highlights: 답변의 결론을 직접 뒷받침한 원문 문장들. 이 문장만 통째로 붉은 굵은 글씨로 표시한다.
    단어 하나("보상합니다" 등)에 걸린다고 칠하지 않는다. 질문과 무관한 문장까지 붉어지기 때문이다.
    """
    blocks = []
    for p in to_paragraphs(text):
        if "label" in p:
            blocks.append(f"**[{p['label']}]**")
            continue
        raw = p["text"]
        parts, last = [], 0
        for start, end in _highlight_spans(raw, highlights):
            parts.append(_escape(raw[last:start]))
            inner = _escape(raw[start:end]).strip()
            if inner:
                parts.append(f":red[**{inner}**]")
            last = end
        parts.append(_escape(raw[last:]))
        body = "".join(parts)
        # "2. 법령..."이 마크다운 번호 목록으로 바뀌어 번호가 달라지지 않게 막는다.
        body = re.sub(r"^(\d{1,2})\.", r"\1\\.", body)
        blocks.append(body)
    return "\n\n".join(blocks)


def highlight_answer(text):
    """답변에서 굵게 쓴 부분(결론·금액)을 붉은색으로도 표시한다."""
    return re.sub(r"\*\*(.+?)\*\*", r":red[**\1**]", text)


def group_evidence(evidence):
    """같은 조항에서 나온 여러 조각을 하나로 묶는다. 순서는 처음 나온 순서를 따른다."""
    groups = {}
    for article in evidence:
        # 보통약관 제3조와 특약 제3조는 다른 조항이라 구역까지 같아야 같은 조항이다
        key = (article.get("section", ""), article["article_no"], article["title"])
        groups.setdefault(key, []).append((article["text"], article.get("highlights") or []))
    return [
        {"section": section, "article_no": no, "title": title,
         "texts": [t for t, _ in items], "highlights": [h for _, h in items]}
        for (section, no, title), items in groups.items()
    ]


def evidence_label(group):
    """펼치기 버튼에 쓸 이름. 예: 특별약관2(비중증 비급여 실손의료비) · 제3조 (보장종목별 보상내용)

    안내자료·별표·법규처럼 구역 이름이 곧 제목인 경우에는 구역 이름을 한 번만 쓴다.
    """
    section, no, title = group["section"], group["article_no"], group["title"]
    if section.startswith(("별표", "관련 법규")) and no in ("별표", "관련 법규"):
        return section
    if no == section == "안내자료":
        return "안내자료 (약관 이용 가이드)"
    name = f"{no} ({title})" if title else no
    return f"{section} · {name}" if section else name


def evidence_label_md(group):
    """펼치기 버튼용 마크다운. 어느 약관(구역)의 몇 조인지 붉은색으로 보여 준다."""
    section, no = group["section"], group["article_no"]
    plain = evidence_label(group)
    if section and plain.startswith(section + " · "):
        rest = plain[len(section) + 3:]
        return f":red[**{section}**] · {rest}"
    return f":red[**{plain}**]"
