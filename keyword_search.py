"""키워드 검색(BM25).

벡터 검색은 "뜻이 비슷한" 조각을 찾지만, "쌍꺼풀", "유독가스"처럼 질문에 정확한 단어가 있을 때
그 단어가 들어 있는 조각을 놓치기도 한다. 그래서 단어가 실제로 겹치는지 세는 검색을 함께 쓴다.

1. 한국어 형태소 분석기(kiwi)로 문장에서 명사 같은 핵심 단어를 뽑는다.
   "비급여 병실료의" → 비급여, 급여, 병실료, 병실, 료
2. BM25 점수로 순위를 매긴다. 질문의 단어가 조각에 많이 나올수록,
   그리고 그 단어가 약관 전체에서 드물수록 점수가 높다.
"""
import math
from collections import Counter
from functools import lru_cache

from kiwipiepy import Kiwi

# 검색에 쓰는 품사: 명사(NN*), 외국어(SL, 예: MRI), 숫자(SN), 어근(XR)
CONTENT_TAGS = ("NNG", "NNP", "NNB", "SL", "SN", "XR")
# 바로 뒤 명사와 붙여야 뜻이 사는 것: 접두사(비+급여 → 비급여), 일부 부사(유독+가스 → 유독가스)
PREFIX_TAGS = ("XPN", "MAG")
# 너무 흔해서 검색에 도움이 안 되는 단어
STOPWORDS = {"것", "수", "등", "때", "경우", "이", "그", "및", "중", "얼마", "무엇", "어떤", "궁금", "질문"}

K1 = 1.5
B = 0.75

_kiwi = Kiwi()


def tokenize(text):
    """문장을 검색용 단어 목록으로 바꾼다.

    띄어쓰기 없이 붙어 있는 명사들은 하나하나와 합친 단어를 모두 넣는다.
    그래야 "병실료"로 물어도 "상급병실료"가 있는 조각을 찾고, "비급여"와 "급여"도 구분된다.
    """
    tokens = []
    group = []  # 띄어쓰기 없이 붙어 있는 단어 묶음
    prev_end = -1

    def flush():
        nouns = [form for form, tag in group if tag in CONTENT_TAGS]
        if nouns:
            tokens.extend(n for n in nouns if n not in STOPWORDS and (len(n) > 1 or n.isdigit()))
            if len(group) > 1:
                tokens.append("".join(form for form, _ in group))
        group.clear()

    for t in _kiwi.tokenize(text):
        attached = t.start == prev_end
        if t.tag in CONTENT_TAGS or t.tag in PREFIX_TAGS:
            if group and not attached:
                flush()
            group.append((t.form, t.tag))
        else:
            flush()
        prev_end = t.start + t.len
    flush()
    # 붙은 접두사가 뒤 명사 없이 끝난 경우(예: 부사 하나)는 버린다
    return [tok for tok in tokens if tok]


class BM25:
    def __init__(self, docs):
        self.docs_tokens = [tokenize(d) for d in docs]
        self.doc_freqs = [Counter(toks) for toks in self.docs_tokens]
        self.doc_lens = [len(toks) for toks in self.docs_tokens]
        self.avg_len = sum(self.doc_lens) / max(len(docs), 1)
        df = Counter(tok for toks in self.docs_tokens for tok in set(toks))
        n = len(docs)
        self.idf = {tok: math.log(1 + (n - f + 0.5) / (f + 0.5)) for tok, f in df.items()}

    def scores(self, query_tokens):
        result = []
        for freqs, length in zip(self.doc_freqs, self.doc_lens):
            score = 0.0
            for tok in set(query_tokens):
                tf = freqs.get(tok, 0)
                if tf:
                    score += self.idf[tok] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.avg_len))
            result.append(score)
        return result


@lru_cache(maxsize=10)
def build_index(source, doc_count):
    """상품별 BM25 색인. doc_count가 바뀌면(약관을 다시 넣으면) 새로 만든다."""
    import rag

    data = rag._collection().get(where={"source": source}, include=["documents", "metadatas"])
    texts = [
        f'{m.get("section", "")} {m["article_no"]} {m["title"]}\n{doc}'
        for doc, m in zip(data["documents"], data["metadatas"])
    ]
    return data["ids"], data["documents"], data["metadatas"], BM25(texts)


def search(query_tokens, source, k):
    """BM25 점수 상위 k개 조각. 반환값: [(id, 문서, 메타데이터, 점수), ...]"""
    import rag

    count = len(rag._collection().get(where={"source": source}, include=[])["ids"])
    ids, docs, metas, index = build_index(source, count)
    scores = index.scores(query_tokens)
    order = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)[:k]
    return [(ids[i], docs[i], metas[i], scores[i]) for i in order if scores[i] > 0]
