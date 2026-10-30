"""기능 2·3: 질문 → 관련 조항 검색 → 근거를 밝힌 답변 생성."""
import json
import threading
from functools import lru_cache

import chromadb
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from boheom_mate.core import glossary
from boheom_mate.core import ingest

ANSWER_MODEL = "gpt-5.4-mini"
TOP_K = 8
# 0이면 GPT가 매번 가장 확률 높은 답을 고른다. 같은 질문에 같은 답이 나오게 하려는 설정
TEMPERATURE = 0

load_dotenv(ingest.BASE_DIR / ".env")
# 질문 분석·답변 생성·채팅 에이전트가 같이 쓰는 LLM (LangChain ChatOpenAI)
_llm = ChatOpenAI(model=ANSWER_MODEL, temperature=TEMPERATURE)
_json_llm = _llm.bind(response_format={"type": "json_object"})


def _ask_json(system, user):
    """시스템 프롬프트와 사용자 글을 보내고 JSON 답을 dict로 돌려준다."""
    reply = _json_llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return json.loads(reply.content)


_db = None
_db_lock = threading.Lock()


def _collection():
    # 여러 방문자(스레드)가 동시에 PersistentClient를 새로 만들면 Chroma가 오류를 내므로 하나만 만들어 같이 쓴다
    global _db
    with _db_lock:
        if _db is None:
            _db = chromadb.PersistentClient(path=str(ingest.DB_DIR))
    return _db.get_or_create_collection(
        ingest.COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def list_sources():
    """DB에 저장된 약관(상품) 파일 이름 목록."""
    metadatas = _collection().get(include=["metadatas"])["metadatas"]
    return sorted({m["source"] for m in metadatas})


# 질문 요지를 GPT에게 자유롭게 쓰게 하면 표현이 조금만 달라도 결과가 달라진다.
# 그래서 보험사 보상 실무처럼 정해진 보기 중에서만 고르게 하고, 검색어는 고른 결과로 만든다.
CAUSES = ["질병", "상해", "해당없음"]
CARE_TYPES = ["입원", "통원", "처방조제"]
# 질문 유형 → 꼭 봐야 하는 약관 조항 이름. 검색어에 붙여서 해당 조항을 찾게 한다
INTENTS = {
    "보상여부": "보장종목별 보상내용 보상하지 않는 사항",
    "보상금액": "보장종목별 보상내용 공제금액 보장대상의료비",
    "보상한도": "보험가입금액 한도 보상한도",
    "청구절차": "보험금의 청구 구비서류",
    "지급시기": "보험금의 지급절차 지급기일",
    "청구기한": "소멸시효",
    "해지환급": "계약자의 임의해지 해약환급금",
    "청약철회": "청약의 철회",
    "보험료": "보험료의 납입 보험료의 계산",
    "갱신": "계약의 자동갱신 재가입",
    "기타": "",
}

ANALYZE_PROMPT = f"""사용자의 실손의료보험 질문에서 요지를 뽑아 JSON으로만 답하세요.
같은 뜻의 질문이면 표현이 달라도 반드시 같은 결과가 나와야 합니다.

{{"subject": [...], "cause": "...", "care": [...], "intent": "...", "terms": [...]}}

- subject: 질문의 대상(병명, 치료, 검사, 사고 등) 1~3개. 질문에 나온 명사를 그대로 쓰세요.
  예: 독감, 임플란트, MRI, 골절, 쌍꺼풀, 유독가스, 약값. 대상이 없으면 [].
- cause: {" / ".join(CAUSES)} 중 하나. 병이면 질병, 사고·부상이면 상해, 계약·절차 질문이면 해당없음.
- care: {CARE_TYPES} 중 해당하는 것만. 입원이라고 하지 않았으면 병원 진료는 통원, 약값은 처방조제.
- intent: {" / ".join(INTENTS)} 중 하나.
  "받을 수 있나요", "보장되나요", "청구 되나요", "어떤 보장"은 모두 보상여부입니다.
  "얼마 받나요", "얼마를 빼나요"는 보상금액, "몇 번까지", "최대 얼마"는 보상한도입니다.
- terms: subject를 약관에서 쓰는 용어로 바꾼 것 0~4개.
  예: MRI → 자기공명영상진단, 임플란트 → 치과치료, 1인실 → 상급병실료, 약값 → 처방조제비,
  쌍꺼풀 → 미용목적 성형수술, 해지 → 해약환급금. 금액·비율·횟수는 쓰지 마세요."""


def _hint(question):
    """용어 사전에서 찾은 약관 용어를 한 줄로. 없으면 빈 문자열."""
    terms = glossary.lookup(question)
    return f"[약관 용어 참고] {', '.join(terms)}" if terms else ""


def _pick(value, allowed, default):
    return value if value in allowed else default


@lru_cache(maxsize=1000)
def analyze_question(question):
    """질문의 요지를 GPT로 뽑는다. 보기가 정해진 항목은 보기 밖의 답을 버린다.

    temperature=0이어도 GPT 결과가 아주 가끔 달라지므로, 한 번 분석한 질문은 기억해 두고 다시 쓴다.
    (앱을 다시 켜면 기억이 지워진다)
    반환값: {"subject": [...], "cause": str, "care": [...], "intent": str, "terms": [...]}
    """
    result = _ask_json(ANALYZE_PROMPT, "\n".join(filter(None, [question, _hint(question)])))
    as_list = lambda v: [str(x).strip() for x in (v if isinstance(v, list) else [v] if v else []) if str(x).strip()]
    return {
        "subject": as_list(result.get("subject"))[:3],
        "cause": _pick(result.get("cause"), CAUSES, "해당없음"),
        # 순서가 달라도 같은 검색어가 되도록 정해진 순서로 정렬한다
        "care": [c for c in CARE_TYPES if c in as_list(result.get("care"))],
        "intent": _pick(result.get("intent"), INTENTS, "기타"),
        "terms": as_list(result.get("terms"))[:4],
    }


def summary_of(analysis):
    """화면에 보여줄 요지 한 줄. 예: 독감 · 질병 · 통원 · 보상여부"""
    cause = analysis["cause"] if analysis["cause"] != "해당없음" else ""
    return " · ".join(filter(None, [", ".join(analysis["subject"]), cause, ", ".join(analysis["care"]), analysis["intent"]]))


def build_queries(analysis):
    """요지로 검색어를 만든다. 같은 요지면 질문 표현이 달라도 검색어가 똑같다.
    반환값: (벡터 검색어 목록, 키워드 검색용 글)"""
    cause = analysis["cause"] if analysis["cause"] != "해당없음" else ""
    care = " ".join(analysis["care"])
    subject = " ".join(analysis["subject"])
    terms = " ".join(analysis["terms"])
    intent = INTENTS[analysis["intent"]]
    queries = [
        # ① 무엇에 대해 묻는가 (대상 + 약관 용어)
        " ".join(filter(None, [subject, terms, cause, care])),
        # ② 어떤 조항을 봐야 하는가 (원인·치료 형태 + 질문 유형별 조항)
        " ".join(filter(None, [cause, care, terms, intent])),
    ]
    queries = [q for q in dict.fromkeys(queries) if q.strip()]
    keyword_text = " ".join(filter(None, [subject, terms, cause, care, intent]))
    return queries, keyword_text


# 질문 유형별로 꼭 봐야 하는 조항(제목이 이것으로 시작하는 조항). 보상 담당자가 확인하는 순서와 같다.
# 보통약관과 각 특약에서 가장 관련 있는 조각을 하나씩 검색 순위와 상관없이 후보에 넣는다.
REQUIRED_ARTICLES = {
    "보상여부": ["보장종목별 보상내용", "보상하지 않는 사항"],
    "보상금액": ["보장종목별 보상내용"],
    "보상한도": ["보장종목별 보상내용", "보험가입금액 한도"],
}
# 필수 조항을 찾을 구역: 실제 보장을 정하는 보통약관과 보장 특약. 안내자료·제도성 특약·별표·법규는 뺀다
NON_COVERAGE_SECTIONS = (ingest.GUIDE, "제도성 특약", "별표", "관련 법규")
USE_REQUIRED_ARTICLES = True

# 검색 방식. "vector": 의미 검색만, "hybrid": 직접 구현한 하이브리드(요지 분석+필수 조항),
# "langchain": LangChain EnsembleRetriever(벡터 + kiwi BM25, 원래 질문). E7에서 채택
SEARCH_MODE = "langchain"
# 각 검색에서 후보를 몇 개씩 가져와 합칠지
CANDIDATES = 20
# 순위 합치기(RRF) 상수. 클수록 1등과 10등의 점수 차이가 줄어든다. 흔히 60을 쓴다
RRF_C = 60
# 요지로 만든 검색어에 원래 질문도 함께 쓸지. 쓰면 질문의 세부 표현을 살리지만 표현에 따라 결과가 흔들린다
INCLUDE_RAW_QUESTION = True
SEARCH_SETTINGS = {
    "include_raw_question": INCLUDE_RAW_QUESTION,
    "required_articles": USE_REQUIRED_ARTICLES,
    "search": SEARCH_MODE,
    "query": "structured",
    "chunking": "sections",
    "embed_with_label": ingest.EMBED_WITH_LABEL,
    "candidates": CANDIDATES,
    "rrf_c": RRF_C,
}


def _article(meta, doc, **extra):
    return {
        "section": meta.get("section", ""),
        "article_no": meta["article_no"],
        "title": meta["title"],
        "text": doc,
        **extra,
    }


LC_VECTOR_K = 20  # 벡터 검색 후보 수
LC_BM25_K = 30  # BM25 후보 수
LC_WEIGHTS = [0.5, 0.5]  # EnsembleRetriever 안에서 벡터, BM25 가중치
# 원래 질문 검색과 요지 검색어 검색의 가중치. 원래 질문이 세부 표현을 살리고, 요지 검색어가 조항 이름을 보탠다(E8)
LC_QUERY_WEIGHTS = [2, 1]
SEARCH_SETTINGS.update({
    "lc_vector_k": LC_VECTOR_K, "lc_bm25_k": LC_BM25_K,
    "lc_weights": LC_WEIGHTS, "lc_query_weights": LC_QUERY_WEIGHTS,
})


@lru_cache(maxsize=1)
def _lc_embeddings():
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=ingest.EMBEDDING_MODEL)


