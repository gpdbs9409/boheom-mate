"""조항 나누기 점검: ingest.split_articles 결과가 이상해 보이는지 자동으로 본다.

주간 약관 확인이 이 결과를 Slack에 "정상" / "확인 필요"로 올린다.
"정상"이어도 운영자가 승인하기 전에는 DB에 넣지 않는다.
"""
import re

import ingest

# 조각 수가 이보다 적으면 잘못 잘렸다고 본다. KB 실손 약관 5개 중 가장 적은 것(간편가입 223조각)의 절반
MIN_ARTICLES = 110

ARTICLE_NO = re.compile(r"^제(\d+)조(?:의(\d+))?$")
MAX_SUSPECTS = 5
# 핵심 구역 번호 판정 기준. KB 기본 약관(운영자가 확인하고 넣은 약관)은 번호 하나가 빠지고(제53조가 제52조에 붙음)
# 본문 인용 8곳이 조항 경계로 잡혀 있다(약 12%). 이 정도는 통과시키고 참고로 알린다
MIN_COVERAGE = 0.9  # 1조부터 마지막 조까지 중 실제로 잡힌 비율
MAX_STRAY_RATIO = 0.2  # 순서에서 벗어난 조각(본문 인용 등)의 비율


def _number(article_no):
    m = ARTICLE_NO.match(article_no)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def _name(n):
    return f"제{n[0]}조" + (f"의{n[1]}" if n[1] else "")


def _suspect(article):
    first = article["text"].split("\n", 1)[0][:40]
    return f'{article["section"]} {article["article_no"]}({article["title"]}): {first}'


def _is_core(section):
    """번호를 점검할 구역. 관련 법규·별표 발췌와 제도성 특약은 원래 번호가 띄엄띄엄이라 뺀다."""
    return section == ingest.MAIN or section.startswith("특별약관")


def _longest_increasing(items):
    """번호가 커지는 가장 긴 줄(진짜 조항 순서). 끼어든 본문 인용은 여기서 빠진다."""
    best = []  # best[i]: i번째까지 고른 줄
    for i, (n, _) in enumerate(items):
        cand = max((b for b in best if items[b[-1]][0] < n), key=len, default=[])
        best.append(cand + [i])
    return set(max(best, key=len, default=[]))


def _numbering(section, articles):
    """한 구역의 (문제 목록, 판정). 문제가 적으면 참고로만 알리고, 많으면 확인 필요로 본다."""
    items = []
    for article in articles:
        n = _number(article["article_no"])
        if n and (not items or items[-1][0] != n):  # 긴 조항을 나눈 조각은 같은 번호가 연달아 온다
            items.append((n, article))
    if not items:
        return [], True
    keep = _longest_increasing(items)
    main = [items[i] for i in sorted(keep)]
    kept_numbers = {n for n, _ in main}

    problems = []
    prev = (0, 0)
    for n, article in main:
        if n[1] == 0 and n[0] != prev[0] + 1 or n[1] and n[0] != prev[0]:
            gap = f"{section}: {_name(prev)} 다음 {_name(n)}" if prev[0] else f"{section}: 제1조 없이 {_name(n)}부터 시작"
            problems.append((gap, article))
        prev = n
    last = (0, 0)
    for i, (n, article) in enumerate(items):
        if i in keep:
            last = n
        elif n in kept_numbers:
            problems.append((f"{section}: {_name(n)} 겹침", article))
        else:
            problems.append((f"{section}: {_name(last)} 다음 {_name(n)} (순서 뒤집힘)", article))

    max_no = max(n[0] for n, _ in main)
    coverage = len({n[0] for n, _ in main}) / max_no
    strays = len(items) - len(main)
    fine = coverage >= MIN_COVERAGE and strays <= MAX_STRAY_RATIO * len(items)
    return problems, fine


def check(articles):
    """반환값: {"ok", "reasons"(확인 필요 이유), "notes"(통과했지만 알릴 것),
    "sections"(구역별 조각 수), "suspects"(문제 근처 조항, 최대 5개)}"""
    if not articles:
        return {"ok": False, "reasons": ["텍스트를 추출하지 못함"], "notes": [], "sections": {}, "suspects": []}

    sections = {}
    for article in articles:
        sections[article["section"]] = sections.get(article["section"], 0) + 1

    reasons, suspects = [], []
    body = [s for s in sections if s != ingest.GUIDE]
    if ingest.MAIN not in sections or len(body) < 2:
        reasons.append("구역을 찾지 못함")
    notes = []
    for section in [s for s in sections if _is_core(s)]:
        problems, fine = _numbering(section, [a for a in articles if a["section"] == section])
        (notes if fine else reasons).extend(p for p, _ in problems)
        if not fine:
            suspects.extend(_suspect(a) for _, a in problems)
    if len(articles) < MIN_ARTICLES:
        reasons.append(f"조항 수가 너무 적음({len(articles)}개)")

    return {"ok": not reasons, "reasons": reasons, "notes": notes, "sections": sections, "suspects": suspects[:MAX_SUSPECTS]}
