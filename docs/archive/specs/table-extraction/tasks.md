# Implementation Plan: 표 처리 개선 (table-extraction)

## Overview

설계(`design.md`)의 흐름을 작은 단위로 구현한다: (1) 코드 변경 없는 진단/기준선 측정 → (2) pdfplumber 의존성 + `extract_tables` → (3) `table_representations` + 변환 PBT + D13/D17 구체 문구 → (4) ingest 병합 배선 + 회귀 테스트 → (5) display 표 분기 + 렌더 PBT → (6) base 약관 재색인 + 전후 eval 측정(수용 증거) → (7) EXPERIMENTS.md 기록.

테스트는 pytest 없이 `.venv/bin/python tests/test_xxx.py`로 실행하며 성공 시 `OK` 또는 `NN/NN 통과`를 출력한다. 속성 기반 테스트는 Hypothesis(`@settings(max_examples=100)`)를 쓰고 각 테스트에 `# Feature: table-extraction, Property N: ...` 주석을 단다.

표현(text) 생성·렌더·분할까지는 오프라인에서 검증한다. 임베딩/검색 랭킹/eval 적중은 OpenAI 임베딩이 필요하므로 **오프라인 검증 불가**로 표시하고 Measurability 절차(태스크 6)로 묶는다.

## Tasks

- [ ] 1. 진단 및 Regression_Baseline 측정 (코드 변경 없음, OpenAI 임베딩 필요)
  - `.venv/bin/python eval/run_eval.py --retrieval-only --split dev --note "Regression_Baseline (표 처리 전)"`를 돌려 `eval/results/..._dev_retrieval.json`을 Regression_Baseline으로 보관하고, 검색 적중한 문항 집합을 기록한다(태스크 6의 회귀 기준).
  - D13("3만원과 보장대상의료비의 30% 중 큰 금액")·D17("비급여 병실료의 50%")을 텍스트 손실 vs 랭킹으로 분류한다: eval 디버그 출력의 `retrieved`를 보고, 일회성 스크립트로 `rag._collection().get(where={"source": ...})`의 documents에서 `norm()` 부분 문자열로 정답 문구가 DB에 존재하는지 직접 확인한다.
  - base 약관 정답 페이지(예: 13쪽)에 대해 `pypdf`의 `page.extract_text()`와 pdfplumber의 `page.extract_tables()`를 나란히 비교해 괘선형 숫자 표 vs 텍스트 목록형 표를 가른다.
  - 진단 결과(텍스트 손실/랭킹 분류 + 비교)와 Regression_Baseline 파일 경로를 메모로 남긴다. 이후 태스크가 이 분류에 의존한다.
  - 오프라인 검증 불가 항목(eval 실행)은 Measurability 절차에 해당한다.
  - _Requirements: 5.1, 5.4_

- [ ] 2. pdfplumber 의존성 추가와 `extract_tables()` 구현
  - [ ] 2.1 pdfplumber 설치 후 `requirements.txt`에 정확한 버전으로 고정
    - `.venv/bin/pip install pdfplumber` 후 실제 해석된 버전을 확인해 `requirements.txt`에 `pdfplumber==<확인된 버전>`로 핀을 맞춘다(범위 버전 금지). LangChain은 도입하지 않는다.
    - _Requirements: 6.1, 6.2_

  - [ ] 2.2 `boheom_mate/core/ingest.py`에 `extract_tables(pdf_path)` 추가
    - 페이지별 `page.extract_tables()`로 표를 뽑아 `[{"page": int, "table_index": int, "rows": [[cell, ...], ...]}, ...]`를 반환한다. 셀의 `None`은 빈 문자열로 정규화하고 각 셀을 `strip()`한다. 행·열 구조는 `rows`로 보존한다.
    - 표 없는 페이지는 결과에 포함하지 않고, 텍스트 미추출(스캔) PDF면 전체 빈 목록을 반환한다. 특정 페이지에서 예외가 나면 그 페이지만 건너뛰고 계속한다(비표 텍스트는 변경 없는 `extract_text` 경로가 담당).
    - `extract_text()`는 건드리지 않는다.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 3.4_

  - [ ]* 2.3 `extract_tables()` 예제/통합 테스트
    - `tests/test_extract_tables.py`: base 약관의 작은 페이지 fixture에 대해 `extract_tables()`가 기대한 행 목록을 돌려주는지 1~3개 예제로 확인하고, 표 없는 입력에 빈 목록을 반환하는지 확인한다.
    - 실행: `.venv/bin/python tests/test_extract_tables.py` (성공 시 `NN/NN 통과`).
    - _Requirements: 1.1, 1.2, 1.3_

