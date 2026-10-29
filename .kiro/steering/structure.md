# 구조 (Structure)

## 레이어드 패키지 `boheom_mate/`
역할별로 나뉜 레이어 구조를 유지한다. 새 코드는 역할에 맞는 레이어에 둔다.

```
boheom_mate/
├── app.py              # Streamlit 진입점. 로직은 두지 않고 각 레이어를 호출한다
├── ui/                 # 표현 계층: 화면·대화·표시 포맷팅
│   ├── chat.py         #   채팅 에이전트(도구 호출: 검색/계산/되묻기)
│   └── display.py      #   근거 원문을 화면에 읽기 좋게 다듬기
├── core/               # 도메인/핵심 로직 (외부 I/O 최소)
│   ├── rag.py          #   질문 분석 → 하이브리드 검색 → 근거 기반 답변
│   ├── ingest.py       #   PDF → 조항 분할 → 임베딩 → Chroma
│   ├── calculator.py   #   보험금 계산(약관 공식). 금액은 여기서만 계산
│   ├── keyword_search.py #  BM25 키워드 검색
│   └── glossary.py     #   일상어 → 약관 용어 사전
├── store/              # 데이터 접근: SQLite·JSON 상태
│   ├── question_store.py #  질문·대화·사용량 저장(questions.db)
│   ├── products.py     #   상품 목록
│   └── sync_state.py   #   주간 확인 상태(state/)
├── integrations/       # 외부 연동(인프라)
│   ├── slack_notify.py #   Slack 알림·승인 읽기
│   └── insurers/       #   보험사 공시실 모듈(자동=ENABLED, 수동=MANUAL)
└── jobs/               # 배치/백그라운드(systemd timer)
    ├── weekly_check.py #   주간 약관 확인
    ├── apply_approved.py # 승인된 약관 반영
    └── split_check.py  #   조항 분할 점검
```

## 레이어 규칙
- `ui`는 `core`/`store`를 호출하되, `core`는 `ui`를 import하지 않는다(의존 방향 단방향).
- 모든 import는 절대 패키지 경로를 쓴다: `from boheom_mate.core import rag`.
- 파일시스템 경로(data/·chroma_db/·state/·assets/)는 **프로젝트 루트** 기준으로 앵커를 잡는다
  (파일 위치가 바뀌어도 루트를 가리키도록 `parents[N]` 계산에 주의).

## 테스트·평가·배포 위치
- 테스트: 루트 `tests/` (pytest 없이 개별 실행).
- 평가: 루트 `eval/` (`run_eval.py`, `EXPERIMENTS.md`, `results/`).
- 배포: 루트 `deploy/` (systemd 서비스가 `-m boheom_mate.jobs.*`, `streamlit run boheom_mate/app.py` 실행).
