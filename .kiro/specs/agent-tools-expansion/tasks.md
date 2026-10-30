# Implementation Plan: agent-tools-expansion

## Overview

design.md의 "구현 순서 제안"(단계 0~6, 실험 E12~E18)을 따라 Python 3.12(`.venv`)로 구현한다. 각 단계는 작은 작업으로 나누고, 단계 끝 체크포인트에서 전체 테스트와 평가를 돌려 `eval/EXPERIMENTS.md`에 기록한다.

공통 규칙:
- 테스트는 pytest 없이 `.venv/bin/python tests/test_x.py`로 실행하고, 성공하면 `OK` 또는 `NN/NN 통과`를 출력한다.
- 속성 테스트는 Hypothesis(`hypothesis==6.170.13`)로 쓰고 `@settings(max_examples=100, deadline=None)` 이상으로 돌린다. 테스트 함수 위에 `# Feature: agent-tools-expansion, Property N: <제목> — 검증하는 속성: <한 줄 설명>` 주석을 단다.
- 전체 테스트 실행: `for f in tests/test_*.py; do .venv/bin/python "$f" || echo "FAIL $f"; done` (`smoke_*.py`는 빠진다).
- 체크포인트 합격선: RAG_Eval 거절 정확도 100%, 검색 적중 94% 이상, Chat_Eval 기존 11턴 11/11 통과, 기존 11턴의 Tool_Selection_Error 수가 E12 기준선보다 늘지 않음. 하나라도 어기면 다음 단계로 가지 않고 멈춘 뒤 사용자에게 묻는다.
- 외부 조건이 걸린 작업은 작업 안에 "실행 조건"을 적었다. 조건이 없으면 목 테스트만 하고 해당 단계는 "건너뜀"으로 기록한다.
- EC2 서버 반영(`deploy/push.sh`, 서버 DB·색인 복사)은 이 스펙에서 하지 않는다.

## Tasks

- [ ] 1. 단계 0: 기준선 측정과 테스트 의존성 (E12)
  - [ ] 1.1 Hypothesis 버전 고정과 설치
    - `requirements.txt`에 `hypothesis==6.170.13` 한 줄을 정확한 버전으로 추가한다(범위 버전 금지)
    - `.venv/bin/pip install hypothesis==6.170.13`로 설치하고 `.venv/bin/python -c "import hypothesis; print(hypothesis.__version__)"`가 `6.170.13`을 출력하는지 확인한다
    - _Requirements: 12.9, 12.10_
  - [ ] 1.2 코드 변경 없이 현재 기준선 저장
    - 저장소 코드(`boheom_mate/`, `eval/`, `tests/`)는 바꾸지 않는다
    - 전체 테스트를 실행해 결과를 기록한다
    - `.venv/bin/python eval/run_eval.py`와 `.venv/bin/python eval/run_chat.py --note "E12 기준선"`를 실행해 `eval/results/`에 생긴 결과 파일 이름을 적어 둔다
    - 기본형 Rule_Set 파일(`state/rules/` 아래 25478 파일)을 `state/rules_backup_e12/`로 복사해 둔다(v1 캐시 업그레이드 후 22/22 값 비교용, `state/`는 git 제외)
    - _Requirements: 12.4, 12.5, 1.6_

