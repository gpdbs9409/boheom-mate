# Design Document

## Overview

이 설계는 요구사항(`requirements.md`)의 "표 처리 개선"을 구현하기 위한 것이다. 목표는 세 가지다.

1. 텍스트 추출형 약관 PDF에서 **표의 행·열 구조를 보존**해 뽑아낸다(Req 1).
2. 뽑은 표를 **검색 가능한 텍스트 조각**(`Table_Representation`)으로 바꿔 기존 조항 청크와 함께 Chroma(벡터) + BM25 색인에 넣는다. 특히 항목명과 숫자를 같은 조각에 유지해 D13/D17 같은 표 안 사실이 하이브리드 검색에 걸리게 한다(Req 2, 5).
3. 표에서 유래한 근거를 화면에서 **행 단위로 읽히게** 표시한다(Req 4).

동시에 **지금 잘 되는 것을 깨지 않는다**. 조항 단위 청킹·구역 꼬리표·`_split_long` 재분할·메타데이터 필드·`TOP_K`·비표 문단 정리·`calculator.py` 금액 계산은 그대로 둔다(Req 3, 6). 개선 여부는 코드 추측이 아니라 기존 평가 하버스(`eval/run_eval.py`)의 전후 점수로 증명한다(Req 5).

### 중요한 현실 인식: 모든 "표"가 깨지는 것은 아니다

설계에 들어가기 전에 반드시 짚어야 할 사실이 있다. base 약관 13쪽의 자기부담금 차감 표를 `pypdf`로 뽑아 확인한 결과:

- 그 표는 **괘선(셀 테두리) 없는 "텍스트 목록형" 표**였고, `pypdf`가 줄바꿈을 유지한 채 이미 꽤 읽을 만하게 뽑아낸다. 예: `∙ 통원 ... : 3만원과 보장대상의료비의 30% 중 큰 금액`, `∙ 상급병실료 차액 : 비급여 병실료의 50%에 해당하는 금액`.
- 즉 D13(`3만원과 보장대상의료비의 30% 중 큰 금액`)과 D17(`비급여 병실료의 50%`)의 `evidence_contains` 문구는 **이미 추출된 텍스트 안에 존재한다**.

따라서 D13/D17이 틀리는 원인은 "표 텍스트가 사라져서"가 아니라 **랭킹(순위)** 때문이다. `eval/EXPERIMENTS.md`가 이를 뒷받침한다:

- E2 기록: D17의 정답 문구는 특약1 제3조에 있는데, "상급병실료"가 많이 나오는 다른 조각들(국민건강보험 규칙의 비급여 대상 등)에 **밀린다**.
- E4 한계: 조항은 맞는데 그 조항의 여러 조각 중 정답 문구가 없는 조각이 뽑히는 "조각이 틀리는" 문제.

이 설계는 이 사실을 정면으로 다룬다:

- (a) 진짜로 무너지는 **괘선형 숫자 표**와 이미 읽히는 **텍스트 목록형 표**를 구분한다.
- (b) **먼저 진단**한다 — D13/D17이 텍스트 손실 문제인지 랭킹 문제인지 기존 eval 디버그 출력과 `pypdf` vs 표 추출기 비교로 확인한 다음에 고치는 방향을 정한다.
- (c) 표 추출만으로는 랭킹 주도 실패가 안 고쳐질 수 있으므로, **표 조각을 section 꼬리표가 붙은 독립 검색 단위**로 만들어 "항목명+숫자"가 한 조각에 모이게 하고, 그 조각이 필수 조항(`REQUIRED_ARTICLES`) 경로로도 후보에 들어오게 해 랭킹을 함께 개선한다.

과한 설계는 피한다. 가장 가벼우면서 eval 하버스로 검증 가능한 방법을 택하고, 비표·텍스트 목록형 텍스트 추출은 **건드리지 않아 회귀를 막는다**.

## Investigation / Diagnosis (설계의 첫 단계)

