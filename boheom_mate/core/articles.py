"""Article_Locator: 조항을 번호가 아니라 구역(section) + 제목(title) 메타데이터로 꺼낸다.
상품마다 조 번호가 다르다(청구: 기본형 제7조, 간편가입 제6조). 제목은 같으므로 제목 완전 일치로 찾는다.
- "지정대리청구인에 의한 보험금의 청구"처럼 제목이 길게 붙은 조항은 완전 일치가 아니라서 빠진다.
- 구역은 "보통약관"·"특별약관"으로 시작하는 것만 쓴다. 용어의 정의만 "별표: 용어의 정의"를 더한다.
  제도성 특약·안내자료·관련 법규는 뺀다.
rank는 이미 고른 조각 안에서만 BM25로 순위를 매긴다. 임베딩을 부르지 않는다.
"""
from functools import lru_cache

from rank_bm25 import BM25Okapi

from boheom_mate.core import glossary
from boheom_mate.core import keyword_search

COVERAGE, EXCLUSION, CLAIM, LIMITATION, DEFINITION = (
    "보장종목별 보상내용", "보상하지 않는 사항", "보험금의 청구", "소멸시효", "용어의 정의")
KINDS = (COVERAGE, EXCLUSION, CLAIM, LIMITATION, DEFINITION)

SECTION_PREFIXES = ("보통약관", "특별약관")
DEFINITION_APPENDIX = "별표: 용어의 정의"


def _allowed(section, title):
    section = section or ""
    if section.startswith(SECTION_PREFIXES):
        return True
    return title == DEFINITION and section == DEFINITION_APPENDIX


def _chunk_no(chunk_id):
    tail = chunk_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


@lru_cache(maxsize=64)
def _locate(source, doc_count, title):
    """doc_count는 캐시 키로만 쓴다. 약관을 다시 넣어 조각 수가 바뀌면 새로 읽는다."""
    from boheom_mate.core import rag
    data = rag._collection().get(
        where={"$and": [{"source": source}, {"title": title}]}, include=["documents", "metadatas"])
    rows = [
        {"id": id_, "section": meta.get("section", ""), "article_no": meta.get("article_no", ""),
         "title": meta.get("title", ""), "text": doc}
        for id_, doc, meta in zip(data["ids"], data["documents"], data["metadatas"])
        if meta.get("title") == title and _allowed(meta.get("section"), title)
    ]
    rows.sort(key=lambda r: _chunk_no(r["id"]))
    return tuple(rows)


def locate(source, title):
    """[{"id", "section", "article_no", "title", "text"}] (조각 id 순). 없으면 []."""
    from boheom_mate.core import rag
    return [dict(r) for r in _locate(source, rag._doc_count(source), title)]


def inventory(source):
    """{제목: 조각 수}. 도구 사용 가능 조건 판정에 쓴다."""
    return {title: len(locate(source, title)) for title in KINDS}


def _query_tokens(question):
    return keyword_search.tokenize(" ".join([question, *glossary.lookup(question)]))


def rank(question, chunks, per_section):
    """질문 + glossary.lookup 용어로 kiwi 토큰을 만들고 chunks 안에서만 BM25Okapi로 순위를 매겨
    구역마다 상위 per_section개를 돌려준다. 구역 순서는 chunks에 처음 나온 순서, 구역 안은 점수 순
    (같은 점수면 원래 순서)."""
    if not chunks or per_section <= 0:
        return []
    corpus = [keyword_search.tokenize(f'{c.get("title", "")}\n{c.get("text", "")}') or [""] for c in chunks]
    scores = BM25Okapi(corpus).get_scores(_query_tokens(question))
    by_section = {}
    for i, chunk in enumerate(chunks):
        by_section.setdefault(chunk.get("section", ""), []).append(i)
    result = []
    for indexes in by_section.values():
        top = sorted(indexes, key=lambda i: -scores[i])[:per_section]
        result.extend(chunks[i] for i in top)
    return result