- [ ] 2. 체크포인트: 단계 0 기록
  - `eval/EXPERIMENTS.md`에 E12(기준선)를 가설·바꾼 것(코드 없음, Hypothesis 설치)·결과 파일·판정 형식으로 기록한다: 거절 정확도, 검색 적중, 기존 11턴 통과 수, 평균 응답 시간
  - 거절 정확도 100%·검색 94% 이상·11/11이 아니면 이후 단계의 비교 기준이 흔들리므로 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. 단계 1: Article_Locator, Rule_Set v2, 간편가입 계산 (E13)
  - [ ] 3.1 `eval/run_chat.py` 채점 확장
    - `tools_called(result)`: `result["tools"]`가 있으면 그것을, 없으면 `calculation`→`calculate_payout`, `searches`→`search_terms`로 만든다
    - 새 expect 키 `tool`, `no_tool`, `answer_includes`, `evidence_title`, 턴 키 `requires`·`expect_fallback`(생략 시 `{"ask": true}`), 시나리오 키 `source`·`memory`·`group`을 채점에 반영한다. expect의 `source`가 있으면 그 Product로 `chat.reply`를 부른다
    - 기존 문항의 암묵 기대(`calc` 숫자 → `tool: calculate_payout`, `calc: null` → `no_tool: [calculate_payout]`, `search: true` → `tool: search_terms`)로 Tool_Selection_Error를 턴 단위로 센다
    - 요약에 `tool_selection_errors`, `legacy_turn_pass`, `legacy_tool_selection_errors`, `mode: {"hira_key", "kcd_index"}`(bool만), `prompt_chars`를 넣고, 행에 `tools`·`source`를 더한다
    - `--rescore <결과 파일>` 옵션으로 저장된 E12 결과의 `searches`·`calculation`에서 legacy Tool_Selection_Error를 다시 세어 출력한다(E12 기준선 오류 수 확정용)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.7_
  - [ ]* 3.2 `tests/test_run_chat.py` 작성
    - 가짜 `result` dict로 `tools_called` 도출, `tool`/`no_tool`/`answer_includes`/`evidence_title` 채점, 턴당 오류 1회 집계, `expect_fallback` 선택을 확인한다
    - _Requirements: 12.1, 12.3, 12.7_
  - [ ] 3.3 `boheom_mate/core/articles.py` 작성
    - 제목 상수(`COVERAGE`, `EXCLUSION`, `CLAIM`, `LIMITATION`, `DEFINITION`)와 `locate(source, title)`(제목 완전 일치, `보통약관`·`특별약관` 구역만, DEFINITION만 `별표: 용어의 정의` 포함, `(source, doc_count, title)` 키 캐시)
    - `inventory(source)`, `rank(question, chunks, per_section)`(kiwi 토큰 + `glossary.lookup` 용어 + `BM25Okapi`, 임베딩 호출 없음)
    - `policy_rules._articles_by_group`은 바꾸지 않는다
    - _Requirements: 4.1, 4.2, 6.1, 6.2, 7.1_
  - [ ]* 3.4 `tests/test_article_locator.py` 통합 테스트(로컬 Chroma)
    - 기본형·간편가입의 CLAIM(제7조/제6조)·LIMITATION(제46조/제45조)·DEFINITION·EXCLUSION 위치가 design.md 표와 같은지, "지정대리청구인에 의한 보험금의 청구"와 제도성 특약이 빠지는지 확인한다
    - _Requirements: 4.2, 6.1, 6.2, 7.1_
  - [ ] 3.5 `boheom_mate/core/kcd.py` 작성
    - `parse_code`(공백·소문자 정리, "N393" → "N39.3"), `in_range`(알파벳 같음, 앞 두 자리 정수 비교, 소수점 끝점은 같은 자릿수로 비교), `match`(`kind_summary`: excluded/exception/excluded_with_exception)
    - `extract_ranges(chunk)`(`~`·`∼`·`～` 범위와 단일 코드, 범위 끝점 중복 계산 없음), `classify`(항목 경계, "다만 … 보상합니다"/괄호 안 "보상합니다" → exception, 조건 문구 표지 3종)
    - 코드 범위 값은 소스에 하드코딩하지 않는다
    - _Requirements: 10.3, 10.4, 10.9, 10.11_
  - [ ] 3.6 Property 9 속성 테스트: 질병코드 범위 포함 판정 (모델 기반)
    - **Property 9: 질병코드 범위 포함 판정 (모델 기반)**
    - **Validates: Requirements 10.9**
    - `tests/test_kcd_ranges.py`에 참조 모델과 `kcd.in_range` 결과 비교를 Hypothesis 100회 이상으로 작성한다. 생성기는 `~`/`∼` 표기와 점 없는 표기("N393")를 섞어 `parse_code`를 함께 거친다
  - [ ] 3.7 `tests/test_kcd_ranges.py` 분류 예제 테스트
    - 확인한 사실 기준 고정: F04∼F09·F51·Q82.5·G47.3 → exception, O00~O99·E66·N39.3 → excluded, K00∼K08 조건 "비급여 의료비만 면책", K60~K62 조건 "요양급여에 해당하지 않는 부분만 면책", 인용 검증 실패 범위 제외, F32의 `kind_summary`가 excluded_with_exception
    - 예외를 면책으로 잘못 분류하면 오안내가 되므로 필수 작업으로 둔다
    - _Requirements: 10.3, 10.11_
  - [ ] 3.8 `policy_rules.py` 프로필 레지스트리와 구조 기반 선택
    - `PROFILE_BASE`/`PROFILE_SIMPLE`, `SIMPLE_FIELDS`(11개, 숫자 상수 없음), `SIMPLE_ANCHORS` 병합, `ALL_FIELDS`, `PROFILES`(간편가입 `must_exceed`: 입원 정액 > 통원 정액)
    - `verify` 시그니처는 그대로 두고 내부 조회만 `ALL_FIELDS`로 바꾼다. 추출 3회 재시도 루프를 `_extract_values(profile, groups)`로 뽑는다
    - `select_profile(groups)`(`SIMPLE_STRUCTURE` 정규식, 상품코드·파일명 분기 없음)와 `profile_basis` 저장
    - 기존 `tests/test_policy_rules.py`가 변경 없이 통과하는지 확인한다
    - _Requirements: 1.1, 1.2, 1.5, 1.7_
  - [ ] 3.9 `policy_rules.py` Rule_Set v2와 부가 정보
    - `SIMPLE_EXCLUSION_ITEMS`와 `verify_exclusion_item`(원문 일치, 필수·선택 말, 앞뒤 `ANCHOR_WINDOW` 안 "보상하지"), 하나라도 실패하면 `ok=false`와 `failed["exclusion_item:<키>"]`
    - `extract_limitation`(`LIMITATION_PATTERN`, 인용문 검증, `basis_quote`, 3년 숫자 하드코딩 없음)
    - `kcd.extract_ranges`로 모든 구역 Exclusion_Article의 `code_ranges` 저장, 검증 실패는 `failed["code_range:…"]`
    - `FORMAT_VERSION = 2`, `load_rule_set`(design.md 흐름도), 기본형 v1 캐시는 `values`·`quotes`·`failed`를 그대로 두고 LLM 없이 새 칸만 채워 업그레이드, 간편가입 v1은 재추출, `load`는 기존 계약 유지
    - `evidence(rules, used_keys)`가 `ALL_FIELDS`와 `exclusion:<키>`를 처리한다
    - _Requirements: 1.3, 1.4, 1.6, 1.8, 6.3, 10.3, 10.11_
  - [ ] 3.10 `tests/test_policy_rules_profiles.py` 작성
    - `select_profile` 조합(기본형·간편가입·판별 불가), `rag._ask_json` 목으로 간편가입 추출 ok/실패, 면책 3항목 각각 누락 시 `ok=false`
    - `state/rules_backup_e12/`의 v1 기본형 파일을 임시 폴더에 복사해 `load_rule_set` 후 `values`가 이전과 같고 22개 모두 `KB_BASE_RULES`와 같음을 확인한다
    - 로컬 Chroma 통합: 간편가입 `limitation.years == 3`, `code_ranges`에 요구사항의 기대 코드 포함
    - 기본형 22/22 유지는 회귀 기준이므로 필수 작업으로 둔다
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.8, 6.3, 10.3_
  - [ ] 3.11 `calculator.py`에 `calculate_simple` 추가
    - `KB_SIMPLE_RULES`(테스트 전용), `DISCLAIMER`, `calculate_simple`(rules 필수, 없으면 `ValueError`), 입원·통원·상급병실료·연간 공제 상한 초과분·미적용 40%·방문 한도·면책 3항목 0원 항목·병원 종류 notes·`rules_used`
    - `_common_notes` 추출은 기존 `calculate` 출력 문자열을 바꾸지 않는 범위에서만 한다. `calculate`, `KB_BASE_RULES`, `_Rules`, `_item`은 그대로 둔다
    - 기존 `tests/test_calculator.py`가 변경 없이 통과하는지 확인한다
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.11, 2.12, 11.2_
  - [ ] 3.12 Property 1 속성 테스트: 간편가입 보상액 상한·하한
    - **Property 1: 간편가입 보상액 상한·하한**
    - **Validates: Requirements 2.9**
    - `tests/test_simple_calculator.py`에 design.md 생성기 범위로 `total`이 0 이상·낸 금액 합계 이하, 각 항목 `amount` 0 이상을 Hypothesis 100회 이상 확인한다
  - [ ] 3.13 Property 2 속성 테스트: 낸 금액에 대한 단조성
    - **Property 2: 낸 금액에 대한 단조성**
    - **Validates: Requirements 2.10**
    - `tests/test_simple_calculator.py`에 `covered_copay`·`non_covered`·`room_fee` 중 하나만 늘린 입력에서 `total`이 줄지 않음을 확인한다
  - [ ] 3.14 Property 3 속성 테스트: 면책 3항목 금액의 무관성
    - **Property 3: 면책 3항목 금액의 무관성**
    - **Validates: Requirements 2.13**
    - `tests/test_simple_calculator.py`에 `manual_therapy`·`injection`·`mri`만 바꾼 두 입력의 `total`이 같음을 확인한다
  - [ ] 3.15 Property 7 속성 테스트: 누적 공제액에 대한 단조성
    - **Property 7: 누적 공제액에 대한 단조성**
    - **Validates: Requirements 8.9**
    - `tests/test_simple_calculator.py`에 입원 계산에서 `prior_deductible`만 늘린 입력(None은 0)의 `total`이 줄지 않음을 확인한다
  - [ ] 3.16 `tests/test_simple_calculator.py` 손계산 예제 테스트
    - C17(40,000원), C18 ①(1,050,000원)·②(1,400,000원), 상급병실료 min 계산, `room_days ≤ 0` 오류, 미적용 40%, 방문 한도 초과 notes, 병원 종류 notes, 누적 공제액 없음 notes(200만원 표시), 면책 0원 항목과 안내 문구, rules 없으면 `ValueError`, `rules_used` 키
    - 금액 계산의 기준값이므로 필수 작업으로 둔다
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.12, 11.2_
  - [ ] 3.17 `ui/chat.py`에 간편가입 계산 연결
    - 프로필이 간편가입이면 `CALCULATE_SIMPLE_TOOL` 스키마를 bind하고 `_run_calculate`가 `calculate_simple`(rules, articles, exclusion_quotes는 Rule_Set v2에서)을 부른다. 기본형 경로는 그대로
    - `product_note`(기본형 기존 문장, 간편가입 문장, 프로필 None이면 없음)를 넣고, 근거에 `policy_rules.evidence`로 면책 3항목 인용을 포함한다
    - _Requirements: 2.1, 2.2, 2.12, 3.2, 3.5_
  - [ ] 3.18 `eval/conversations.json`에 C17~C19 추가
    - `source`를 간편가입 파일로 두고, 기대 총액과 손계산 근거를 `name`·`note`에 적는다(C17 40000, C18 1050000·1400000, C19 calc 0 + not_covered)
    - _Requirements: 12.1, 12.2, 12.6_