고치기 전에, 대상 표(최소 D13, D17)별로 실패 원인을 먼저 분류한다. 이 단계는 코드 변경 없이 수행하며, 결과에 따라 아래 구현 범위가 달라진다.

### 진단 절차

1. **현재 검색 디버그 확인 (텍스트 손실 여부)**
   `eval/run_eval.py`는 각 문항의 `retrieved`(검색된 조각의 section·article_no·title)와 채점 기준(`evidence_contains` 문구가 `norm()`으로 공백 제거 후 어느 조각에 부분 문자열로 들어 있는가)을 결과 JSON에 남긴다. 재색인 없이 현재 DB로 `--retrieval-only`를 돌려 D13/D17의 결과 행을 본다.
   - 정답 문구가 **어떤 조각에도 없다** → 텍스트 손실 문제(표가 무너져 숫자가 사라짐).
   - 정답 문구가 **DB 어딘가의 조각엔 있는데 상위 8에 안 들었다** → 랭킹 문제.
   DB에 문구가 있는지는 `rag._collection().get(where={"source": ...})`의 documents에서 `norm()` 부분 문자열로 직접 확인한다(짧은 일회성 스크립트/REPL).

2. **`pypdf` vs 표 추출기 비교 (base 약관)**
   정답 문구가 있는 페이지(예: 13쪽)에 대해 `pypdf`의 `page.extract_text()` 결과와 표 추출기(아래 pdfplumber 후보)의 `page.extract_tables()` 결과를 나란히 본다.
   - 추출기가 그 숫자 표를 행 목록으로 잡아내고, `pypdf`는 한 줄로 뭉갰다 → 괘선형 표 → **표 추출이 가치 있음**.
   - `pypdf`도 이미 줄바꿈을 유지해 읽힌다(13쪽 자기부담금 표가 이 경우) → 텍스트 목록형 표 → **텍스트 추출은 그대로 두고 랭킹만 손본다**.

### 예상 결과와 분기

위 샘플링 결과, **D13/D17은 텍스트 손실이 아니라 랭킹 문제**로 판단된다(문구가 이미 추출됨, EXPERIMENTS E2/E4가 랭킹 패배를 기록). 따라서:

- **텍스트 목록형 표**에 대해서는 텍스트 추출을 바꾸지 않는다(회귀 위험 0). 대신 그 표를 **독립된 `Table_Representation` 조각**으로도 색인해, 항목명+숫자가 한 조각에 모이고 section 꼬리표가 붙은 작은 단위로 검색·필수조항 경로에 노출되게 한다. 이것이 랭킹 개선의 핵심이다.
- **괘선형 숫자 표**가 실제로 발견되면(진단 2에서 확인되면) 그 표는 표 추출기로 행·열을 복원해 같은 `Table_Representation` 경로를 탄다.

이 진단 결과는 구현 착수 전 `Regression_Baseline` 측정과 함께 기록한다(Measurability 참조).

## Architecture

기존 레이어 구조(`core` / `ui` / `store` / `integrations`)를 유지한다. 변경은 오프라인 인게스트 경로와 표시 경로에 국한된다. 질의 시점에는 표를 추출하지 않는다(Req 6.3).

```mermaid
flowchart TD
    subgraph Offline[오프라인 색인 - ingest.py]
        PDF[약관 PDF] --> ET["extract_text() - pypdf (변경 없음)"]
        PDF --> TE["extract_tables() - Table_Extractor (신규)"]
        ET --> SA["split_articles() - 조항 청크 (변경 없음)"]
        TE --> TR["table_representations() - Table_Representation 생성 (신규)"]
        SA --> MERGE["articles 리스트 병합"]
        TR --> MERGE
        MERGE --> BI["build_index() - Chroma + 메타데이터"]
    end

    subgraph Query[질의 - rag.py (로직 변경 없음)]
        Q[질문] --> SR["search(): 벡터 3 + BM25 1 → RRF + REQUIRED_ARTICLES"]
        BI -.색인.-> SR
        SR --> ANS["answer() / calculator.py"]
    end

    subgraph UI[표시 - display.py]
        SR --> DISP["to_markdown(): 표 조각 행 단위 렌더 (확장)"]
    end
```

