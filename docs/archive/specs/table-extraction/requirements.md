# Requirements Document

## Introduction

boheom-mate는 KB 실손의료보험 약관 PDF를 읽어 질문에 답하는 RAG 챗봇입니다. 현재 PDF 본문 추출은 `pypdf`의 `page.extract_text()`만 사용하며, 이 방식은 표(공제 한도표, 지급기준표, 자기부담금 표 등)의 행·열 구조를 잃어버리고 셀 안 숫자를 한 줄로 뭉쳐 버립니다. 그 결과 금액·비율·한도·횟수처럼 표 안에만 있는 사실을 묻는 질문에서 검색이 비슷한 다른 조각에게 밀리고, 답이 틀리거나 "확인 불가"로 나옵니다(예: 평가셋 D13 비급여 통원 자기부담금, D17 상급병실료 차액 "비급여 병실료의 50%"). 화면에서도 근거 원문이 한 줄로 뭉쳐 보입니다(common.md에 기록된 알려진 문제).

이 기능은 텍스트 추출이 가능한 약관 PDF에서 표를 행·열 구조를 보존하며 추출하고, 검색(벡터 + BM25 하이브리드)이 표 안 사실을 찾을 수 있는 형태로 표현하며, 화면에서 표 근거를 읽기 좋게 보여 주는 것을 목표로 합니다. 기존 조항 단위 청킹·구역 꼬리표·비표 텍스트 동작과 현재 통과 중인 평가 기준선은 그대로 유지합니다. 개선 여부는 기존 평가 하버스(`eval/run_eval.py`)로 전후 점수를 비교해 증명합니다.

## Glossary

- **Ingest_Pipeline**: 약관 PDF를 읽어 조항 단위로 자르고 임베딩해 Chroma에 저장하는 오프라인 처리 모듈(`boheom_mate/core/ingest.py`). 현재 `extract_text`, `split_articles`, `_split_long`, `build_index`를 포함한다.
- **Table_Extractor**: 이 기능에서 추가/확장되는, 텍스트 추출형 약관 PDF에서 표의 행·열 구조를 보존해 추출하는 구성요소.
- **Text_Extractor**: 현재 `Ingest_Pipeline`의 `extract_text` 동작. `pypdf`로 페이지 텍스트를 이어 붙이며 표 구조를 잃는다.
- **Table_Representation**: 추출된 표를 검색 가능한 텍스트로 바꾼 형태. 조항 청크와 함께 Chroma(벡터)와 BM25 색인에 들어간다.
- **Retrieval_Pipeline**: 질문을 분석해 벡터 검색과 BM25 검색을 RRF로 합쳐 상위 조각을 고르는 검색 모듈(`boheom_mate/core/rag.py`). TOP_K=8.
- **Display_Renderer**: 근거 원문을 화면에서 읽기 좋게 다듬는 모듈(`boheom_mate/ui/display.py`)의 `to_markdown`/`to_paragraphs` 동작.
- **Eval_Harness**: `eval/run_eval.py`. `eval/questions.json`의 `evidence_contains` 문구가 검색된 조각에 부분 문자열로 들어 있는지로 검색 적중을 채점하고, 결과를 `eval/results/`에 저장한다.
- **Baseline_Config**: 현재 채택된 검색 설정(실험 E4). `eval/EXPERIMENTS.md`에 기록돼 있다.
- **Table_Question**: 정답 근거가 표(또는 표에서 유래한 텍스트)에 있는 평가 문항. 최소 D13, D17을 포함한다.
- **Non_Table_Text**: 표가 아닌 일반 조항 본문.
- **Base_Policy**: 현재 DB의 기준 약관 파일 `KB_실손의료비보장보험_26.09_약관.pdf`.
- **Text_Extractable_PDF**: `pypdf`로 텍스트가 추출되는 PDF(스캔 이미지 PDF가 아님).
- **Regression_Baseline**: 이 기능 적용 직전에 `Eval_Harness`로 측정해 저장한 검색 점수 기준값.