- [ ] 4. 체크포인트: 단계 1 평가 (E13)
  - 전체 테스트를 실행한다(`test_calculator.py`, `test_policy_rules.py` 무변경 통과 포함)
  - `run_eval.py`와 `run_chat.py`를 실행하고 E12와 비교한다: 거절 정확도 100%, 검색 94% 이상, 기존 11턴 11/11, legacy Tool_Selection_Error가 E12(3.1의 `--rescore` 결과)보다 늘지 않음, C17~C19 결과
  - `eval/EXPERIMENTS.md`에 E13(간편가입 계산)을 기록한다
  - 합격선을 어기면 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. 단계 2: 도구 레지스트리·디스패처로 구조 변경 (E14)
  - [ ] 5.1 `boheom_mate/ui/tools.py` 작성과 기존 2개 도구 이전
    - `Tool`, `Ctx`(`add_evidence`는 `(section, article_no, title, text)` 기준 중복 제거), `REGISTRY`, `bound_tools(ctx)`, `dispatch(name, args, bound, ctx)`
    - `dispatch` 규칙: 없는 도구 거절(`{"error": "없는 도구: <이름>"}`), `turn["tools"]`에 호출 순서대로 추가, `ValueError`·`TypeError`(이후 `HiraError`·`sqlite3.Error`) 포착, `disclaimer=True` 도구는 오류 결과에도 Disclaimer 부착, 근거 원문 대신 라벨·짧은 인용만 LLM에 반환
    - `search_terms`(항상, Disclaimer 없음)와 `calculate_payout`(`rule_set.ok`)을 chat.py에서 옮긴다
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 11.1_
  - [ ] 5.2 `ui/chat.py` 프롬프트 조립과 반환값 변경
    - `_system_prompt(ctx, bound)` = HEADER + bound 도구 규칙 + `product_note` + SCOPE + PLAIN_RULES + 메모. 기본형에서 bound가 2개면 지금 `BASE_PROMPT`와 같은 내용이 되게 한다
    - `reply` 반환값에 `tools`, `memory_updates`를 더하고, `bound_tool_names(source)`를 추가한다. `CALCULATOR_SOURCE`, `calculator_available`, `PLAIN_LANGUAGE`, `reply`의 이름·인자는 유지한다
    - _Requirements: 3.1, 3.6, 3.8_
  - [ ] 5.3 `tests/test_tools_dispatch.py` 작성(가짜 Ctx, LLM 없음)
    - 조건 조합별 Bound_Tools, 없는 도구 거절, 오류 포착 후 계속, `tools` 순서, 근거 중복 제거, Disclaimer 부착(오류 포함), 프롬프트에 bound 도구 규칙만 있음, 기본형 프롬프트 내용이 이전과 같음
    - 구조 변경 회귀를 잡는 테스트이므로 필수 작업으로 둔다
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [ ] 6. 체크포인트: 단계 2 평가 (E14)
  - 전체 테스트를 실행한다
  - `run_eval.py`와 `run_chat.py`를 실행해 기존 11턴과 legacy Tool_Selection_Error가 E12와 같거나 나은지, C17~C19가 E13과 같은지, `prompt_chars` 변화를 확인한다
  - `eval/EXPERIMENTS.md`에 E14(구조 변경 회귀)를 기록한다
  - 합격선을 어기면 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. 단계 3: 로컬 도구 4개 (E15)
  - [ ] 7.1 `boheom_mate/core/claim.py` 작성
    - `reference_deadline(treatment_date, years)`(2월 29일 → 평년이면 2월 28일), `parse_date("YYYY-MM-DD")`(형식·달력 오류는 `ValueError`), `DEADLINE_NOTE` 상수
    - _Requirements: 6.4, 6.6_
  - [ ] 7.2 Property 5 속성 테스트: 참고 기한 불변식
    - **Property 5: 참고 기한 불변식**
    - **Validates: Requirements 6.7**
    - `tests/test_claim_deadline.py`에 1990-01-01~2100-12-31 날짜와 1~10년에서 유효한 날짜·치료일보다 늦음·연도 차이 n·월 같음·일 같음(2월 29일 평년만 28)을 Hypothesis 100회 이상 확인한다
  - [ ]* 7.3 `tests/test_claim_deadline.py` 예제 테스트
    - 2월 29일, 날짜 형식 오류, 소멸시효 문장 추출 fixture(통과·숫자 없음·"소멸시효" 없음)
    - _Requirements: 6.3, 6.4, 6.5_
  - [ ] 7.4 `ui/tools.py`에 `check_exclusions` 추가
    - 조건: inventory에 COVERAGE와 EXCLUSION이 모두 있음. 실행: `articles.rank`로 보장(구역당 2)·면책(모든 구역, 구역당 3)을 따로 고르고 `rag.answer(q, source, articles=...)`로 답한다
    - Tool_Result `found`, `conclusive`, `answer`(found=false면 `rag.NOT_FOUND_MESSAGE`), `coverage`, `exclusions`, `cited`. 근거는 `rag.answer`가 검증한 조각만
    - 프롬프트 규칙: 금액 질문에 쓰지 않음, conclusive=false면 단정 금지, found=false면 확인 불가 안내
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 11.1_
  - [ ] 7.5 `ui/tools.py`에 `identify_generation` 추가
    - `memory.generation_of` 결과, None이면 `need`("joined_year"/"joined_month"), 1~3세대 경고 문구, `latest_rules`(현재 상품 Verified_Value와 인용문만, 1~3세대 숫자·외부 자료 없음), `turn["memory_updates"]`에 가입 연월·세대
    - `[금액 계산]` 세대 경계 안내 문장을 identify_generation 사용 안내로 바꾼다(bound일 때만)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 11.1_
  - [ ] 7.6 Property 4 속성 테스트: 세대 판정은 `generation_of`와 같다 (모델 기반)
    - **Property 4: 세대 판정은 `generation_of`와 같다 (모델 기반)**
    - **Validates: Requirements 5.6**
    - `tests/test_identify_generation.py`에 1990~2100년, 1~12월 또는 None에서 도구 결과 `generation`이 `generation_of`와 같고 `need` 규칙이 맞는지 Hypothesis 100회 이상 확인한다(가짜 Ctx)
  - [ ]* 7.7 `tests/test_identify_generation.py` 예제 테스트
    - 경계(2009-09/10, 2017-03/04, 2021-06/07), 경고 문구, `latest_rules`에 현재 상품 값 외 숫자 없음, `memory_updates`
    - _Requirements: 5.2, 5.3, 5.4, 5.5_
  - [ ] 7.8 `ui/tools.py`에 `claim_guide` 추가
    - 조건: inventory에 CLAIM이 있음. `locate(CLAIM)`·`locate(LIMITATION)`을 Evidence_List에 넣고("영수증"·"소멸시효" 문장 강조), `treatment_date` 없으면 `need="treatment_date"`, 있고 기간이 Verified_Value면 `deadline`·`deadline_basis`·`deadline_note`, 기간 미검증이면 `deadline=null`과 원문만
    - _Requirements: 6.1, 6.2, 6.4, 6.5, 6.6, 11.1_
  - [ ] 7.9 `ui/tools.py`에 `explain_term` 추가
    - 조건: DEFINITION ≥ 1. `glossary.lookup`으로 후보 말을 만들고 정의 조각을 찾아 상위 3개, 원문에 있는 문장만 `highlights`, 못 찾으면 `found=false`와 `rag.NOT_FOUND_MESSAGE`
    - `glossary.py` 항목은 꼭 필요할 때만 더한다(오른쪽 용어는 Chroma 원문에 있는 것만, 평가셋 정답 숫자 금지)
    - _Requirements: 7.1, 7.2, 7.3, 7.4_
  - [ ]* 7.10 `tests/test_glossary.py` 작성(glossary.py를 바꾼 경우에만)
    - 사전 오른쪽 용어가 Chroma 원문에 있는지, `eval/questions.json`의 `answer_must_include`·`evidence_contains` 숫자 표현을 포함하지 않는지 확인한다
    - _Requirements: 7.4_
  - [ ]* 7.11 `tests/test_tools_dispatch.py`에 로컬 도구 4개 케이스 추가
    - `check_exclusions` conclusive/found 조합, 4개 도구의 사용 가능 조건, Disclaimer 대상 여부
    - _Requirements: 3.2, 4.3, 4.4, 11.1_
  - [ ] 7.12 `eval/conversations.json`에 C09~C13 추가(`group: "tools"`)
    - C09·C10 check_exclusions(evidence_title "보상하지 않는 사항", no_tool calculate_payout), C11 identify_generation("2세대"), C12 claim_guide(①"영수증" ②"2029"), C13 explain_term(evidence_title "용어의 정의")
    - _Requirements: 4.6, 6.8, 7.5, 12.1_