핵심 아이디어: 표는 **새로운 종류의 조각**이지 새로운 저장소나 새로운 검색 알고리즘이 아니다. `Table_Representation`은 기존 article dict와 **같은 모양**(`{section, article_no, title, text}`)으로 만들어져 `articles` 리스트에 섞여 들어가고, 그 뒤로는 `build_index` → Chroma/BM25 → `search` → `display`가 기존 조각과 동일하게 흐른다. 이렇게 하면 변경 표면이 최소화되고 회귀 위험이 낮다.

## Components and Interfaces

### 1. `Table_Extractor` (신규, `boheom_mate/core/ingest.py`)

표 추출 라이브러리로 **pdfplumber**를 추가한다. 이유:

- 순수 파이썬 + 현재 스택과 충돌 없음, LangChain 불필요(Req 6.1).
- `page.extract_tables()`가 **괘선형 표**(지급기준표 style)를 행 목록(각 행은 셀 목록)으로 복원한다 — 이것이 Req 1.1/1.2가 요구하는 바로 그 형태.
- 텍스트가 안 뽑히는 스캔 PDF에서는 `extract_tables()`가 빈 목록을 돌려주므로 Req 1.4를 자연히 만족한다.

버전은 `requirements.txt`에 정확히 고정한다(Req 6.2). 설치 후 실제 해석되는 버전으로 핀을 맞춘다:

```
pdfplumber==0.11.4
```

인터페이스:

```python
def extract_tables(pdf_path) -> list[dict]:
    """각 페이지에서 표를 추출한다.
    반환: [{"page": int, "table_index": int, "rows": [[cell, ...], ...]}, ...]
    - 표가 없는 페이지는 결과에 포함하지 않는다(Req 1.3: 빈 표 목록).
    - 텍스트 미추출 PDF면 전체가 빈 목록(Req 1.4).
    - 특정 페이지에서 예외가 나면 그 페이지만 건너뛰고 계속한다(Req 3.4: 비표 텍스트는 extract_text가 이미 담당).
    """
```

셀의 `None`은 빈 문자열로 정규화하고, 각 셀은 `strip()` 한다. 행·열 구조는 `rows`(행 목록, 각 행은 셀 목록)로 보존한다(Req 1.1).

### 2. `Table_Representation` 생성기 (신규, `boheom_mate/core/ingest.py`)

추출된 표(행 목록)를 검색 가능한 텍스트 조각으로 바꾼다.

```python
def table_representations(tables, page_sections) -> list[dict]:
    """표를 article dict와 같은 모양의 조각으로 변환한다.
    각 표 → 하나의 Table_Representation 조각(필요 시 _split_long으로 재분할).
    반환 원소: {"section": ..., "article_no": ..., "title": ..., "text": ..., "is_table": True}
    """
```

텍스트 표현 규칙(Req 2.1, 2.3):

- 표 → 여러 줄. **한 행 = 한 줄**로 만들어 행 구조를 텍스트에 보존한다.
- 한 행의 셀들은 구분자로 이어 붙이되(예: ` | ` 또는 `: `), **항목명과 그 행의 숫자 값을 반드시 같은 줄(= 같은 조각)에 둔다**(Req 2.3). 예: `상급병실료 차액 | 비급여 병실료의 50%에 해당하는 금액`.
- 조각 맨 앞에 표 식별 머리말(예: `[표] {title}`)을 둬, 화면 렌더러와 사람이 표 조각임을 알게 한다(Req 4의 분기 신호로도 사용).
- `embed_text`의 라벨 접두사 규칙과의 관계(아래 Data Models에서 상술): 저장/표시되는 `text`에는 셀 값이 그대로 들어가고(Req 2.1/2.3 충족), 임베딩 시에는 `[section] article_no(title)` 라벨이 앞에 붙는다 — 기존 조항과 완전히 동일한 취급.

