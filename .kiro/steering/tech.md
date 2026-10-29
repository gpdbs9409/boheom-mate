# 기술 스택 (Tech)

## 스택
- Python 3.12 (Homebrew `python3.12`, 가상환경 `.venv`). 시스템 `python3`(3.9)는 쓰지 않는다.
- Streamlit (UI), OpenAI (임베딩 `text-embedding-3-small` + 답변 `gpt-5.4-mini`),
  ChromaDB (벡터 저장), pypdf (PDF 텍스트), kiwipiepy (형태소 분석 + BM25), slack_sdk, requests.

## 의도적으로 하지 않는 것
- **LangChain을 쓰지 않는다.** "검색 → 프롬프트 → 답변" 흐름을 직접 이해하고 설명할 수 있어야 한다.
- 금액 계산을 LLM에 맡기지 않는다. `calculator.py`가 약관 공식으로 계산한다.
- 질의 시점에 PDF를 다시 파싱하지 않는다. 색인은 오프라인 일회성(`build_index`).

## 의존성 규칙
- 새 의존성은 `requirements.txt`에 **정확한 버전으로 고정**한다. 범위 버전(^, ~, >=) 금지.
- 새 라이브러리 도입 전 "기존 스택으로 안 되는가"를 먼저 따진다.

## 테스트
- **pytest를 쓰지 않는다.** 각 테스트는 `.venv/bin/python tests/test_xxx.py`로 직접 실행하고,
  성공 시 `OK` 또는 `NN/NN 통과`를 출력한다.
- 속성 기반 테스트는 Hypothesis를 쓰고, 최소 100회 반복, 각 테스트에 검증하는 속성을 주석으로 단다.

## RAG 변경 규칙
- 검색/프롬프트/청킹을 바꾸면 반드시 `eval/run_eval.py`로 **전후 점수를 비교**한다.
- 실험은 `eval/EXPERIMENTS.md`에 가설·바꾼 것·결과 파일·판정으로 기록한다.
- 평가셋 정답 숫자를 용어사전(`glossary.py`)에 넣지 않는다(점수 부풀림 방지).

## 실행
```
.venv/bin/streamlit run boheom_mate/app.py          # 앱
.venv/bin/python eval/run_eval.py                   # 평가 dev
.venv/bin/python -m boheom_mate.jobs.weekly_check   # 주간 약관 확인
```