- [ ] 8. 체크포인트: 단계 3 평가 (E15)
  - 전체 테스트를 실행한다
  - `run_eval.py`(프롬프트·glossary 변경 전후 비교)와 `run_chat.py`를 실행해 합격선, C09~C13 통과와 `tool_selection_errors`, `avg_seconds`를 확인한다
  - `eval/EXPERIMENTS.md`에 E15를 기록한다
  - 합격선을 어기면 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. 단계 4: 세션 메모리 확장과 `remaining_limit` (E16)
  - [ ] 9.1 `core/memory.py` 확장
    - 새 칸 `received_this_year`, `deductible_this_year`, `outpatient_visits_this_year`(1,000 이하), `anniversary_month`·`anniversary_day`(둘 다 유효할 때만)를 `EMPTY`·`PROMPT`·`clean`·`describe`에 더한다
    - `merge_updates(memory, updates)`는 가입 연월·세대·새 칸만 받아 `clean`을 거친다(외부 API 응답·KCD 결과를 받을 칸 없음)
    - 삭제·24시간 TTL은 기존 경로를 그대로 쓴다
    - _Requirements: 8.1, 8.2, 11.4, 5.5_
  - [ ]* 9.2 `tests/test_memory.py`에 케이스 추가
    - 새 칸 `clean`(음수·실수·bool·잘못된 날짜), `describe` 줄, `prompt_block`, `merge_updates`가 허용 칸만 받음
    - _Requirements: 8.1, 8.2, 11.4_
  - [ ] 9.3 `calculator.remaining_limit` 추가
    - design.md 3.2 표의 항목(간편가입 3개, 기본형 2종), `remaining = max(0, limit − used)`, 한도 미검증이면 `limit`·`remaining` None과 "약관에서 한도를 확인하지 못함", `used`가 None이면 `remaining` None
    - _Requirements: 8.3, 8.4, 8.5_
  - [ ] 9.4 Property 6 속성 테스트: 남은 한도의 범위
    - **Property 6: 남은 한도의 범위**
    - **Validates: Requirements 8.8**
    - `tests/test_remaining_limit.py`에 두 프로필, 선택 한도 유무, 0 이상 또는 None 입력에서 `remaining`이 None이거나 `0 ≤ remaining ≤ limit`, `limit` None이면 `remaining` None임을 Hypothesis 100회 이상 확인한다
  - [ ]* 9.5 `tests/test_remaining_limit.py` 예제 테스트
    - 간편가입 두 항목 값, 미검증 한도 None, 기본형 항목별 None과 안내 문구
    - _Requirements: 8.4, 8.5_
  - [ ] 9.6 `ui/tools.py`에 `remaining_limit` 추가
    - 조건: `rule_set.ok`. 메모에서 기수령액·누적 공제액·방문 횟수·계약해당일을 읽고, 기수령액과 누적 공제액이 모두 없으면 계산하지 않고 `need`, 계약해당일이 없으면 Policy_Year 경고
    - `calculate_payout` 간편가입 실행이 메모의 `deductible_this_year`·`outpatient_visits_this_year`를 기본값으로 쓰게 한다
    - _Requirements: 8.3, 8.6, 8.7, 11.1_
  - [ ] 9.7 `app.py`와 `eval/run_chat.py`에 `memory_updates` 반영
    - `app.py`: `reply` 결과의 `memory_updates`를 `merge_updates`로 합쳐 저장하고 "이 대화에서 기억하는 내용"에 새 칸이 보이게 한다
    - `run_chat.py`: 시나리오 `memory: true`면 턴마다 `session_memory.update`와 `merge_updates`를 부른다
    - _Requirements: 5.5, 8.2, 12.1_
  - [ ] 9.8 `eval/conversations.json`에 C14 추가
    - 간편가입 `source`, `memory: true`, tool remaining_limit, answer_includes ["50만"](200만 − 150만 손계산을 `note`에)
    - _Requirements: 8.4, 12.1, 12.6_