### 3. `Ingest_Pipeline` 연결 (`boheom_mate/core/ingest.py`)

- `extract_text()`는 **변경하지 않는다**(Req 3.1, 3.4). 조항 청크는 지금과 100% 동일하게 만들어진다.
- `main()`에서 PDF마다 `extract_text()` 경로(조항 청크)와 `extract_tables()` 경로(표 조각)를 각각 만든 뒤 **하나의 `articles` 리스트로 병합**해 `build_index`에 넘긴다.
- base 약관만 재색인하는 기존 방식(`build_index`를 직접 호출)도 그대로 동작한다. `build_index`는 같은 `source`의 기존 행을 지우고 다시 넣으므로 표 조각이 추가돼도 호출 방식이 바뀌지 않는다(Req 6.3).

### 4. `Retrieval_Pipeline` (`boheom_mate/core/rag.py`) — 로직 변경 없음

표 조각은 기존 조각과 모양이 같으므로 `search()`의 벡터·BM25·RRF·`REQUIRED_ARTICLES` 로직을 **바꾸지 않는다**. `TOP_K=8` 유지(Req 3.3). 랭킹 개선은 "표 조각이 작고 항목명+숫자가 뭉쳐 있어서 BM25·벡터 양쪽에서 더 또렷하게 매칭된다"는 데이터 쪽 효과로 얻는다. 표 조각의 `title`/`section`을 그 표가 속한 조항과 맞추면 `REQUIRED_ARTICLES`(예: 보상금액 → "보장종목별 보상내용")의 필수 후보 풀에 자연히 포함돼, D13/D17의 "조각이 틀리는" 문제를 완화한다. (기본적으로 알고리즘 변경 없이 데이터만 바꿔 측정한다. 이것으로 부족하면 EXPERIMENTS의 "다음 후보"대로 후속 실험 여지를 둔다.)

### 5. `Display_Renderer` (`boheom_mate/ui/display.py`)

`to_paragraphs`/`to_markdown`에 표 조각 분기를 **추가**한다(기존 비표 경로는 손대지 않음, Req 4.4).

- 표 조각 신호(머리말 `[표]` 또는 행 구분자 `|`)가 보이면, 저장된 `text`를 **행 단위로 분리**해 둘 이상의 블록으로 만든다(Req 4.1, 4.2).
- 각 행에 기존 `AMOUNT` 정규식 강조와 `KEY_PHRASE` 강조를 **그대로 재사용**한다(Req 4.3). `중 큰 금액`, `공제금액` 등은 이미 `KEY_PHRASE`에 있다.
- 비표 조각은 `NEW_ITEM`/`SPACED_LABEL`/줄 잇기 휴리스틱을 포함해 **변경 전과 동일**하게 처리한다(Req 4.4).

## Data Models

### 조각(청크)의 공통 모양 — 변경 없음

```python
article = {
    "section": str,      # 안내자료/보통약관/특별약관1·2/제도성 특약/별표/관련 법규
    "article_no": str,   # "제3조" 등
    "title": str,
    "text": str,         # 저장·표시되는 원문
}
```

`Table_Representation`도 **이 모양을 그대로 따른다**(+ 선택적 내부 플래그 `is_table`은 화면 분기용이며 Chroma 메타데이터에는 넣지 않는다 → Req 3.2의 메타데이터 필드 불변 보장).

### 표 조각이 받는 section / article_no / title / metadata