def _lc_vector_retriever(source):
    """기존 Chroma 컬렉션을 그대로 LangChain에 연결한다(재색인 없음). 같은 클라이언트를 같이 쓴다."""
    from langchain_chroma import Chroma

    _collection()  # 공용 PersistentClient(_db)를 먼저 만든다
    store = Chroma(client=_db, collection_name=ingest.COLLECTION_NAME, embedding_function=_lc_embeddings())
    return store.as_retriever(search_kwargs={"k": LC_VECTOR_K, "filter": {"source": source}})


@lru_cache(maxsize=10)
def _lc_bm25(source, doc_count):
    """상품별 BM25 검색기. 토큰화는 kiwi(keyword_search.tokenize)를 쓰고, 조각 앞에 구역·조항 제목을 붙여 센다.
    doc_count가 바뀌면(약관을 다시 넣으면) 새로 만든다."""
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document
    from boheom_mate.core import keyword_search

    data = _collection().get(where={"source": source}, include=["documents", "metadatas"])
    prefix = {doc: f'{m.get("section", "")} {m["article_no"]} {m["title"]}\n'
              for doc, m in zip(data["documents"], data["metadatas"])}
    docs = [Document(page_content=doc, metadata={**m, "id": id_})
            for id_, doc, m in zip(data["ids"], data["documents"], data["metadatas"])]
    retriever = BM25Retriever.from_documents(
        docs, preprocess_func=lambda text: keyword_search.tokenize(prefix.get(text, "") + text))
    retriever.k = LC_BM25_K
    return retriever