- [ ] 10. 체크포인트: 단계 4 평가 (E16)
  - 전체 테스트를 실행한다
  - `run_eval.py`와 `run_chat.py`를 실행해 합격선과 C14 결과를 확인한다
  - `eval/EXPERIMENTS.md`에 E16을 기록한다
  - 합격선을 어기면 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 11. 단계 5: `classify_hospital` 외부 API (E17)
  - [ ] 11.1 `boheom_mate/core/hospital.py` 작성
    - `GENERAL_CLASSES`, `to_calculator_hospital(class_name)`(공백 제거 후 상급종합병원·종합병원 또는 "종합병원"으로 끝나면 "종합병원", 그 밖은 "의원")
    - _Requirements: 9.3_
  - [ ] 11.2 Property 8 속성 테스트: 병원 종류 매핑의 값 범위
    - **Property 8: 병원 종류 매핑의 값 범위**
    - **Validates: Requirements 9.8**
    - `tests/test_hospital_class.py`에 임의 문자열(빈 문자열·공백·한글·영문·기호·실제 종별 9종)에서 결과가 "의원"/"종합병원"뿐이고 종합병원 계열은 "종합병원"임을 Hypothesis 100회 이상 확인한다. API는 부르지 않는다
  - [ ] 11.3 `boheom_mate/integrations/hira_hospital.py` 작성
    - G1·G2 가정 상수(`ENDPOINT`, `PARAMS`, `RESPONSE`, `CLASS_CODES`, `ROWS`, `SOURCE_NOTE`)를 이 파일 한 곳에 둔다. `TIMEOUT_SECONDS = 5`, 키는 `os.environ["HIRA_SERVICE_KEY"]`에서만 읽는다
    - `has_key()`, `search(name)`: 요청 파라미터는 이름·키·행 수·페이지·`_type` 다섯 개뿐, JSON 우선·XML(stdlib) 대체 파싱, 오류 코드 판정, `items.item` dict/list 처리, `totalCount`
    - `HiraError`는 고정 문구만 갖고 키·URL을 담지 않는다. 로그를 남기지 않는다. 새 라이브러리 없이 `requests`만 쓴다
    - _Requirements: 9.1, 9.2, 9.5, 9.6, 9.7, 12.10_
  - [ ] 11.4 `tests/test_hospital_class.py` 목 테스트
    - `requests.get` 목으로 1건·2건·0건·시간 초과·오류 코드 20(JSON)·XML 응답을 확인하고, 요청 params가 5개뿐인지, 예외 메시지·결과에 키 문자열이 없는지 확인한다
    - 키 보호와 전송 범위 검증이므로 필수 작업으로 둔다
    - _Requirements: 9.1, 9.2, 9.4, 9.5, 9.6_
  - [ ] 11.5 `ui/tools.py`에 `classify_hospital` 추가
    - 조건: `hira_hospital.has_key()`. `HiraError`·0건·종별 정보 없음 → `need="ask_hospital_type"`, 1건 → `calculator_hospital`, 2건 이상 → `need="choose_candidate"`, 후보 최대 5개, `source_note`
    - Evidence_List에 넣지 않고, 응답 원문을 메모·`memory_updates`에 넣지 않는다. 프롬프트 규칙: 병원 종류를 이미 말했으면 쓰지 않음
    - _Requirements: 9.1, 9.3, 9.4, 9.5, 9.7, 11.4_
  - [ ] 11.6 화면 안내와 `.env.example`
    - `app.py`: `chat.bound_tool_names(source)`에 `classify_hospital`이 있으면 상품 선택 아래에 "병원 이름 조회는 외부 공공 API(건강보험심사평가원)로 전송됩니다." 캡션
    - `.env.example`에 `HIRA_SERVICE_KEY=`(빈 값)와 주석 "공공데이터포털 개발계정 키, 병원정보서비스 15001698"을 더한다. `.env`의 실제 키는 건드리지 않는다
    - _Requirements: 9.6, 11.3_
  - [ ] 11.7 `eval/conversations.json`에 C15 추가
    - `requires: ["hira_key"]`, 기대 tool classify_hospital과 calc 10000(상급종합 → 종합병원 공제 손계산을 `note`에), `expect_fallback`은 `{"ask": true}`
    - _Requirements: 9.5, 12.1, 12.6, 12.7_
  - [ ] 11.8 `tests/smoke_hira_hospital.py` 작성과 조건부 실행
    - 스크립트: 키가 없으면 "HIRA_SERVICE_KEY 없음, 스모크 건너뜀"을 출력하고 끝낸다. 있으면 실제 병원 2~3곳을 조회해 응답 필드, JSON 정상 응답 여부, `clCd`/`clCdNm` 쌍을 출력한다(키 값은 출력하지 않음)
    - **실행 조건: `.env`에 `HIRA_SERVICE_KEY`가 있을 때만 실제 호출한다.** 키가 없으면 11.4 목 테스트만으로 마치고, 스모크는 E17 기록에 "건너뜀(키 없음)"으로 적는다
    - 키가 있어 스모크를 돌렸다면 결과로 `hira_hospital.py` 상수를 확정하고, design.md "외부 출처 확정 기록" 표의 G1·G2 행에 확인 결과와 출처를 적는다
    - _Requirements: 9.9_