## Requirements

### Requirement 1: 표 구조를 보존해 추출

**User Story:** 약관을 색인하는 개발자로서, 약관 PDF의 표를 행·열 구조를 잃지 않고 추출하고 싶다. 그래야 표 안의 금액·비율·한도가 한 줄로 뭉개지지 않는다.

#### Acceptance Criteria

1. WHEN `Text_Extractable_PDF`의 한 페이지에 표가 포함되어 있으면, THE `Table_Extractor` SHALL 해당 표를 행 목록으로 추출하고 각 행을 셀 목록으로 보존한다.
2. WHEN 한 페이지에 표가 여러 개 있으면, THE `Table_Extractor` SHALL 각 표를 개별 표로 분리해 추출한다.
3. IF 한 페이지에서 표가 하나도 검출되지 않으면, THEN THE `Table_Extractor` SHALL 빈 표 목록을 반환한다.
4. WHERE 입력 PDF가 스캔 이미지 PDF(텍스트가 추출되지 않는 PDF)이면, THE `Table_Extractor` SHALL 빈 표 목록을 반환한다.

### Requirement 2: 표를 검색 가능한 형태로 표현

**User Story:** 약관을 색인하는 개발자로서, 추출한 표를 검색이 찾을 수 있는 텍스트로 바꾸고 싶다. 그래야 "비급여 통원 자기부담금", "비급여 병실료의 50%" 같은 표 안 사실이 하이브리드 검색에 걸린다.

#### Acceptance Criteria

1. WHEN `Table_Extractor`가 표를 추출하면, THE `Ingest_Pipeline` SHALL 각 표를 셀 값을 포함하는 `Table_Representation` 텍스트로 변환한다.
2. THE `Ingest_Pipeline` SHALL `Table_Representation`을 해당 표가 속한 조항 구역의 `section` 꼬리표와 함께 Chroma(벡터) 색인과 BM25 색인에 모두 저장한다.
3. WHEN 표의 한 행에 항목명과 숫자 값(금액·비율·한도·횟수)이 함께 있으면, THE `Ingest_Pipeline` SHALL 그 항목명과 숫자 값을 같은 `Table_Representation` 단위 안에 유지한다.
4. THE `Ingest_Pipeline` SHALL `Base_Policy`의 비급여 통원 자기부담금 문구 "3만원과 보장대상의료비의 30% 중 큰 금액"을 공백을 무시하고 부분 문자열로 포함하는 `Table_Representation`을 하나 이상 생성한다.
5. THE `Ingest_Pipeline` SHALL `Base_Policy`의 상급병실료 차액 문구 "비급여 병실료의 50%"를 공백을 무시하고 부분 문자열로 포함하는 `Table_Representation`을 하나 이상 생성한다.

### Requirement 3: 비표 텍스트 동작 유지 (회귀 안전)

**User Story:** 개발자로서, 표 처리를 추가하더라도 기존 조항 청킹과 비표 본문 처리가 그대로이길 바란다. 그래야 지금 잘 되는 질문이 망가지지 않는다.

#### Acceptance Criteria

1. WHEN 표가 없는 조항 본문을 처리하면, THE `Ingest_Pipeline` SHALL `split_articles`의 조항 분할, `section` 구역 꼬리표 부여, `_split_long`의 CHUNK_CHARS=1500 재분할 동작을 변경 전과 동일하게 수행한다.
2. THE `Ingest_Pipeline` SHALL 조항 청크의 메타데이터 필드(`source`, `section`, `article_no`, `title`)를 변경 전과 동일한 이름과 의미로 유지한다.
3. THE `Retrieval_Pipeline` SHALL 변경 전과 동일한 `TOP_K` 개수의 조각을 반환한다.
4. IF 표 추출이 특정 페이지에서 실패하면, THEN THE `Ingest_Pipeline` SHALL 그 페이지의 비표 텍스트를 기존 `Text_Extractor` 경로로 계속 추출한다.