def _intent_query(analysis):
    """요지로 만든 검색어 하나: 대상 + 약관 용어 + 원인 + 치료 + 질문 유형별 조항 이름."""
    cause = analysis["cause"] if analysis["cause"] != "해당없음" else ""
    return " ".join(filter(None, [
        " ".join(analysis["subject"]), " ".join(analysis["terms"]), cause,
        " ".join(analysis["care"]), INTENTS[analysis["intent"]],
    ]))


def _doc_count(source):
    return len(_collection().get(where={"source": source}, include=[])["ids"])


def warm_up(source):
    """BM25 검색기를 미리 만든다. 서버에서 첫 질문이 오래 걸리지 않게 앱 시작 때 부른다."""
    _lc_bm25(source, _doc_count(source))


def _search_langchain(question, source, k, debug):
    """LangChain 하이브리드 검색.

    1. EnsembleRetriever: 벡터 검색(OpenAI 임베딩 + Chroma)과 BM25(kiwi 형태소)를 가중 RRF로 합친다.
    2. 이 검색을 원래 질문과 요지 검색어로 한 번씩 돌리고, 두 순위를 2:1 가중 RRF로 다시 합친다.
    """
    from langchain_classic.retrievers import EnsembleRetriever

    ensemble = EnsembleRetriever(
        retrievers=[_lc_vector_retriever(source), _lc_bm25(source, _doc_count(source))], weights=LC_WEIGHTS)
    analysis = analyze_question(question)
    intent_query = _intent_query(analysis)
    queries = [question] + ([intent_query] if intent_query and intent_query != question else [])
    scores, docs = {}, {}
    for query, weight in zip(queries, LC_QUERY_WEIGHTS):
        for rank, d in enumerate(ensemble.invoke(query), start=1):
            docs[d.page_content] = d
            scores[d.page_content] = scores.get(d.page_content, 0) + weight / (RRF_C + rank)
    ranked = sorted(scores, key=scores.get, reverse=True)[:k]
    if debug is not None:
        debug["analysis"] = analysis
        debug["queries"] = queries
        debug["keywords"] = intent_query
    return [_article(docs[t].metadata, t, score=scores[t], id=docs[t].metadata.get("id") or docs[t].id)
            for t in ranked]