- [ ] 12. 체크포인트: 단계 5 평가 (E17)
  - 전체 테스트를 실행한다(`smoke_*.py` 제외)
  - `run_eval.py`와 `run_chat.py`를 키 없는 모드로 실행한다(C15는 `expect_fallback`으로 채점). `HIRA_SERVICE_KEY`가 있으면 키 있는 모드도 실행하고, 없으면 "키 있는 모드: 건너뜀"으로 적는다
  - `eval/EXPERIMENTS.md`에 E17을 두 모드 결과(또는 건너뜀 표시)와 함께 기록한다. 결과 파일의 `mode`에는 키 유무(bool)만 남는지 확인한다
  - 합격선을 어기면 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. 단계 6: `lookup_kcd`와 KCD 로컬 색인 (E18)
  - [ ] 13.1 `.gitignore`에 `data/kcd/` 추가
    - 색인·원본 파일을 만들기 전에 추가하고 `git check-ignore data/kcd/x.csv`로 확인한다
    - _Requirements: 10.12_
  - [ ] 13.2 `boheom_mate/store/kcd_index.py` 작성
    - `INDEX_PATH`(프로젝트 루트 기준 `data/kcd/kcd_index.sqlite3`), 색인 생성 함수(`kcd`·`meta` 표), `available()`(파일 존재 + 읽기 전용 열기 + meta에 license), `lookup(text, limit=5)`(코드 모양이면 접두 일치, 아니면 완전 → 접두 → 포함, 이름 짧은 순 → 코드 순), `source_note()`
    - 외부 호출이 없다(병명이 밖으로 나가지 않음)
    - _Requirements: 10.1, 10.7, 10.8, 10.10_
  - [ ] 13.3 `boheom_mate/jobs/build_kcd_index.py` 작성
    - CSV(UTF-8/CP949 자동 판별)만 받고, `KCD_COLUMNS` 한 곳에 열 이름 가정(G4)을 두며, 열이 없으면 실제 열 목록을 출력하고 멈춘다
    - `--license`·`--downloaded`·`--version` 필수(`--license` 없으면 생성 거부), `--check`로 요약·샘플 조회. 오프라인 일회성 명령이다
    - _Requirements: 10.10, 10.12_
  - [ ] 13.4 `tests/test_kcd_index.py`와 가짜 CSV fixture
    - `tests/fixtures/`에 수십 행짜리 가짜 상병 CSV(UTF-8, CP949 각 1개)를 만들고 임시 폴더에 색인을 생성해 테스트한다. 실제 상병마스터 파일은 쓰지 않는다
    - 완전·접두·포함 순위, 코드 입력("N393"), 읽기 전용, 파일 없음 → `available()` False, `--license` 없으면 거부, 열 없음 오류, `source_note` 내용
    - 운영자 파일이 들어오기 전 유일한 검증 수단이므로 필수 작업으로 둔다
    - _Requirements: 10.1, 10.8, 10.10, 10.12_
  - [ ] 13.5 `ui/tools.py`에 `lookup_kcd` 추가
    - 조건: `kcd_index.available()`, `rule_set["code_ranges"]` ≥ 1, `check_exclusions` 사용 가능
    - 실행: `kcd_index.lookup` → 0건이나 `sqlite3.Error`면 `found=false`, `kcd.match`로 `matches`(범위·kind·condition·구역·조항·인용문)와 `kind_summary`, 매치 인용문을 Evidence_List에, 같은 실행 안에서 `check_exclusions`를 돌려 `exclusions_check`에 넣고 `turn["tools"]`에도 기록
    - `warning`(Req 10.5 문구), `source_note`, exception은 "면책 예외(보상 가능성 있음)"로 표시. 조회 결과는 메모에 저장하지 않는다
    - 프롬프트 규칙: 사용자가 이번 대화에서 직접 말한 병명·코드가 있을 때만 호출, 병명을 먼저 묻지 않음, matches만으로 단정 금지
    - _Requirements: 10.1, 10.2, 10.4, 10.5, 10.6, 10.8, 10.10, 10.11, 11.1, 11.4_
  - [ ]* 13.6 `tests/test_tools_dispatch.py`에 `lookup_kcd` 케이스 추가
    - 가짜 색인으로 사용 가능 조건 3개, `check_exclusions` 동시 실행과 `tools` 기록, exception 표시, `found=false` 경로, 메모에 결과가 들어가지 않음
    - _Requirements: 10.2, 10.6, 10.8, 10.11, 11.4_
  - [ ] 13.7 `eval/conversations.json`에 C16 추가
    - `requires: ["kcd_index"]`, 기대 tool lookup_kcd와 evidence_title "보상하지 않는 사항", `expect_fallback`은 `{tool: check_exclusions, no_tool: [lookup_kcd]}`
    - _Requirements: 10.6, 12.1, 12.7_
  - [ ] 13.8 실제 KCD 색인 생성(조건부)
    - **실행 조건: 운영자가 hira.or.kr 자료실에서 상병마스터 파일과 이용 조건을 확인하고 CSV를 `data/kcd/`에 넣은 뒤에만 한다.** 그 전에는 이 작업을 건너뛰고 13.4의 가짜 CSV 테스트만으로 마친다
    - 조건이 충족되면 `.venv/bin/python -m boheom_mate.jobs.build_kcd_index data/kcd/<파일>.csv --downloaded … --version … --license "<운영자 확인 조건>"`과 `--check`를 실행하고, 열 이름이 다르면 `KCD_COLUMNS`를 고친다. design.md "외부 출처 확정 기록" 표의 G4 행을 채운다
    - 운영자가 이용 불가로 확인하면 대체 경로(질병정보서비스 API)는 이 계획에 없으므로 멈추고 사용자에게 묻는다
    - _Requirements: 10.10, 10.12_