### Requirement 4: 표 근거를 화면에서 읽기 좋게 표시

**User Story:** 상담을 쓰는 사용자로서, 근거로 제시된 표가 한 줄로 뭉치지 않고 행 단위로 읽히길 바란다. 그래야 금액·비율 근거를 눈으로 확인할 수 있다.

#### Acceptance Criteria

1. WHEN 근거 조각이 `Table_Representation`에서 유래한 내용을 포함하면, THE `Display_Renderer` SHALL 그 표 내용을 둘 이상의 행으로 나눠 표시한다.
2. THE `Display_Renderer` SHALL 표 내용을 한 줄로 뭉쳐 표시하지 않는다.
3. WHEN 표 내용에 금액·비율·기간·횟수 숫자가 있으면, THE `Display_Renderer` SHALL 기존 숫자 강조(`AMOUNT` 규칙)를 그 숫자에 적용한다.
4. WHEN 근거 조각이 비표 본문이면, THE `Display_Renderer` SHALL 변경 전과 동일한 문단 정리 결과를 생성한다.

### Requirement 5: 평가 하버스로 개선 측정 (회귀 없음)

**User Story:** 개발자로서, 표 처리 개선이 실제로 효과가 있는지 숫자로 확인하고 싶다. 그래야 채택 여부를 근거로 판단한다.

#### Acceptance Criteria

1. THE `Eval_Harness` SHALL 변경 없이 `Base_Policy`에 대해 재색인 후의 검색 점수를 측정할 수 있다.
2. WHEN 재색인 후 `Eval_Harness`를 `--retrieval-only`로 실행하면, THE `Retrieval_Pipeline` SHALL 문항 D13의 `evidence_contains` 문구 "3만원과 보장대상의료비의 30% 중 큰 금액"을 포함하는 조각을 검색 결과에 반환한다.
3. WHEN 재색인 후 `Eval_Harness`를 `--retrieval-only`로 실행하면, THE `Retrieval_Pipeline` SHALL 문항 D17의 `evidence_contains` 문구 "비급여 병실료의 50%"를 포함하는 조각을 검색 결과에 반환한다.
4. WHEN 재색인 후 `Eval_Harness`를 `--retrieval-only --split dev`로 실행하면, THE `Retrieval_Pipeline` SHALL `Regression_Baseline`에서 검색 적중한 모든 문항에 대해 재색인 후에도 검색 적중을 유지한다.

### Requirement 6: 기존 스택·운영 제약 준수

**User Story:** 개발자로서, 표 처리 추가가 프로젝트의 기존 구조와 운영 방식을 깨지 않길 바란다. 그래야 유지보수가 단순하게 유지된다.

#### Acceptance Criteria

1. THE `Ingest_Pipeline` SHALL LangChain을 도입하지 않고 표 처리를 구현한다.
2. WHERE 표 추출을 위해 새 파이썬 의존성이 필요하면, THE `requirements.txt` SHALL 그 의존성을 정확한 버전으로 고정해 포함한다.
3. THE `Ingest_Pipeline` SHALL 표 색인을 기존 오프라인 일회성 색인 방식(`build_index` 호출)으로 수행하고, 질의 시점에 표를 추출하지 않는다.
4. THE `Retrieval_Pipeline` SHALL 금액 계산을 기존대로 `calculator.py` 코드로 수행하고, 금액 계산을 LLM에 위임하지 않는다.
5. WHEN 표 처리 코드를 검증하면, THE 테스트 SHALL 기존 테스트 방식(`.venv/bin/python tests/test_xxx.py` 실행, OK/실패 수 출력)을 따른다.

## Out of Scope (Non-Goals)

- 여러 보험사 표를 공통 스키마로 정규화하는 작업(다보험사 표 정규화).
- 스캔/이미지 PDF에 대한 OCR. 이 기능은 텍스트 추출형 PDF만 다룬다.
- PII 처리, 답변 생성 품질 개선, QA 에이전트 등 별도 진행 중인 작업.