- **section**: 표가 등장한 페이지/위치의 구역을 기존 `find_sections`·`section_at`으로 판정해 붙인다(Req 2.2). 즉 표 조각도 조항 청크와 같은 구역 꼬리표 체계를 쓴다.
- **article_no / title**: 표가 속한 조항과 맞춘다. 페이지 상의 가장 가까운 선행 조항 제목(예: "제3조", "보장종목별 보상내용")을 물려받되, 조항을 특정할 수 없으면 `article_no="표"`, `title=표 머리글/캡션`으로 둔다. 조항과 맞추는 이유: `REQUIRED_ARTICLES`가 `title` 접두사로 필수 조항을 고르므로, 표를 올바른 조항 제목에 맞추면 D13/D17이 필수 후보 풀에 들어온다.
- **metadata (Chroma)**: `{source, section, article_no, title}` — 기존과 **동일한 네 필드만**(Req 3.2). 표 여부를 메타데이터로 따로 저장하지 않는다.

### 저장 document vs 임베딩 text 구분 (중요)

`build_index`는 **서로 다른 두 텍스트**를 쓴다:

- Chroma `documents` = `a["text"]` (라벨 없는 원문). 화면 표시·eval 채점(`norm(a["text"])`)·BM25 색인(`keyword_search.build_index`는 `f'{section} {article_no} {title}\n{doc}'`로 document를 읽음)이 모두 이 `text`를 본다.
- 임베딩 벡터 = `embed_text(a)[:MAX_EMBED_CHARS]` = `[section] article_no(title)\n{text}` (라벨 접두사 붙음, `EMBED_WITH_LABEL=True`).

표 조각에 대한 함의:

- Req 2.4/2.5(문구가 `Table_Representation`에 부분 문자열로 존재)와 eval 채점은 **저장 document(`text`)**를 기준으로 하므로, 셀 값(항목명+숫자)을 반드시 `text`에 담아야 한다. 라벨 접두사는 임베딩에만 영향을 주고 채점·표시에는 영향이 없다.
- BM25도 document를 읽으므로, 표 조각의 `text`에 항목명+숫자가 있으면 BM25 매칭에 그대로 기여한다. 추가로 `section/article_no/title` 접두사가 BM25 입력 앞에 붙어 section 꼬리표가 키워드 검색에도 반영된다(Req 2.2의 "BM25 색인에도 section과 함께 저장" 충족).
- 임베딩은 기존 조각과 동일하게 라벨이 붙으므로(E1b 실험이 라벨 유지가 낫다고 확인), 표 조각만 다르게 취급할 필요가 없다.

### `_split_long`과의 관계

표 조각도 `CHUNK_CHARS=1500`을 넘으면 `_split_long`으로 줄 단위 재분할된다. 한 행 = 한 줄이므로 재분할은 **행 경계에서** 일어나 "항목명+숫자 한 줄"이 쪼개지지 않는다(Req 2.3 보존). `_split_long` 자체는 변경하지 않는다(Req 3.1).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

이 기능의 핵심 로직 중 **순수 함수**인 부분(표 → `Table_Representation` 텍스트 변환, 표시 렌더러의 행 분리)은 입력이 다양하고 "모든 입력에 대해 성립해야 하는" 불변식이 있어 속성 기반 테스트(PBT)에 적합하다. 반대로 Chroma 임베딩·검색 랭킹·eval 적중은 외부 서비스(OpenAI 임베딩)와 랭킹에 의존하므로 PBT가 아니라 예제/통합/eval로 검증한다(Testing Strategy 참조).

### Property 1: 표 변환은 모든 셀 값을 보존한다

*For any* 비어 있지 않은 표(행 목록, 각 행은 셀 목록)에 대해, `table_representations`가 만든 `Table_Representation` 텍스트들의 합집합은 원래 표의 모든 비어 있지 않은 셀 문자열을 (공백 정규화 후) 부분 문자열로 포함한다.

**Validates: Requirements 1.1, 2.1**

### Property 2: 항목명과 숫자는 같은 조각 단위에 남는다