- [ ] 14. 체크포인트: 단계 6 평가 (E18)
  - 전체 테스트를 실행한다
  - `run_eval.py`와 `run_chat.py`를 색인 없는 모드로 실행한다(C16은 `expect_fallback`). 13.8을 했다면 색인 있는 모드도 실행하고, 아니면 "색인 있는 모드: 건너뜀(운영자 확인 전)"으로 적는다
  - `git status`로 `data/kcd/` 아래 파일이 추적되지 않는지 확인한다
  - `eval/EXPERIMENTS.md`에 E18을 기록한다
  - 합격선을 어기면 멈추고 사용자에게 묻는다
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 15. 문서 갱신
  - [ ] 15.1 `common.md` 갱신
    - 도구 9개와 각 사용 가능 조건, 간편가입 계산(프로필 구조 판별, 공식, 면책 3항목 0원 처리), Rule_Set v2, KCD 색인 생성·확인 명령(`build_kcd_index`, `--check`), `HIRA_SERVICE_KEY` 설정, E12~E18 결과 요약
    - _Requirements: 1.5, 2.1, 2.2, 9.6, 10.12, 12.8_
  - [ ] 15.2 `.kiro/steering/safety.md` 갱신
    - 외부 API 전송 규칙(병원 이름만 HIRA로, 키 보호, 응답 원문 미저장, 화면 안내), KCD는 로컬 색인이라 병명이 외부로 나가지 않음, 대체 API를 쓰면 안내 문구에 "병명"을 넣는다는 조건
    - _Requirements: 9.2, 9.6, 10.1, 10.7, 11.3, 11.4_
  - [ ] 15.3 `README.md` 갱신
    - 채팅 도구 목록(9개)과 한 줄 설명, `.env.example`의 `HIRA_SERVICE_KEY`, `data/kcd/` 색인 준비 방법
    - _Requirements: 9.6, 10.12_