- [ ] 3. `table_representations()` 구현과 변환 검증
  - [ ] 3.1 `boheom_mate/core/ingest.py`에 `table_representations(tables, page_sections)` 추가
    - 각 표를 article dict 모양 `{"section", "article_no", "title", "text", "is_table": True}`의 조각으로 변환한다. 한 행 = 한 줄로 만들어 행 구조를 보존하고, 한 행의 셀들을 구분자(예: ` | `)로 이어 붙여 항목명과 숫자 값을 같은 줄에 둔다. 조각 맨 앞에 `[표] {title}` 머리말을 둔다.
    - `section`은 `find_sections`/`section_at`으로 판정하고, `article_no`/`title`은 가장 가까운 선행 조항에 맞춰 `REQUIRED_ARTICLES` 경로에 들어오게 한다(특정 불가 시 `article_no="표"`, `title=캡션`). `CHUNK_CHARS=1500` 초과 시 기존 `_split_long`이 행 경계에서 재분할하도록 둔다(`_split_long`은 변경하지 않음).
    - _Requirements: 2.1, 2.2, 2.3_

  - [ ]* 3.2 Property 1 PBT — 셀 값 보존
    - `tests/test_table_representation.py`에 `# Feature: table-extraction, Property 1: 표 변환은 모든 셀 값을 보존한다` 태그. 무작위 비어있지 않은 표를 생성해 변환 결과 텍스트들의 합집합이 모든 비어있지 않은 셀을 (공백 정규화 후) 부분 문자열로 포함하는지 `@settings(max_examples=100)`으로 검증한다.
    - **Property 1: 표 변환은 모든 셀 값을 보존한다**
    - **Validates: Requirements 1.1, 2.1**

  - [ ]* 3.3 Property 2 PBT — 항목명+숫자 같은 줄 유지
    - 같은 `tests/test_table_representation.py`에 `# Feature: table-extraction, Property 2: ...` 태그. "항목명 셀 + `AMOUNT`가 잡는 숫자 셀(`80%`, `3만원`, `50회` 등)"로 된 행을 생성해 항목명과 숫자가 같은 조각의 같은 줄에 남는지(재분할 후에도 행 경계 유지) 검증한다.
    - **Property 2: 항목명과 숫자는 같은 조각 단위에 남는다**
    - **Validates: Requirements 2.3**

  - [ ]* 3.4 Property 3 PBT — 빈 입력 → 빈 결과
    - 같은 파일에 `# Feature: table-extraction, Property 3: ...` 태그. 표가 하나도 없는 입력(빈 표 목록, 스캔 PDF 포함)에 대해 `table_representations`가 빈 조각 목록을 반환하는지 검증한다.
    - **Property 3: 표가 없으면 빈 결과**
    - **Validates: Requirements 1.3, 1.4**

  - [ ]* 3.5 D13/D17 구체 문구 예제 테스트 (텍스트 단계, 임베딩 없음)
    - `tests/test_table_phrases.py`: base 약관을 실제로 `extract_tables → table_representations`로 처리해 "3만원과 보장대상의료비의 30% 중 큰 금액"과 "비급여 병실료의 50%"를 공백 무시 부분 문자열로 포함하는 `Table_Representation`이 각각 하나 이상 생기는지 확인한다(임베딩 불필요).
    - 실행: `.venv/bin/python tests/test_table_phrases.py`.
    - _Requirements: 2.4, 2.5_

- [ ] 4. ingest 파이프라인 배선과 회귀 안전
  - [ ] 4.1 `main()`/`build_index` 경로에서 조항 청크와 표 조각 병합
    - PDF마다 `extract_text()` 경로(조항 청크)와 `extract_tables()`→`table_representations()` 경로(표 조각)를 각각 만들어 하나의 `articles` 리스트로 병합해 `build_index`에 넘긴다. 색인 직전 내부 `is_table` 플래그를 떨어뜨려 Chroma 메타데이터는 `{source, section, article_no, title}` 네 필드만 유지한다. `extract_text()`와 base 약관만 재색인하는 직접 `build_index` 호출 방식은 그대로 둔다(질의 시 추출 없음).
    - _Requirements: 2.2, 3.2, 6.3_

  - [ ]* 4.2 split_articles / 메타데이터 회귀 테스트
    - `tests/test_ingest_regression.py`: 고정된 비표 조항 입력에 대해 `split_articles`의 조항 수·경계·`section`·`_split_long` 결과가 변경 전과 동일한지, 조각 메타데이터가 여전히 네 필드인지 확인한다.
    - 실행: `.venv/bin/python tests/test_ingest_regression.py`.
    - _Requirements: 3.1, 3.2_