*For any* "항목명 셀 + 숫자 값(금액·비율·한도·횟수) 셀"로 이루어진 표의 한 행에 대해, 그 항목명과 숫자 값은 **같은 `Table_Representation` 조각의 같은 줄** 안에 함께 존재한다(재분할이 일어나도 행 경계를 넘지 않는다).

**Validates: Requirements 2.3**

### Property 3: 표가 없으면 빈 결과

*For any* 표가 하나도 검출되지 않는 입력(빈 표 목록)에 대해, `table_representations`는 빈 조각 목록을 반환한다(스캔 PDF로 아무 표도 못 뽑은 경우 포함).

**Validates: Requirements 1.3, 1.4**

### Property 4: 표 조각 렌더링은 한 줄로 뭉치지 않고 행 단위로 나뉜다

*For any* 둘 이상의 행을 가진 `Table_Representation` 조각에 대해, `to_paragraphs`(또는 `to_markdown`)의 출력은 둘 이상의 블록으로 나뉘며, 서로 다른 행의 내용이 하나의 블록으로 합쳐지지 않는다.

**Validates: Requirements 4.1, 4.2**

### Property 5: 비표 텍스트 렌더링은 변경 전과 동일하다

*For any* 비표 본문 텍스트에 대해, 표 분기가 추가된 `to_paragraphs`/`to_markdown`의 출력은 변경 전 구현의 출력과 동일하다(표 분기는 비표 경로에 영향을 주지 않는다).

**Validates: Requirements 3.1, 4.4**

## Error Handling

- **페이지 단위 표 추출 실패/무표**: 특정 페이지에서 `extract_tables()`가 빈 목록을 내거나 예외가 나면 그 페이지는 표 조각을 만들지 않고 넘어간다. 그 페이지의 **비표 텍스트는 바뀌지 않은 `extract_text()` 경로**로 이미 조항 청크에 들어가 있으므로 정보 손실이 없다(Req 3.4). 표 추출은 "있으면 더 얹는" 보강이지, 텍스트 추출을 대체하지 않는다.
- **스캔 이미지 PDF**: `extract_text()`가 빈 문자열이면 조항이 안 생겨 기존대로 "텍스트 추출 실패" 안내가 뜬다(`main()`). `extract_tables()`도 빈 목록을 돌려주므로 표 조각도 없다(Req 1.4). 새로운 실패 모드가 생기지 않는다.
- **셀 `None`/빈 셀**: 빈 문자열로 정규화해 변환이 깨지지 않게 한다.
- **메타데이터 필드 불변**: 표 조각도 `{source, section, article_no, title}` 네 필드만 쓴다. 내부 `is_table` 플래그는 색인 전에 떨어뜨린다(Req 3.2).
- **금액 계산 위임 금지**: 표에서 숫자를 뽑아 색인·표시할 뿐, 금액 계산은 기존대로 `calculator.py`가 담당한다. LLM에 계산을 맡기지 않는다(Req 6.4).

## Testing Strategy

프로젝트에 pytest가 없다. 모든 테스트는 기존 방식(`.venv/bin/python tests/test_xxx.py`, 성공 시 `OK`/`NN/NN 통과` 출력)을 따른다(Req 6.5).

### 속성 기반 테스트 (PBT)

- 라이브러리: **Hypothesis**(파이썬 표준 선택지). 직접 구현하지 않는다. `requirements.txt`에 정확한 버전으로 고정한다(설치 후 핀 확정).
- 각 속성 테스트는 **최소 100회** 반복(Hypothesis `@settings(max_examples=100)`).
- 각 테스트에 설계 속성을 주석으로 단다. 형식: `# Feature: table-extraction, Property 1: 표 변환은 모든 셀 값을 보존한다`.
- 각 Correctness Property를 **하나의** 속성 기반 테스트로 구현한다.
- 생성기(generator): 무작위 표는 "행 목록(각 행은 셀 목록, 셀은 임의 문자열·숫자·빈 셀 포함)", 숫자 셀은 `AMOUNT` 정규식이 잡는 형태(`80%`, `3만원`, `50회` 등)를 섞어 생성해 Property 2를 의미 있게 검증한다. 비표 텍스트는 기존 fixture 문장을 변형해 생성한다(Property 5).
- 파일 예: `tests/test_table_representation.py`(Property 1·2·3), `tests/test_display_table.py`(Property 4·5).