def search(question, source, k=TOP_K, debug=None):
    """관련 조각 k개를 고른다. 같은 상품 안에서만 찾는다.

    1. GPT가 질문의 요지를 정해진 보기에서 고른다 (analyze_question)
    2. 요지로 검색어를 만든다. 원래 질문의 말투("저 그럼", "~인지")는 검색에 쓰지 않는다
    3. 의미 검색(벡터) 2개와 키워드 검색(BM25) 1개의 순위를 RRF로 합친다.
       여러 검색에서 고르게 상위권인 조각이 1등이 된다.
    """
    from boheom_mate.core import keyword_search

    question = " ".join(question.split())
    if SEARCH_MODE == "langchain":
        return _search_langchain(question, source, k, debug)
    analysis = analyze_question(question)
    queries, keyword_text = build_queries(analysis)
    if not queries:
        # 요지를 하나도 뽑지 못한 질문(보험과 무관한 질문 등)은 원래 질문으로 찾는다
        queries, keyword_text = [question], question
    elif INCLUDE_RAW_QUESTION:
        queries = [*queries, question]
    keyword_tokens = keyword_search.tokenize(keyword_text)
    if debug is not None:
        debug["analysis"] = analysis
        debug["queries"] = queries
        debug["keywords"] = keyword_text
        debug["keyword_tokens"] = keyword_tokens

    embeddings = _lc_embeddings().embed_documents(queries)
    n = k if SEARCH_MODE == "vector" else CANDIDATES
    result = _collection().query(query_embeddings=embeddings, n_results=n, where={"source": source})
    vector_lists = [
        list(zip(ids, docs, metas, dists))
        for ids, docs, metas, dists in zip(
            result["ids"], result["documents"], result["metadatas"], result["distances"]
        )
    ]

    if SEARCH_MODE == "vector":
        # 여러 검색 결과를 합치고, 같은 조각은 더 가까운 거리로 한 번만 남긴다.
        best = {}
        for hits in vector_lists:
            for id_, doc, meta, dist in hits:
                if id_ not in best or dist < best[id_]["distance"]:
                    best[id_] = _article(meta, doc, distance=dist)
        return sorted(best.values(), key=lambda a: a["distance"])[:k]

    keyword_hits = keyword_search.search(keyword_tokens, source, CANDIDATES)
    found = {}
    for hits in [*vector_lists, keyword_hits]:
        for rank, (id_, doc, meta, _) in enumerate(hits, start=1):
            if id_ not in found:
                found[id_] = _article(meta, doc, score=0.0, id=id_)
            found[id_]["score"] += 1 / (RRF_C + rank)
    ranked = sorted(found.values(), key=lambda a: a["score"], reverse=True)

    required = []
    if USE_REQUIRED_ARTICLES and analysis["intent"] in REQUIRED_ARTICLES:
        required = _required_chunks(
            REQUIRED_ARTICLES[analysis["intent"]], source, embeddings, keyword_tokens, found
        )
    if debug is not None:
        debug["required"] = [f'{a["section"]} {a["article_no"]}' for a in required]
    # 필수 조각을 먼저 자리 잡고, 남은 자리를 검색 순위대로 채운다. 화면·평가에는 점수 순으로 보낸다
    picked = {a["id"]: a for a in required[:k]}
    for a in ranked:
        if len(picked) >= k:
            break
        picked.setdefault(a["id"], a)
    return sorted(picked.values(), key=lambda a: a["score"], reverse=True)