- [ ] 16. 최종 체크포인트
  - 전체 테스트, `run_eval.py`, `run_chat.py`를 마지막으로 실행해 합격선(거절 100%, 검색 94% 이상, 기존 11턴 11/11, legacy 도구 선택 오류 증가 없음)을 확인한다
  - `.env`·`questions.db`·`state/`·`data/*.pdf`·`data/kcd/`가 git에 올라가지 않았는지 확인한다
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*`가 붙은 하위 작업은 선택이다. 속성 테스트 9개(3.6, 3.12, 3.13, 3.14, 3.15, 7.2, 7.6, 9.4, 11.2)와 모든 체크포인트는 필수다. 금액 기준값·기본형 22/22·키 보호·질병코드 분류·가짜 CSV 색인 테스트(3.7, 3.10, 3.16, 5.3, 11.4, 13.4)도 회귀·안전 기준이라 필수로 두었다.
- 속성과 작업 대응: P1→3.12, P2→3.13, P3→3.14, P4→7.6, P5→7.2, P6→9.4, P7→3.15, P8→11.2, P9→3.6.
- 단계 0은 코드를 바꾸지 않는다. `run_chat.py` 채점 확장은 단계 1 첫 작업(3.1)에서 하고, `--rescore`로 E12 결과 파일에서 legacy 도구 선택 오류 수를 다시 세어 기준선으로 쓴다.
- 외부 조건이 걸린 작업: 11.8(`HIRA_SERVICE_KEY`가 있을 때만 실제 스모크), 12의 키 있는 모드 평가, 13.8(운영자가 상병마스터 CSV와 이용 조건을 확인해 `data/kcd/`에 넣은 뒤에만), 14의 색인 있는 모드 평가.
- EC2 서버 반영은 하지 않는다. 간편가입 Chroma 반영은 기존 주간 약관 확인·승인 절차를 따른다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["3.1", "3.3", "3.5"] },
    { "id": 2, "tasks": ["3.2", "3.4", "3.6", "3.8", "3.11"] },
    { "id": 3, "tasks": ["3.7", "3.9", "3.12"] },
    { "id": 4, "tasks": ["3.10", "3.13", "3.17"] },
    { "id": 5, "tasks": ["3.14", "3.18"] },
    { "id": 6, "tasks": ["3.15"] },
    { "id": 7, "tasks": ["3.16"] },
    { "id": 8, "tasks": ["5.1"] },
    { "id": 9, "tasks": ["5.2"] },
    { "id": 10, "tasks": ["5.3"] },
    { "id": 11, "tasks": ["7.1", "7.4"] },
    { "id": 12, "tasks": ["7.2", "7.5"] },
    { "id": 13, "tasks": ["7.3", "7.6", "7.8"] },
    { "id": 14, "tasks": ["7.7", "7.9"] },
    { "id": 15, "tasks": ["7.10", "7.11", "7.12"] },
    { "id": 16, "tasks": ["9.1", "9.3"] },
    { "id": 17, "tasks": ["9.2", "9.4", "9.6"] },
    { "id": 18, "tasks": ["9.5", "9.7", "9.8"] },
    { "id": 19, "tasks": ["11.1", "11.3"] },
    { "id": 20, "tasks": ["11.2", "11.5"] },
    { "id": 21, "tasks": ["11.4", "11.6", "11.7", "11.8"] },
    { "id": 22, "tasks": ["13.1", "13.2"] },
    { "id": 23, "tasks": ["13.3", "13.5"] },
    { "id": 24, "tasks": ["13.4", "13.6", "13.7"] },
    { "id": 25, "tasks": ["13.8"] },
    { "id": 26, "tasks": ["15.1", "15.2", "15.3"] }
  ]
}
```