- [ ] 5. display 표 분기 추가와 렌더 검증
  - [ ] 5.1 `boheom_mate/ui/display.py`의 `to_paragraphs`/`to_markdown`에 표 분기 추가
    - 표 신호(머리말 `[표]` 또는 행 구분자 `|`)가 보이면 저장된 `text`를 행 단위로 분리해 둘 이상의 블록으로 렌더하고, 각 행에 기존 `AMOUNT`/`KEY_PHRASE` 강조를 그대로 재사용한다. 비표 경로(`NEW_ITEM`/`SPACED_LABEL`/줄 잇기)는 변경하지 않는다.
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 5.2 Property 4 PBT — 표 렌더는 행 단위로 분리
    - `tests/test_display_table.py`에 `# Feature: table-extraction, Property 4: ...` 태그. 둘 이상의 행을 가진 `Table_Representation` 조각에 대해 `to_paragraphs`/`to_markdown` 출력이 둘 이상의 블록으로 나뉘고 서로 다른 행이 한 블록으로 합쳐지지 않는지 검증한다.
    - **Property 4: 표 조각 렌더링은 한 줄로 뭉치지 않고 행 단위로 나뉜다**
    - **Validates: Requirements 4.1, 4.2**

  - [ ]* 5.3 Property 5 PBT — 비표 렌더 변경 없음
    - 같은 `tests/test_display_table.py`에 `# Feature: table-extraction, Property 5: ...` 태그. 비표 본문 텍스트에 대해 표 분기가 추가된 출력이 변경 전 구현의 출력과 동일한지 검증한다(변경 전 함수 스냅샷 또는 복제본과 비교).
    - **Property 5: 비표 텍스트 렌더링은 변경 전과 동일하다**
    - **Validates: Requirements 3.1, 4.4**

- [ ] 6. 체크포인트 — 오프라인 테스트 전부 통과 확인
  - 태스크 2~5의 모든 테스트(`.venv/bin/python tests/test_*.py`)가 통과하는지 확인한다. 문제가 있으면 사용자에게 질문한다.

- [ ] 7. base 약관 재색인과 전후 eval 측정 (수용 증거, OpenAI 임베딩 필요 — 오프라인 검증 불가)
  - 표 조각을 포함해 `build_index`로 base 약관만 다시 색인한다(질의 시 추출 없음).
  - `.venv/bin/python eval/run_eval.py --retrieval-only --split dev --note "표 처리 후"`를 돌려 재측정한다.
  - 수용 판정(Measurability): D13 "3만원과 보장대상의료비의 30% 중 큰 금액"과 D17 "비급여 병실료의 50%"를 포함하는 조각이 검색 결과에 나오고(Req 5.2/5.3), 태스크 1의 Regression_Baseline에서 적중했던 모든 문항이 재색인 후에도 적중을 유지하는지(Req 5.4) 확인한다. `run_eval.py`는 변경 없이 사용한다.
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [ ] 8. eval/EXPERIMENTS.md에 실험 기록
  - 가설·바꾼 것(표 조각 색인)·결과 파일 경로(Regression_Baseline, 표 처리 후)·전후 적중 비교·채택 판정을 `eval/EXPERIMENTS.md`에 추가한다. 평가셋 정답 숫자는 용어사전에 넣지 않는다.
  - _Requirements: 5.4_

## Notes

- `*` 표시 서브태스크(단위/속성/통합 테스트)는 선택이며 빠른 MVP에서 건너뛸 수 있다. 상위 태스크와 핵심 구현 태스크에는 `*`를 붙이지 않는다.
- 태스크 1과 7은 OpenAI 임베딩/실제 eval 실행이 필요해 오프라인 단위 테스트로 검증할 수 없고, Measurability 절차의 수용 증거에 해당한다.
- 표현(text)·렌더·분할 로직은 태스크 2~5에서 오프라인으로 검증한다.
- 각 코딩 태스크는 참조 요구사항/속성을 명시하고, 테스트는 pytest 없이 `.venv/bin/python tests/test_xxx.py`로 실행한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1"] },
    { "id": 1, "tasks": ["2.2"] },
    { "id": 2, "tasks": ["2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5", "5.1"] },
    { "id": 4, "tasks": ["4.1", "5.2", "5.3"] },
    { "id": 5, "tasks": ["4.2"] },
    { "id": 6, "tasks": ["7"] },
    { "id": 7, "tasks": ["8"] }
  ]
}
```