def _required_chunks(title_prefixes, source, embeddings, keyword_tokens, found):
    """필수 조항마다, 보통약관과 각 보장 특약에서 질문과 가장 가까운 조각을 하나씩 고른다.

    해당 조항의 조각들만 놓고 의미 검색 순위와 키워드 검색 순위를 RRF로 합쳐 1등을 고른다.
    전체 검색에서 받은 점수(found)가 있으면 그 점수를 쓰고, 없으면 0으로 둔다(맨 뒤로 정렬).
    """
    from boheom_mate.core import keyword_search

    data = _collection().get(where={"source": source}, include=["metadatas"])
    titles = sorted({
        m["title"] for m in data["metadatas"]
        if any(m["title"].startswith(t) for t in title_prefixes)
        and not m.get("section", "").startswith(NON_COVERAGE_SECTIONS)
    })
    if not titles:
        return []
    where = {"$and": [{"source": source}, {"title": {"$in": titles}}]}
    count = sum(1 for m in data["metadatas"] if m["title"] in titles)
    result = _collection().query(query_embeddings=embeddings, n_results=count, where=where)

    scores = {}
    for ids, docs, metas in zip(result["ids"], result["documents"], result["metadatas"]):
        for rank, (id_, doc, meta) in enumerate(zip(ids, docs, metas), start=1):
            if meta.get("section", "").startswith(NON_COVERAGE_SECTIONS):
                continue
            entry = scores.setdefault(id_, {"meta": meta, "doc": doc, "score": 0.0})
            entry["score"] += 1 / (RRF_C + rank)
    allowed = set(scores)
    keyword_ranked = [h for h in keyword_search.search(keyword_tokens, source, 10_000) if h[0] in allowed]
    for rank, (id_, _, _, _) in enumerate(keyword_ranked, start=1):
        scores[id_]["score"] += 1 / (RRF_C + rank)

    best = {}
    for id_, e in scores.items():
        key = (e["meta"].get("section", ""), e["meta"]["title"])
        if key not in best or e["score"] > best[key][1]["score"]:
            best[key] = (id_, e)
    chunks = []
    for id_, e in best.values():
        score = found[id_]["score"] if id_ in found else 0.0
        chunks.append(_article(e["meta"], e["doc"], score=score, id=id_))
    return sorted(chunks, key=lambda a: a["score"], reverse=True)


