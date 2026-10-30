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
# 강조할 숫자: 80%, 1만원, 180일, 3영업일, 50회, 99세 등
AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?\s?(?:%|천만원|백만원|만원|원|영업일|일|회|년|세|개월))")
# 보상 여부·금액을 가르는 표현. 숫자와 함께 붉은색으로 표시해 사용자가 판단 근거를 바로 보게 한다
KEY_PHRASE = re.compile(
    r"(보상하지 않습니다|보상하지 아니합니다|보상합니다|지급하지 않습니다|지급합니다|제외합니다|"
    r"통원항목별 공제금액|공제금액|보장한도|한도로 합니다|중 큰 금액|뺀 금액|에 해당하는 금액)"
)
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


def to_markdown(text):
    """원문을 읽기 좋은 마크다운으로 바꾼다. 숫자(금액·비율·기간)와 판정 표현은 붉은 굵은 글씨로 표시한다."""
    blocks = []
    for p in to_paragraphs(text):
        if "label" in p:
            blocks.append(f"**[{p['label']}]**")
            continue
        body = MD_SPECIAL.sub(r"\\\1", p["text"])
        # "2. 법령..."이 마크다운 번호 목록으로 바뀌어 번호가 달라지지 않게 막는다.
        body = re.sub(r"^(\d{1,2})\.", r"\1\\.", body)
        # 모든 숫자를 칠하면 근거 원문이 온통 빨개져 핵심이 안 보인다.
        # 판정에 직접 중요한 표현(보상/면책/공제 등)만 강조한다.
        body = KEY_PHRASE.sub(r":red[**\1**]", body)
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
        groups.setdefault(key, []).append(article["text"])
    return [
        {"section": section, "article_no": no, "title": title, "texts": texts}
        for (section, no, title), texts in groups.items()
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