### 예제·통합 테스트 (PBT 비적합 영역)

- **`Table_Extractor`의 실제 PDF 동작**: base 약관의 특정 페이지를 작은 fixture로 두고, `extract_tables()`가 기대한 행 목록을 돌려주는지 1~3개 예제로 확인한다. 외부 라이브러리(pdfplumber) 동작 자체는 저자들이 테스트했으므로 대표 예제면 충분하다.
- **Req 2.4/2.5 구체 문구**: base 약관을 실제로 처리해 "3만원과 보장대상의료비의 30% 중 큰 금액", "비급여 병실료의 50%"를 (공백 무시) 부분 문자열로 포함하는 `Table_Representation`이 하나 이상 생기는지 확인하는 예제 테스트. 이는 임베딩 없이 텍스트 단계에서 검증 가능하다.

### 회귀 테스트 (Req 3)

- `split_articles`가 비표 조항 입력에 대해 변경 전과 동일한 조항 수·경계·`section`·`_split_long` 결과를 내는지 고정 입력으로 확인한다.
- 조각 메타데이터가 여전히 `{source, section, article_no, title}` 네 필드인지 확인한다.

### eval 하버스로 측정 (Req 5, 수용 증거)

- 임베딩/검색 랭킹/eval 적중은 **OpenAI 임베딩**에 의존해 오프라인 단위 테스트가 불가능하다. 따라서 PBT가 아니라 `eval/run_eval.py`의 전후 실행으로 검증한다(아래 Measurability).
- 오프라인에서 불가능한 부분(실 임베딩)은 eval 실행 시점에만 API로 수행하며, 단위 테스트에서는 표현(text) 생성까지만 검증한다.

## Measurability (전후 측정 절차)

Req 5의 수용 증거는 코드 추측이 아니라 다음 절차의 숫자다.

1. **Regression_Baseline 먼저 측정 (변경 전)**
   ```
   .venv/bin/python eval/run_eval.py --retrieval-only --split dev --note "Regression_Baseline (표 처리 전)"
   ```
   결과(`eval/results/..._dev_retrieval.json`)를 Regression_Baseline으로 보관한다. 여기서 검색 적중한 문항 집합을 기록한다(Req 5.4의 기준).

2. **구현 후 base 약관 재색인**
   표 조각을 포함해 `build_index`로 base 약관만 다시 색인한다(질의 시 추출 안 함, Req 6.3).

3. **재색인 후 재측정**
   ```
   .venv/bin/python eval/run_eval.py --retrieval-only --split dev --note "표 처리 후"
   ```
   - D13의 `evidence_contains` "3만원과 보장대상의료비의 30% 중 큰 금액"을 포함하는 조각이 검색 결과에 나오는가(Req 5.2).
   - D17의 "비급여 병실료의 50%"를 포함하는 조각이 나오는가(Req 5.3).
   - Regression_Baseline에서 적중했던 **모든 문항이 여전히 적중**하는가(Req 5.4, 회귀 없음).

4. **판정**: D13·D17이 적중으로 바뀌고 기존 적중 문항이 하나도 떨어지지 않으면 채택. 결과는 `eval/EXPERIMENTS.md`에 실험 기록으로 남긴다(기존 관행).

측정은 `eval/run_eval.py`를 **변경 없이** 사용한다(Req 5.1).

## Review and Approval

`requirements.md`는 이미 작성·승인되어 있다. 이 설계는 요구사항 1~6과 수용 기준을 모두 다룬다. 설계 검토 중 요구사항에 공백이 발견되면 요구사항 정리 단계로 돌아갈 수 있다.