SYSTEM_PROMPT = """당신은 보험약관 상담 도우미입니다.
아래 [근거1]~[근거N] 약관 조항만 근거로 사용자의 질문에 한국어로 답하세요.
약관에 없는 내용은 추측하지 마세요.

반드시 다음 JSON 형식으로만 답하세요.
{"found": true 또는 false, "answer": "답변", "evidence": [실제로 근거로 쓴 번호들]}

- 약관에는 구체적인 병명이나 상황이 적혀 있지 않습니다. 질문 속 상황을 약관의 일반 개념
  (질병/상해, 입원/통원, 급여/비급여 등)으로 분류해 해당 조항을 적용하세요.
  예: 맹장 수술 → 질병으로 인한 입원·수술. 이런 상식 수준의 분류는 추측이 아닙니다.
  분류했다면 답변에 그 사실을 밝히세요.
- 보상 여부는 보상하는 조항과 보상하지 않는 사항(면책)을 함께 확인해 답하세요.
- 면책(보상하지 않는 사항)은 질문의 상황과 직접 관련된 항목만 언급하세요.
  질문과 무관한 면책 항목(예: 감기 질문에 임신·출산·비만·요실금 등)은 나열하거나 인용하지 마세요.
  관련된 면책이 없으면 면책은 언급하지 않아도 됩니다.
- 주어진 조항으로 답할 수 있으면 found=true, 답변에 쓴 근거 번호를 evidence에 넣으세요.
- evidence에는 답변의 결론을 **직접 뒷받침한 핵심 조항만** 넣으세요(보통 1~3개).
  관련 있어 보인다는 이유로 참고만 한 조항은 넣지 마세요. 핵심 근거를 적게 추리는 것이 좋습니다.
- 주어진 조항이 질문의 주제(예: 자동차 수리비, 대출 금리)를 전혀 다루지 않을 때만
  found=false, evidence=[]로 하고 answer는 비워 두세요.
- 실손의료보험은 사람이 다치거나 아파서 낸 의료비만 보상합니다. 차량 수리비, 집·가구·물건 같은 재산 손해,
  사은품처럼 의료비가 아닌 것을 물으면, 비슷한 단어(자동차보험, 화재 등)가 조항에 보여도 found=false로 하세요."""

NOT_FOUND_MESSAGE = "약관에서 확인할 수 없는 내용입니다. 정확한 내용은 보험사 고객센터에 문의해 주세요."


def answer(question, source, articles=None):
    """기능 2·3: 답변과 실제로 근거가 된 조항(원문 포함)을 반환한다.

    articles를 넘기면 검색을 다시 하지 않고 그 조항으로 답한다(평가 스크립트용).
    반환값: {"found": bool, "answer": str, "evidence": [조항, ...], "summary": 질문 요지 한 줄}
    """
    summary = summary_of(analyze_question(" ".join(question.split())))
    if articles is None:
        articles = search(question, source)
    context = "\n\n".join(
        f"[근거{i}] [{a.get('section', '')}] {a['article_no']} {a['title']}\n{a['text'][:ingest.MAX_EMBED_CHARS]}"
        for i, a in enumerate(articles, start=1)
    )
    hint = _hint(question)
    result = _ask_json(SYSTEM_PROMPT, f"{context}\n\n[질문]\n{question}\n[질문 요지] {summary}" + (f"\n{hint}" if hint else ""))

    # 모델이 없는 번호를 대더라도 실제 검색된 조항만 근거로 인정한다.
    evidence = [
        {"section": a.get("section", ""), "article_no": a["article_no"], "title": a["title"], "text": a["text"]}
        for i, a in enumerate(articles, start=1)
        if i in set(result.get("evidence") or [])
    ]
    if not result.get("found") or not evidence:
        return {"found": False, "answer": NOT_FOUND_MESSAGE, "evidence": [], "summary": summary}
    return {"found": True, "answer": result["answer"], "evidence": evidence, "summary": summary}
