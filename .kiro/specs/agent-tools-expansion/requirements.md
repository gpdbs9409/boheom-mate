# Requirements Document

## Introduction

`boheom-mate` 채팅 에이전트(`boheom_mate/ui/chat.py`)는 지금 도구 2개(`search_terms`, `calculate_payout`)만 쓴다. 이 스펙은 두 가지를 더한다.

- **A. 간편가입 상품 계산식**: `KB_간편가입실손의료비보장보험_26.09_약관.pdf`(상품코드 25481)는 급여·비급여를 한 담보로 합친 구조라 기본형용 계산 항목 22개 중 2개만 검증되어 계산기가 꺼져 있다. 간편가입 전용 계산 항목과 계산식을 추가한다. 값은 기본형과 같은 방식(약관 제3조에서 추출 → 인용문 검증)으로만 얻고, 기본형 상품의 동작은 바꾸지 않는다.
- **B. 새 도구 7개**: `check_exclusions`, `identify_generation`, `claim_guide`, `explain_term`, `remaining_limit`, `classify_hospital`(외부 API), `lookup_kcd`(외부 데이터).
- **C. 공통 규칙**: 도구별 채팅 평가 문항, 도구 선택 오류 측정, 거절 정확도 100% 유지, 금액·날짜는 코드 계산, 보험사 심사 고지, 근거 화면 표시, 상품별 도구 bind.

이 문서는 steering(`product`, `tech`, `structure`, `safety`)과 `common.md`를 따른다. 충돌하면 `common.md`가 우선한다.

### 이번 작성 중 확인한 사실 (2026-10-30 기준, 로컬 Chroma와 웹)

**간편가입 약관 보통약관 제3조 (Chroma, `policy_rules._articles_by_group`로 확인, 356조각, covered 13,076자)**
- 상해입원·질병입원: 보장대상의료비(급여 본인부담금 + 비급여, 비급여병실료 제외)에서 "10만원과 보장대상의료비의 30% 중 큰 금액"을 뺀 금액을 보상. 그 공제액이 계약일 또는 매년 계약해당일부터 기산해 연간 200만원을 넘으면 초과금액을 보상. 하나의 상해·질병당 보험가입금액 5천만원 한도.
- 상급병실료 차액: 비급여 병실료의 50%, 1일 평균금액 10만원 한도(입원기간 비급여 병실료 전체 ÷ 총 입원일수).
- 상해통원·질병통원: 방문 1회당 보장대상의료비(급여 본인부담금 + 비급여)에서 "2만원과 보장대상의료비의 30% 중 큰 금액"을 뺀 금액을 보험가입금액(회당 20만원) 한도 내에서 보상. 매년 계약해당일부터 1년간 방문 180회 한도. 처방조제비는 보상하지 않는다. 통원에는 병원 종류별 공제 구분과 연간 200만원 초과분 규정이 없다.
- 국민건강보험 미적용 시: 같은 공제를 뺀 금액의 40% 보상(입원·통원 모두).
- 비급여 특약1·2 구역은 Chroma에 없다(`covered` 구역만 존재).

**조항 위치 (상품마다 번호가 다르다)**
- 기본형(25478): 보통약관 제2조 용어의 정의, 제4조 보상하지 않는 사항, 제7조 보험금의 청구, 제46조 소멸시효(같은 번호에 "청약의 철회" 조각도 있음), 특별약관1·2 각각 제2조·제4조. 별표 "용어의 정의" 있음.
- 간편가입(25481): 보통약관 제2조 용어의 정의, 제4조 보상하지 않는 사항, 제6조 보험금의 청구, 제7조 보험금의 지급절차, 제45조 소멸시효. 별표 "용어의 정의" 있음.
- 따라서 조항은 번호가 아니라 구역(`section`) + 제목(`title`) 메타데이터로 찾아야 한다.

**약관 안의 질병코드 범위 (Chroma 원문 정규식 검색)**
- 기본형 보통약관 제4조: F04~F09, F04~F99, F20~F29, F30~F39, F40~F48, F80~F89, F90~F98, N96~N98, O00~O99, Q00~Q04. 특별약관1·2 제4조: F04~F99, K00~K08, K60~K62, N96~N98, O00~O99, Q00~Q04. 보통약관 제3조에도 O00~O99가 나온다(보장 조항 안의 예외 언급).
- 간편가입 보통약관 제4조: 괄호 표기 검색으로 N96~N98, O00~O99, Q00~Q04를 확인(다른 표기는 설계 단계에서 다시 확인).
- (설계 단계 재확인, 2026-10-30) 간편가입 보통약관 제4조 전체 조각에서 찾은 코드 표기: 범위 F04∼F99, F04∼F09, F20∼F29, F30∼F39, F40∼F48, F90∼F98, N96~N98, O00~O99, Q00~Q04, K60~K62, K00∼K08과 단일 코드 F51, E66, N39.3, N39.4, R32, K64, Q82.5, G47.3. 물결표는 `~`(U+007E)와 `∼`(U+223C) 두 가지가 섞여 있다.
- 코드가 모두 "보상하지 않는" 뜻은 아니다. F04∼F09, F20∼F29, F30∼F39, F40∼F48, F51, F90∼F98은 "다만, … 요양급여에 해당하는 의료비는 보상합니다"의 예외 목록이고, Q82.5(선천성 비신생물성모반)와 G47.3(수면무호흡증)도 "보상합니다" 괄호 안의 예외다. K00∼K08은 "비급여 의료비"만, K60~K62·K64는 "요양급여에 해당하지 않는 부분"만 면책이다. 따라서 코드 범위는 면책/예외 구분과 조건 문구를 함께 추출해야 한다.

**간편가입 비급여 도수치료·주사·MRI (Q4 확인 결과, Chroma 원문 직접 조회)**
- 간편가입 보통약관 제4조는 네 보장종목(상해입원·상해통원·질병입원·질병통원) 모두에 같은 면책을 둔다: "도수치료·체외충격파치료·증식치료로 인하여 발생한 비급여의료비", "비급여 주사료(다만, 항암제, 항생제(항진균제를 포함합니다), 희귀의약품은 보상합니다)", "자기공명영상진단(MRI/MRA)으로 인하여 발생한 비급여의료비(조영제, 판독료를 포함합니다)". 그리고 이 셋과 관련해 자동차보험(입원·통원 상해)·산재보험에서 생긴 본인부담의료비도 보상하지 않는다(조각 id -85, -98·-99, -112·-113, -128).
- 이 의료비가 다른 의료비와 함께 청구되어 항목별로 구분되지 않으면 회사가 각 의료비 확인을 요청할 수 있다고 적혀 있다.
- 결론: 간편가입에서는 비급여 도수치료·체외충격파·증식치료, 비급여 주사(항암제·항생제·희귀의약품 제외), 비급여 MRI/MRA가 **보상 대상이 아니다**. 제3조의 보장대상의료비에도 넣지 않는다. 급여(건강보험 적용) 도수치료·MRI는 이 면책에 해당하지 않는다.

**청구·소멸시효 조항 (간편가입, Chroma 원문)**
- 제6조(보험금의 청구): 청구서, 사고증명서(진료비 계산서·영수증, 진료비 세부산정내역, 약제비 계산서·영수증, 입원치료확인서, 의사처방전 등), 신분증 등.
- 제45조(소멸시효): "보험금 청구권 … 은 3년간 행사하지 않으면 소멸시효가 완성됩니다." 부가설명은 "소멸시효는 해당 청구권을 행사할 수 있는 때부터 진행합니다"라고 적는다. 즉 약관상 기산점은 치료일이 아니라 청구권을 행사할 수 있는 때다.

**구조 판별 (설계 단계 확인)**
- `policy_rules._articles_by_group` 결과: 간편가입은 `covered` 구역만 있고 제3조 원문(공백 제거)에 "비급여 … 를 합한 금액(이하 보장대상의료비" 문구가 있다. 기본형은 `covered`·`severe`·`non_severe` 세 구역이 있고 이 문구가 없다. 로컬 Chroma의 상품은 이 두 개뿐이다(기본형 503조각, 간편가입 356조각).

**외부 API (웹·직접 호출 확인)**
- 건강보험심사평가원_병원정보서비스: 공공데이터포털 [data.go.kr/data/15001698](https://www.data.go.kr/data/15001698/openapi.do). REST, XML, 무료, 업데이트 실시간, 이용허락범위 공공누리 제1유형(출처표시), 개발계정 자동승인 / 운영계정 심의승인, 개발계정 트래픽 10,000(운영은 활용사례 등록 시 증가 신청). 요양기호는 암호화되어 제공되며, 종별코드 등 코드표는 보건의료빅데이터개방시스템(opendata.hira.or.kr) 코드조회를 참고하라고 안내한다.
- 엔드포인트 `https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList`는 키 없이 호출하면 `SERVICE_KEY_IS_NULL`(reasonCode 20)을 돌려준다. 즉 서비스가 존재하고 서비스키 인증이 필요하다(2026-10-30 직접 호출).
- 건강보험심사평가원_질병정보서비스: [data.go.kr/data/15119055](https://www.data.go.kr/data/15119055/openapi.do). REST, XML, 무료, 공공누리 제1유형, 개발·운영 모두 자동승인, 개발계정 트래픽 10,000. 질병명칭/코드 조회를 제공하며, 통계청 한국표준질병·사인분류(KCD)를 기본으로 한 심평원 상병마스터 파일을 hira.or.kr(제도·정책 > 보험인정기준 > 자료실)에서 받을 수 있다고 안내한다.
- 통계청 통계분류포털(kssc.kostat.go.kr)은 이번에 인증서 오류로 열지 못했다. KCD 원본의 이용 조건은 확인하지 못했다.
- (설계 단계, 2026-10-30) `getHospBasisList?_type=json`을 키 없이 호출하면 JSON(`OpenAPI_ServiceResponse.cmmMsgHeader.returnReasonCode="20"`)으로 오류를 돌려준다. 오류 응답은 JSON을 지원한다. 정상 응답의 JSON 지원은 키가 없어 확인하지 못했다.
- (설계 단계) 활용가이드 docx는 이 환경에서 열어 볼 수 없었고, hira.or.kr 자료실과 opendata.hira.or.kr 코드조회는 연결되지 않았다(curl 연결 실패, 페이지 내용 없음). 상병마스터 파일의 형식과 이용 조건은 확인하지 못했다.

### 가정 (확인하지 못한 것, 설계 전에 확인 필요)

- **G1**: `getHospBasisList`의 요청 파라미터 이름(`yadmNm` 병원명, `ServiceKey`, `numOfRows`, `pageNo`, `_type`)과 응답 필드(`yadmNm`, `clCd`, `clCdNm`, `addr`)는 활용가이드 문서(`OpenAPI활용가이드_건강보험심사평가원(병원정보서비스)_210616.docx`)를 열어 보지 못해 확인하지 않았다. JSON 응답 지원 여부도 확인하지 않았다(포털 표기는 XML).
- **G2**: 종별코드 `clCd` 값(예: 01 상급종합병원, 11 종합병원, 21 병원, 28 요양병원, 31 의원, 41 치과병원, 51 치과의원, 92 한방병원, 93 한의원)은 확인하지 않았다. 코드표는 opendata.hira.or.kr 코드조회 기준으로 설계 단계에서 확정한다. 코드에 하드코딩하지 않고 응답의 `clCdNm`(종별 이름)을 우선 쓴다.
- **G3**: 질병정보서비스의 엔드포인트 이름과 파라미터는 확인하지 않았다(추측한 `diseaseInfoService1/getDissNameCodeList`는 "서비스 없음"을 돌려줬다). Q1 결정으로 이 API는 대체 경로가 되었으므로, 상병마스터 파일을 쓸 수 없을 때만 활용가이드(`..._20241011.docx`)로 확정한다.
- **G4**: 통계청 KCD 원본 파일과 심평원 상병마스터 파일의 재배포·앱 내 사용 조건은 확인하지 못했다(설계 단계에서도 hira.or.kr 연결 실패). Q1 결정대로 상병마스터 파일을 1순위로 쓰되, 구현 전에 운영자가 hira.or.kr 자료실에서 파일과 이용 조건을 직접 확인한다. 이용할 수 없으면 공공누리 제1유형이 확인된 질병정보서비스 API로 대체한다.
- G1·G2·G4는 설계 단계에서도 확인하지 못해 가정으로 남는다. 설계는 파라미터 이름·코드표·파일 열 이름을 한곳의 상수로 두고, 키·파일을 받은 뒤 1회 확인 절차(통합 스모크 테스트)로 확정한다.
- **G5**: 소멸시효 기간과 기산점은 약관 원문에서 뽑아 인용문으로 검증한다고 가정한다. 상법상 3년으로 알려져 있지만 그 숫자를 코드에 박지 않는다.
- **G6**: "올해"의 기준은 약관 문구대로 "계약일 또는 매년 계약해당일부터 1년"이다(달력 연도가 아님). 계약해당일(월·일)을 사용자가 모르면 기준일을 확정할 수 없다고 가정한다.

## Glossary

- **Chat_Agent**: `boheom_mate/ui/chat.py`의 `reply` 함수와 도구 호출 루프. LangChain `ChatOpenAI.bind_tools`로 매 턴 도구를 고른다.
- **Tool**: Chat_Agent가 `bind_tools`로 LLM에 노출하는 함수 스키마와 그 실행 함수 한 쌍.
- **Tool_Result**: Tool 실행 함수가 LLM에 돌려주는 JSON 객체.
- **Bound_Tools**: 한 턴에서 현재 상품(`source`)에 대해 Chat_Agent가 bind한 Tool 목록.
- **Product**: 상담 대상 약관 하나. Chroma 메타데이터 `source`(PDF 파일명)로 구분한다.
- **Base_Product**: `KB_실손의료비보장보험_26.09_약관.pdf`(상품코드 25478, `chat.CALCULATOR_SOURCE`).
- **Simple_Product**: `KB_간편가입실손의료비보장보험_26.09_약관.pdf`(상품코드 25481).
- **Rule_Extractor**: `boheom_mate/core/policy_rules.py`. 약관 제3조에서 계산 값을 뽑고 인용문으로 검증해 `state/rules/`에 저장한다.
- **Rule_Set**: Rule_Extractor가 한 Product에 대해 만든 결과(`values`, `quotes`, `failed`, `ok`, `doc_count` 등).
- **Rule_Profile**: 한 Product에 적용할 계산 항목 묶음. 기본형 항목 22개(`FIELDS`)를 "기본형 프로필"이라 하고, 이 스펙에서 추가하는 간편가입 항목 묶음을 "간편가입 프로필"이라 한다.
- **Verified_Value**: 인용문이 약관 원문에 그대로 있고, 숫자가 인용문 안에 있고, 앵커 단어가 인용문 앞뒤 400자 안에 있고, 값 범위가 맞는 값(`policy_rules.verify`가 None을 돌려준 값).
- **Calculator**: `boheom_mate/core/calculator.py`. 금액은 여기서만 계산한다.
- **Covered_Medical_Cost**: 간편가입 약관의 "보장대상의료비". 급여 본인부담금과 비급여(입원은 비급여병실료 제외)를 합한 금액.
- **Simple_Deductible**: 간편가입 공제액. 정액(입원 10만원 / 통원 2만원)과 Covered_Medical_Cost × 공제비율(30%) 중 큰 금액. 숫자는 모두 Verified_Value에서 온다.
- **Annual_Deductible_Cap**: 간편가입 입원 공제액의 연간 상한(약관상 200만원). 연간 누적 공제액이 이 값을 넘으면 초과분을 보상한다.
- **Policy_Year**: 약관의 "계약일 또는 매년 계약해당일부터 1년" 기간.
- **Session_Memory**: `boheom_mate/core/memory.py`의 상담 메모와 `store/question_store.py`의 `session_memory` 표. 한 대화 안에서만 쓰고 "새 대화"와 24시간 경과 시 지운다.
- **Generation**: 실손 가입 세대(1~4세대). `memory.generation_of(year, month)`가 가입 연월로 계산한다.
- **Article_Locator**: Product와 구역(`section`)·조항 제목(`title`) 메타데이터로 Chroma에서 조항 원문 조각을 정확히 꺼내는 기능(검색이 아닌 조회).
- **Coverage_Article**: 각 구역(보통약관, 특별약관)의 보장 조항(제목 "보장종목별 보상내용").
- **Exclusion_Article**: 각 구역의 "보상하지 않는 사항" 조항.
- **Claim_Article**: "보험금의 청구" 조항(기본형 보통약관 제7조, 간편가입 보통약관 제6조).
- **Limitation_Article**: "소멸시효" 조항(기본형 보통약관 제46조, 간편가입 보통약관 제45조).
- **Simple_Excluded_Items**: 간편가입 보통약관 제4조가 보상하지 않는다고 정한 비급여 도수치료·체외충격파치료·증식치료, 비급여 주사료(항암제·항생제·희귀의약품 제외), 비급여 MRI/MRA.
- **Definition_Article**: 보통약관·특별약관의 "용어의 정의" 조항과 별표 "용어의 정의".
- **Glossary_Module**: `boheom_mate/core/glossary.py`의 일상어 → 약관 용어 사전.
- **Exclusion_Code_Range**: Exclusion_Article 원문에 적힌 질병코드 범위(예: O00~O99, K00~K08). 원문에서 추출하고 인용문으로 검증한다.
- **HIRA_Hospital_API**: 건강보험심사평가원_병원정보서비스 `getHospBasisList`(공공데이터포털 15001698).
- **KCD_Source**: 병명 → KCD 코드 조회에 쓰는 데이터 출처. 1순위(확정): 심평원 상병마스터 파일을 받아 만든 로컬 색인(병명이 외부로 나가지 않음). 대체: 건강보험심사평가원_질병정보서비스 API(공공데이터포털 15119055). 대체는 상병마스터 파일을 쓸 수 없다고 확인된 경우에만 쓴다.
- **KCD_Master_File**: 운영자가 hira.or.kr(제도·정책 > 보험인정기준 > 자료실)에서 받은 상병마스터 파일 원본. git에 올리지 않고 `data/` 아래에 두며, 서버가 원본이다.
- **Exclusion_Exception**: Exclusion_Article 안에서 "다만, … 보상합니다"처럼 면책에서 다시 빼 주는 예외 문구에 나오는 질병코드나 범위.
- **Hospital_Class**: HIRA 종별 이름(상급종합병원, 종합병원, 병원, 요양병원, 의원, 치과병원, 치과의원, 한방병원, 한의원 등).
- **Calculator_Hospital**: 계산기 입력의 병원 구분(`"의원"` 또는 `"종합병원"`).
- **Disclaimer**: 고정 문구 "실제 지급 여부와 금액은 보험사 심사로 확정됩니다."(문구는 설계에서 확정, 모든 Tool_Result에 같은 문자열을 쓴다).
- **Not_Found_Message**: 근거가 없을 때 쓰는 고정 문구 "약관에서 확인할 수 없음".
- **Evidence_List**: 화면의 근거 조항 목록. `turn["evidence"]` 항목(`section`, `article_no`, `title`, `text`, `highlights`)으로 그려진다.
- **Chat_Eval**: `eval/run_chat.py`와 `eval/conversations.json`.
- **RAG_Eval**: `eval/run_eval.py`(dev 20문항 × 3회).
- **Developer**: 이 스펙을 구현하고 평가를 돌리는 사람(또는 에이전트).
- **Test_Suite**: 루트 `tests/`의 `test_*.py` 파일들(pytest 없이 개별 실행).
- **Dependency_Manifest**: 루트 `requirements.txt`.
- **Tool_Selection_Error**: Chat_Eval 한 턴에서 기대한 Tool을 부르지 않았거나, 부르면 안 된다고 적은 Tool을 부른 경우.

## Requirements

### Requirement 1: 간편가입 계산 항목 추출과 검증

**User Story:** As a 보험설계사, I want 간편가입 상품도 약관에서 검증한 값으로 계산되기를, so that 간편가입 고객에게도 근거 있는 참고 금액을 보여 줄 수 있다.

#### Acceptance Criteria

1. THE Rule_Extractor SHALL 간편가입 프로필로 다음 값을 Simple_Product 보통약관 제3조에서 추출한다: 입원 공제 정액, 입원 공제 비율, 입원 연간 공제 상한(Annual_Deductible_Cap), 통원 공제 정액, 통원 공제 비율, 통원 1회당 보상 한도, 통원 연간 방문 한도 횟수, 상급병실료 차액 보상 비율, 상급병실료 차액 1일 평균 한도, 국민건강보험 미적용 시 보상 비율.
2. THE Rule_Extractor SHALL 간편가입 프로필의 모든 값을 기본형과 같은 `verify` 규칙(인용문 원문 일치, 숫자 포함, 앵커 단어, 값 범위)으로 검증한다.
3. WHEN 간편가입 프로필의 필수 값이 모두 Verified_Value이면, THE Rule_Extractor SHALL Simple_Product의 Rule_Set에 `ok=true`와 프로필 이름 "간편가입"을 기록한다.
4. IF 간편가입 프로필의 필수 값 중 하나라도 검증에 실패하면, THEN THE Rule_Extractor SHALL Simple_Product의 Rule_Set에 `ok=false`와 실패 이유를 항목별로 기록한다.
5. THE Rule_Extractor SHALL Product마다 Rule_Profile을 약관 구조(보통약관 제3조의 "보장대상의료비" 합산 구조 여부와 비급여 특약 구역 존재 여부)로 정하고, 상품코드나 파일명으로 정하는 분기를 두지 않는다.
6. THE Rule_Extractor SHALL Base_Product에 기본형 프로필 22개 항목을 계속 적용하고, Base_Product의 Rule_Set 값이 이 스펙 적용 전 `state/rules/` 값 22/22와 같게 유지한다.
7. THE Rule_Extractor SHALL 간편가입 프로필 값의 숫자를 소스 코드 상수로 두지 않는다(테스트 기준값 상수는 `tests/` 또는 `calculator.KB_BASE_RULES`처럼 테스트 전용으로만 둔다).
8. THE Rule_Extractor SHALL 간편가입 프로필에서 Simple_Excluded_Items 세 항목(도수치료·체외충격파·증식치료, 비급여 주사료, 비급여 MRI/MRA)이 보통약관 제4조에서 보상하지 않는 사항으로 적혀 있음을 각각 원문 인용문으로 검증하고, 하나라도 검증하지 못하면 Rule_Set을 `ok=false`로 둔다.

### Requirement 2: 간편가입 보험금 계산식

**User Story:** As a 일반 고객, I want 간편가입 상품에서 영수증 금액으로 참고 보험금을 받기를, so that 내가 얼마를 돌려받을지 대략 알 수 있다.

#### Acceptance Criteria

1. WHEN 간편가입 프로필 Rule_Set으로 입원 계산이 요청되면, THE Calculator SHALL 보상액을 Covered_Medical_Cost − Simple_Deductible(입원 정액과 Covered_Medical_Cost × 입원 공제 비율 중 큰 금액)로 계산하고 0원 미만이면 0원으로 한다.
2. WHEN 간편가입 프로필 Rule_Set으로 통원 계산이 요청되면, THE Calculator SHALL 방문 1회마다 (회당 Covered_Medical_Cost − Simple_Deductible)을 0원과 통원 1회당 보상 한도 사이로 제한한 금액을 방문 횟수만큼 더한다.
3. WHEN 간편가입 통원 계산의 방문 횟수 합계가 통원 연간 방문 한도 횟수를 넘으면, THE Calculator SHALL 한도 횟수까지만 보상액에 넣고 초과 방문 수를 결과 notes에 적는다.
4. WHEN 간편가입 입원 계산에 상급병실료 차액과 입원 일수가 주어지면, THE Calculator SHALL 상급병실료 차액 보상액을 min(비급여 병실료 × 보상 비율, 1일 평균 한도 × 입원 일수)로 계산하고 Covered_Medical_Cost에는 비급여 병실료를 넣지 않는다.
5. WHEN 간편가입 입원 계산에 Policy_Year 누적 입원 공제액이 주어지면, THE Calculator SHALL 이번 공제액 중 누적 공제액과 합쳐 Annual_Deductible_Cap을 넘는 부분을 보상액에 더한다.
6. IF 간편가입 입원 계산에 Policy_Year 누적 입원 공제액이 주어지지 않으면, THEN THE Calculator SHALL 누적 공제액을 0원으로 계산하고 "올해 이미 낸 공제액이 있으면 연간 200만원 초과분이 더 보상될 수 있음"을 결과 notes에 적는다(200만원은 Verified_Value에서 가져와 표시한다).
7. THE Calculator SHALL 간편가입 계산에서 병원 종류(Calculator_Hospital) 입력을 공제액에 반영하지 않고, 병원 종류를 받으면 "이 상품은 병원 종류에 따라 공제금액이 달라지지 않음"을 notes에 적는다.
8. THE Calculator SHALL 간편가입 계산 결과의 `rules_used`에 계산에 쓴 Rule_Set 키를 모두 넣는다.
9. FOR ALL 0원 이상 급여 본인부담금·비급여 금액, 1 이상 방문 횟수 조합에서, THE Calculator SHALL 간편가입 보상액이 0원 이상이고 낸 금액 합계 이하가 되게 한다(불변식, Hypothesis 100회 이상).
10. FOR ALL 같은 조건에서 낸 금액만 늘린 두 입력에서, THE Calculator SHALL 간편가입 보상액이 줄지 않게 한다(단조성, Hypothesis 100회 이상).
11. THE Calculator SHALL 기본형 프로필 계산 결과를 이 스펙 적용 전과 같게 유지하고, `tests/test_calculator.py`가 변경 없이 통과한다.
12. WHEN 간편가입 계산에 비급여 도수치료·주사·MRI 금액이 주어지면, THE Calculator SHALL 그 금액을 Covered_Medical_Cost에 넣지 않고 보상액 0원 항목으로 표시하며, 검증된 제4조 인용문과 "항암제·항생제·희귀의약품 주사는 보상되므로 일반 비급여로 알려 주세요" 안내를 notes에 적는다.
13. FOR ALL 간편가입 계산 입력에서, THE Calculator SHALL 비급여 도수치료·주사·MRI 금액만 바꾼 두 입력의 총 보상액을 같게 한다(불변식, Hypothesis 100회 이상).

### Requirement 3: 상품별 도구 bind와 공통 Tool_Result 규칙

**User Story:** As a 보험설계사, I want 현재 상품에서 쓸 수 있는 도구만 에이전트가 쓰기를, so that 검증되지 않은 값이나 없는 조항으로 답하지 않는다.

#### Acceptance Criteria

1. WHEN 한 턴이 시작되면, THE Chat_Agent SHALL 현재 Product에서 사용 가능 조건을 만족한 Tool만 Bound_Tools에 넣는다.
2. THE Chat_Agent SHALL 각 Tool의 사용 가능 조건을 다음과 같이 판정한다: `search_terms`와 `identify_generation`은 항상, `calculate_payout`과 `remaining_limit`은 Rule_Set `ok=true`, `check_exclusions`는 Coverage_Article과 Exclusion_Article이 모두 조회될 때, `claim_guide`는 Claim_Article이 조회될 때, `explain_term`은 Definition_Article이 하나 이상 조회될 때, `classify_hospital`은 HIRA_Hospital_API 키가 설정되었을 때, `lookup_kcd`는 KCD_Source를 쓸 수 있고 Exclusion_Code_Range가 하나 이상 검증되었을 때.
3. IF LLM이 Bound_Tools에 없는 Tool 이름을 호출하면, THEN THE Chat_Agent SHALL 그 호출을 실행하지 않고 `{"error": "없는 도구: <이름>"}`을 Tool_Result로 돌려준다.
4. THE Chat_Agent SHALL 금액·날짜·세대·횟수가 들어 있는 모든 Tool_Result에 Disclaimer 문자열을 포함한다.
5. THE Chat_Agent SHALL Tool_Result의 근거 조항(조항 원문 조각 또는 인용문)을 Evidence_List 항목 형식(`section`, `article_no`, `title`, `text`, `highlights`)으로 `turn["evidence"]`에 중복 없이 더한다.
6. THE Chat_Agent SHALL 한 턴에서 실행한 Tool 이름 목록을 `reply` 반환값의 `tools` 키에 호출 순서대로 담는다.
7. IF Tool 실행 중 `ValueError`, `TypeError` 또는 외부 호출 실패가 발생하면, THEN THE Chat_Agent SHALL 오류 내용을 Tool_Result `error`에 담아 돌려주고 대화를 계속한다.
8. THE Chat_Agent SHALL 시스템 프롬프트에 Bound_Tools에 들어간 Tool의 사용 규칙만 넣는다.

### Requirement 4: `check_exclusions` 면책 확인

**User Story:** As a 일반 고객, I want "이건 보상돼요?"에 보장 조항과 면책 조항을 함께 보고 답받기를, so that 한쪽 조항만 보고 잘못 안내받지 않는다.

#### Acceptance Criteria

1. WHEN `check_exclusions`가 질문 문장과 함께 호출되면, THE Chat_Agent SHALL 현재 Product의 Coverage_Article 관련 조각과 Exclusion_Article 관련 조각을 각각 따로 찾아 한 Tool_Result에 `coverage`와 `exclusions` 두 목록으로 돌려준다.
2. THE Chat_Agent SHALL `check_exclusions`의 Exclusion_Article 조각을 Product의 모든 구역(보통약관과 있는 특별약관 전부)에서 찾는다.
3. IF `coverage`와 `exclusions` 중 한쪽이 비어 있으면, THEN THE Chat_Agent SHALL Tool_Result에 `conclusive=false`를 넣고, 시스템 프롬프트 규칙에 따라 "보상된다" 또는 "보상되지 않는다"를 단정하지 않는다.
4. IF 두 목록이 모두 비어 있으면, THEN THE Chat_Agent SHALL Tool_Result에 `found=false`를 넣고 답변에 Not_Found_Message를 쓴다.
5. THE Chat_Agent SHALL `check_exclusions`가 돌려준 조각 중 LLM 답변이 근거로 든 조각만 Evidence_List에 넣고, 근거 번호 검증은 `rag.answer`와 같은 방식(실제 돌려준 조각 번호만 인정)으로 한다.
6. WHEN Chat_Eval의 `check_exclusions` 문항(예: "임신 관련 진료도 실손 돼요?", "치과 임플란트 보상돼요?")이 실행되면, THE Chat_Agent SHALL 그 턴에서 `check_exclusions`를 호출하고 Evidence_List에 Exclusion_Article 조각을 1개 이상 넣는다.

### Requirement 5: `identify_generation` 세대 확인

**User Story:** As a 일반 고객, I want 가입 시기만 말하면 내 실손 세대를 알기를, so that 이 앱의 안내가 내 계약과 얼마나 다를 수 있는지 안다.

#### Acceptance Criteria

1. WHEN `identify_generation`이 가입 연도와 (있으면) 가입 월로 호출되면, THE Chat_Agent SHALL `memory.generation_of`로 Generation을 계산해 Tool_Result `generation`에 넣는다.
2. IF `memory.generation_of`가 None을 돌려주면(경계 연도에 월이 없거나 범위 밖 연도), THEN THE Chat_Agent SHALL Tool_Result에 `generation=null`과 `need="joined_month"`(또는 `need="joined_year"`)를 넣어 LLM이 사용자에게 되묻게 한다.
3. WHEN Generation이 1세대, 2세대, 3세대 중 하나이면, THE Chat_Agent SHALL Tool_Result에 "현재 앱의 약관·계산은 최신 약관 기준 참고 정보이며 가입하신 세대의 약관과 자기부담률·보장이 다를 수 있으니 보험증권과 가입 당시 약관을 확인하세요" 경고를 넣는다.
4. THE Chat_Agent SHALL `identify_generation`의 "최신 약관 대비 차이" 안내에 현재 Product의 Verified_Value와 그 인용문만 쓰고, 1~3세대의 자기부담률·공제금액 숫자나 금융감독원 등 외부 자료의 세대별 설명을 Tool_Result에 넣지 않는다(세대별 약관 데이터가 없기 때문, Q3 결정).
5. WHEN `identify_generation`이 Generation을 계산하면, THE Chat_Agent SHALL 그 가입 연월과 Generation을 Session_Memory에 반영한다.
6. FOR ALL 1990~2100년, 1~12월 입력에서, THE Chat_Agent SHALL `identify_generation`의 `generation` 값이 `memory.generation_of`의 결과와 같게 한다(모델 기반 속성, Hypothesis 100회 이상).

### Requirement 6: `claim_guide` 청구 서류·기한

**User Story:** As a 일반 고객, I want 보험금 청구에 필요한 서류와 언제까지 청구해야 하는지 알기를, so that 서류를 빠뜨리거나 기한을 놓치지 않는다.

#### Acceptance Criteria

1. WHEN `claim_guide`가 호출되면, THE Chat_Agent SHALL Article_Locator로 현재 Product의 Claim_Article 원문을 조회해 Tool_Result `documents_article`에 넣고 Evidence_List에 더한다.
2. WHEN `claim_guide`가 호출되면, THE Chat_Agent SHALL Article_Locator로 현재 Product의 Limitation_Article 원문을 조회해 Tool_Result `limitation_article`에 넣고 Evidence_List에 더한다.
3. THE Rule_Extractor SHALL Limitation_Article에서 소멸시효 기간(년 수)을 추출하고 인용문으로 검증한다.
4. WHEN `claim_guide`가 치료일 인자 `treatment_date`("YYYY-MM-DD")와 함께 호출되고 소멸시효 기간이 Verified_Value이면, THE Chat_Agent SHALL `treatment_date`를 기준으로 참고 기한 날짜를 코드로 계산해(치료일 + 기간 년 수, 2월 29일은 해당 연도 2월 말일로) Tool_Result `deadline`에 "YYYY-MM-DD" 형식으로 넣고, `deadline_basis`에 "치료일 기준 참고 날짜"를 넣는다. 사용자가 치료일이 아닌 다른 날짜(사고일, 청구권이 생긴 날)를 말해도 기준은 치료일로 두고, `treatment_date`가 없으면 `deadline`을 null로 두고 `need="treatment_date"`를 넣는다(Q5 결정).
5. IF 소멸시효 기간이 Verified_Value가 아니면, THEN THE Chat_Agent SHALL `deadline`을 null로 두고 Limitation_Article 원문만 안내한다.
6. THE Chat_Agent SHALL `deadline`이 있을 때마다 "치료일 기준으로 계산한 참고 날짜입니다. 약관상 소멸시효는 청구권을 행사할 수 있는 때부터 진행하므로 실제 기산일은 사안마다 다를 수 있고, 실제 판단은 보험사 심사로 확정됩니다" 안내를 Tool_Result `deadline_note`에 넣는다.
7. FOR ALL 1990-01-01~2100-12-31 사이 치료일과 1~10년 기간에서, THE Chat_Agent SHALL 계산한 `deadline`이 치료일보다 늦고, 연도 차이가 기간과 같고, 유효한 달력 날짜가 되게 한다(불변식, Hypothesis 100회 이상).
8. WHEN Chat_Eval의 `claim_guide` 문항("보험금 청구하려면 어떤 서류를 내야 해요?")이 실행되면, THE Chat_Agent SHALL `claim_guide`를 호출하고 답변에 "영수증"을 포함한다.

### Requirement 7: `explain_term` 용어 풀이

**User Story:** As a 일반 고객, I want 약관 용어의 뜻을 쉬운 말로 알기를, so that 답변과 약관 원문을 이해할 수 있다.

#### Acceptance Criteria

1. WHEN `explain_term`이 용어와 함께 호출되면, THE Chat_Agent SHALL Glossary_Module로 일상어를 약관 용어로 바꾼 뒤 Article_Locator로 현재 Product의 Definition_Article에서 그 용어가 들어 있는 정의 조각을 찾아 Tool_Result `definitions`에 넣는다.
2. IF Definition_Article에서 용어 정의를 찾지 못하면, THEN THE Chat_Agent SHALL Tool_Result에 `found=false`를 넣고 답변에 Not_Found_Message를 쓴다.
3. THE Chat_Agent SHALL `explain_term`의 근거로 실제 정의 조각만 Evidence_List에 넣고, 정의 원문에 있는 문장만 `highlights`로 표시한다.
4. WHERE Glossary_Module에 새 항목을 추가하면, THE Glossary_Module SHALL 오른쪽 약관 용어가 Chroma 약관 원문에 실제로 있는 항목만 가지며, `eval/questions.json`의 `answer_must_include`와 `evidence_contains`에 있는 숫자 표현을 포함하지 않는다.
5. WHEN Chat_Eval의 `explain_term` 문항(예: "비급여가 무슨 뜻이에요?")이 실행되면, THE Chat_Agent SHALL `explain_term`을 호출하고 Evidence_List에 Definition_Article 조각을 1개 이상 넣는다.

### Requirement 8: `remaining_limit` 올해 받은 금액 반영

**User Story:** As a 보험설계사, I want 고객이 올해 이미 받은 금액과 낸 공제액을 반영한 남은 한도를 보기를, so that 연간 한도와 간편가입 200만원 초과분을 함께 안내할 수 있다.

#### Acceptance Criteria

1. THE Session_Memory SHALL 다음 칸을 추가로 가진다: Policy_Year 기준 기수령 보험금(원), Policy_Year 누적 입원 공제액(원), Policy_Year 통원 방문 횟수, 계약해당일(월·일).
2. THE Session_Memory SHALL 추가 칸을 기존 칸과 같은 규칙으로 다룬다: 사용자가 직접 말한 값만 기록, 정수 검증(`clean`), "새 대화"·"기억 지우기"에서 삭제, 24시간(`MEMORY_TTL_HOURS`) 경과 시 삭제, 화면 "이 대화에서 기억하는 내용"에 표시.
3. WHEN `remaining_limit`이 호출되면, THE Chat_Agent SHALL Session_Memory의 기수령액·누적 공제액·방문 횟수와 현재 Product의 Verified_Value(연간·회당 한도, 방문 한도 횟수, Annual_Deductible_Cap)로 남은 한도를 Calculator에서 계산한다.
4. WHERE Product가 간편가입 프로필이면, THE Calculator SHALL `remaining_limit` 결과에 "Annual_Deductible_Cap까지 남은 공제액"과 "남은 통원 방문 횟수"를 넣는다.
5. IF 남은 한도 계산에 필요한 한도 값이 Verified_Value가 아니면, THEN THE Calculator SHALL 그 항목을 null로 두고 "약관에서 한도를 확인하지 못함"을 notes에 적는다.
6. IF Session_Memory에 기수령액과 누적 공제액이 모두 없으면, THEN THE Chat_Agent SHALL 계산하지 않고 Tool_Result `need`에 필요한 값 이름을 넣어 LLM이 되묻게 한다.
7. IF Session_Memory에 계약해당일이 없으면, THEN THE Chat_Agent SHALL Tool_Result에 "올해는 달력 연도가 아니라 계약일부터 1년 단위이므로 기준일에 따라 실제와 다를 수 있음" 경고를 넣는다.
8. FOR ALL 0원 이상 기수령액·누적 공제액과 0 이상 방문 횟수에서, THE Calculator SHALL 남은 한도 값이 0 이상이고 해당 Verified_Value 한도 이하가 되게 한다(불변식, Hypothesis 100회 이상).
9. FOR ALL 같은 조건에서 누적 공제액만 늘린 두 입력에서, THE Calculator SHALL 간편가입 입원 보상액이 줄지 않게 한다(단조성, Hypothesis 100회 이상).

### Requirement 9: `classify_hospital` 병원 종류 판별 (외부 API)

**User Story:** As a 일반 고객, I want 병원 이름만 말하면 병원 종류를 알아서 판단해 주기를, so that 의원·종합병원 구분을 몰라도 계산을 받을 수 있다.

#### Acceptance Criteria

1. WHEN `classify_hospital`이 병원 이름과 함께 호출되면, THE Chat_Agent SHALL HIRA_Hospital_API에 병원 이름만 보내 조회하고, 결과의 기관명·Hospital_Class·주소를 Tool_Result `candidates`에 최대 5개 넣는다.
2. THE Chat_Agent SHALL HIRA_Hospital_API 요청에 병원 이름, 서비스키, 페이지 파라미터 외의 값(병명, 가입 정보, Session_Memory 내용)을 넣지 않는다.
3. WHEN 조회 결과가 정확히 1건이면, THE Chat_Agent SHALL Hospital_Class를 Calculator_Hospital로 코드 매핑(상급종합병원·종합병원 → "종합병원", 그 밖 → "의원")해 Tool_Result `calculator_hospital`에 넣는다.
4. WHEN 조회 결과가 2건 이상이면, THE Chat_Agent SHALL `calculator_hospital`을 null로 두고 Tool_Result `need="choose_candidate"`를 넣어 LLM이 후보 중 하나를 사용자에게 고르게 한다.
5. IF HIRA_Hospital_API 키가 없거나, 호출이 5초 안에 응답하지 않거나, 오류 응답(`returnReasonCode` 등)을 돌려주거나, 결과가 0건이면, THEN THE Chat_Agent SHALL Tool_Result `need="ask_hospital_type"`을 넣어 LLM이 사용자에게 병원 종류를 되묻게 하고 대화를 계속한다.
6. THE Chat_Agent SHALL HIRA_Hospital_API 서비스키를 `.env`의 환경변수 `HIRA_SERVICE_KEY`(`.env.example`에 빈 값으로 추가)에서만 읽고, 로그·Tool_Result·화면·저장소에 키 값을 남기지 않는다. 키는 공공데이터포털 개발계정(자동승인, 트래픽 10,000) 키로 시작한다(Q2 결정, 하루 질문 한도 100개 기준으로 충분). 운영계정 신청은 이 스펙 범위 밖이다.
7. THE Chat_Agent SHALL `classify_hospital` 결과를 보여 줄 때 출처 "건강보험심사평가원 병원정보서비스(공공누리 제1유형)"를 Tool_Result `source_note`에 넣는다.
8. FOR ALL Hospital_Class 문자열에서, THE Chat_Agent SHALL Calculator_Hospital 매핑 결과가 `"의원"` 또는 `"종합병원"` 중 하나가 되게 한다(Hypothesis 100회 이상, API는 목으로 대체).
9. THE Chat_Agent SHALL HIRA_Hospital_API 파라미터 이름과 종별 코드값을 가정 G1·G2 확인 결과(활용가이드, opendata.hira.or.kr 코드조회)에 따라 확정하고, 확인한 출처를 설계 문서에 남긴다.

### Requirement 10: `lookup_kcd` 질병코드 찾기 (외부 데이터)

**User Story:** As a 보험설계사, I want 고객이 말한 병명의 KCD 코드 후보와 약관 면책 코드 범위를 대조해 보기를, so that 면책 가능성을 참고 정보로 빠르게 짚을 수 있다.

#### Acceptance Criteria

1. WHEN `lookup_kcd`가 병명과 함께 호출되면, THE Chat_Agent SHALL KCD_Source에서 KCD 코드 후보를 최대 5개 찾아 Tool_Result `candidates`(코드, 한글 병명)에 넣는다. KCD_Source가 로컬 색인이면 병명을 외부로 보내지 않는다(Q1 결정).
2. THE Chat_Agent SHALL `lookup_kcd`를 사용자가 이번 대화에서 직접 말한 병명이나 질병코드가 있을 때만 호출하도록 시스템 프롬프트에 규칙을 넣고, 병명·진단명을 사용자에게 먼저 묻지 않는다.
3. THE Rule_Extractor SHALL 현재 Product의 Exclusion_Article 원문에서 Exclusion_Code_Range(범위와 단일 코드 모두)를 추출하고 각 범위를 원문 인용문으로 검증한다(코드 범위를 소스 코드에 하드코딩하지 않는다).
4. WHEN KCD 후보 코드가 Exclusion_Code_Range 안에 들면, THE Chat_Agent SHALL Tool_Result `matches`에 그 범위, 면책/예외 구분(`kind`), 구역, 조항, 인용문을 넣고 Evidence_List에 그 Exclusion_Article 인용문을 더한다.
5. THE Chat_Agent SHALL `lookup_kcd` Tool_Result에 "질병코드는 병명으로 찾은 후보이며 실제 진단서의 코드와 다를 수 있습니다. 보상 여부는 약관 조항과 보험사 심사로 확정됩니다" 경고를 넣고, 시스템 프롬프트 규칙으로 `matches`만으로 "보상되지 않는다"를 단정하지 않게 한다.
6. WHEN `lookup_kcd`가 호출되면, THE Chat_Agent SHALL 같은 턴에서 `check_exclusions` 결과(보장 조항 + 면책 조항)를 함께 근거로 써서 답한다.
7. WHERE KCD_Source가 대체 경로(질병정보서비스 API)이면, THE Chat_Agent SHALL 요청에 병명, 서비스키, 페이지 파라미터 외의 값(Session_Memory 내용, 가입 정보)을 넣지 않고, 호출 제한 시간 5초를 적용한다. 로컬 색인에서는 병명을 어떤 외부로도 보내지 않는다.
8. IF 로컬 색인 파일이 없거나 읽을 수 없거나 조회 결과가 0건이면(대체 API이면 5초 안에 응답하지 않거나 오류인 경우 포함), THEN THE Chat_Agent SHALL Tool_Result `found=false`를 넣고, LLM은 코드 대조 없이 `check_exclusions` 결과로만 안내한다.
9. FOR ALL 알파벳 1자 + 숫자 2자리(선택적으로 소수점 이하) KCD 코드와 Exclusion_Code_Range 조합에서, THE Chat_Agent SHALL 범위 포함 판정이 "같은 알파벳이고 시작 숫자 ≤ 코드 앞 두 자리 숫자 ≤ 끝 숫자"와 같게 한다. 범위 끝점이나 단일 코드에 소수점 이하가 있으면(예: N39.3) 소수점 이하까지 같은 자릿수로 비교한다(모델 기반 속성, Hypothesis 100회 이상).
10. THE Chat_Agent SHALL KCD_Source의 출처("건강보험심사평가원 상병마스터(KCD 기반)", 받은 날짜·파일 버전)와 이용허락범위(G4 확인 결과)를 Tool_Result `source_note`에 넣는다. 대체 API를 쓰면 "건강보험심사평가원 질병정보서비스(공공누리 제1유형)"를 넣는다.
11. THE Rule_Extractor SHALL Exclusion_Code_Range마다 면책(`excluded`)과 Exclusion_Exception(`exception`)을 구분하고, 조건 문구(예: "비급여 의료비만", "요양급여에 해당하지 않는 부분")를 함께 저장한다. WHEN 후보 코드가 `exception`에만 들면, THE Chat_Agent SHALL 그 매치를 "면책 예외(보상 가능성 있음)"로 표시하고 면책으로 안내하지 않는다.
12. THE KCD_Master_File과 그로부터 만든 로컬 색인 SHALL `data/kcd/` 아래에 두고 `.gitignore`로 git에서 제외하며, `deploy/push.sh`로 서버에 올리지 않는다(서버가 원본). 색인 생성은 오프라인 일회성 명령으로 한다.

### Requirement 11: 고지와 PII 안내

**User Story:** As a 보험사 담당자, I want 새 도구들도 기존 안전 규칙을 지키기를, so that 오안내와 개인정보 노출로 인한 민원을 막는다.

#### Acceptance Criteria

1. THE Chat_Agent SHALL `calculate_payout`, `remaining_limit`, `claim_guide`, `identify_generation`, `check_exclusions`, `lookup_kcd` Tool_Result에 Disclaimer를 넣는다.
2. THE Calculator SHALL 간편가입 계산 결과 notes에 기존 기본형과 같은 면책·연간 한도·기수령액 안내(미용·성형, 치과 보철, 라식, 예방접종, 산재·자동차보험 중복 등에 따라 실제 금액이 달라지거나 0원일 수 있음)를 넣는다.
3. WHERE `classify_hospital`이 Bound_Tools에 있으면, THE Chat_Agent SHALL 화면에 "병원 이름 조회는 외부 공공 API(건강보험심사평가원)로 전송됩니다" 안내를 표시한다. `lookup_kcd`는 로컬 색인을 쓰므로 이 안내 대상이 아니다. WHERE `lookup_kcd`가 대체 API를 쓰면, THE Chat_Agent SHALL 안내 문구에 "병명"을 함께 넣는다.
4. THE Session_Memory SHALL `classify_hospital`의 외부 응답 원문과 `lookup_kcd`의 조회 결과 원문(로컬 색인 결과와 대체 API 응답 모두)을 저장하지 않고, 사용자가 확인한 Calculator_Hospital 값만 기존 `hospital` 칸에 반영한다.

### Requirement 12: 평가와 회귀 방지

**User Story:** As a 개발자, I want 도구를 늘려도 기존 품질이 떨어지지 않음을 수치로 확인하기를, so that 도구 추가가 거절 정확도와 도구 선택을 망치지 않는다.

#### Acceptance Criteria

1. THE Chat_Eval SHALL 새 도구 7개와 간편가입 계산마다 "이 질문이면 이 도구를 불러야 한다" 문항을 1개 이상 `eval/conversations.json`에 추가하고, 턴 `expect`에 `tool`(불러야 할 도구 이름)과 `no_tool`(부르면 안 되는 도구 이름 목록) 키를 쓸 수 있게 한다.
2. THE Chat_Eval SHALL 턴 `expect`에 `source`가 있으면 그 Product로 `chat.reply`를 호출한다(간편가입 문항용).
3. THE Chat_Eval SHALL 전체 턴의 Tool_Selection_Error 수를 결과 요약 `tool_selection_errors`에 기록한다.
4. WHEN 이 스펙의 변경을 적용한 뒤 Chat_Eval을 실행하면, THE Chat_Eval SHALL 기존 11턴이 11/11 통과하고, 기존 11턴의 Tool_Selection_Error 수가 적용 전 기준 실행보다 늘지 않음을 보여 준다.
5. WHEN 이 스펙의 변경을 적용한 뒤 RAG_Eval을 실행하면, THE RAG_Eval SHALL 거절 정확도 100%를 보여 주고, 검색 적중이 적용 전 94%보다 낮지 않음을 보여 준다.
6. THE Chat_Eval SHALL 간편가입 계산 문항의 기대 총액을 약관 공식으로 손계산한 값으로 적고, 계산 근거를 문항 `name`이나 주석 필드에 적는다.
7. THE Chat_Eval SHALL 외부 API 도구 문항을 API 키가 없는 환경에서 실행하면 "되묻기"(`ask: true`)를 기대값으로 채점하는 모드를 가진다.
8. WHEN 각 단계(간편가입 계산, 도구 추가)를 마치면, THE Developer SHALL `eval/EXPERIMENTS.md`에 가설·바꾼 것·결과 파일·판정을 새 실험 번호(E12부터)로 기록한다.
9. THE Test_Suite SHALL `tests/test_*.py` 파일을 `.venv/bin/python`으로 직접 실행하고 성공 시 `OK` 또는 `NN/NN 통과`를 출력하며, 각 속성 테스트에 검증하는 속성을 주석으로 단다.
10. THE Dependency_Manifest SHALL 외부 API 호출에 기존 `requests`만 쓰는 상태를 유지하고, 새 라이브러리를 추가하면 그 라이브러리를 정확한 버전(`==`)으로 고정해 기록한다.

## Out of Scope

- 여러 상품 비교, 로그인/회원가입, 알림, 공유, 관리자 화면, 화면 PDF 업로드.
- 1~3세대 약관 데이터 수집과 세대별 실제 계산식(이번엔 경고와 최신 약관 값만).
- 간편가입 외 상품(계약전환용 25483, 다이렉트 25487, 다이렉트간편 25488)의 계산식. 다만 Requirement 1.5대로 구조로 프로필을 정하므로, 같은 구조 상품은 검증을 통과하면 자동으로 계산기가 켜질 수 있다.
- 독립 grounding 검증 단계(답변 문장 단위 근거 검증), Automated Reasoning.
- 브라우저 자동화, 병원 위치 기반 검색, 진료비 세부내역서 OCR.
- 서버 DB(EC2) 반영. 간편가입 Chroma 반영은 기존 주간 약관 확인·승인 절차를 따른다.


## 확정된 결정 (2026-10-30, 사용자 승인)

1. **Q1 KCD 데이터 방식 → 상병마스터 파일 로컬 조회.** 심평원 상병마스터 파일(KCD_Master_File)을 받아 로컬 색인을 만들고, 병명은 외부로 보내지 않는다. 파일은 git에 올리지 않고 `data/kcd/` 아래에 두며 서버가 원본이다(Requirement 10.12). 파일 이용 조건은 설계 단계에서 확인을 시도했으나 hira.or.kr에 연결되지 않아 확인하지 못했다. 구현 전에 운영자가 직접 확인하고, 이용할 수 없으면 질병정보서비스 API로 대체한다(대체 시 Requirement 10.7 전송 제한 적용). 관련 기준: Requirement 10.1, 10.7, 10.8, 10.10, 10.12, 11.3, 11.4.
2. **Q2 병원정보 API → 개발계정으로 시작.** 개발계정(자동승인, 트래픽 10,000)으로 운영한다. 앱 하루 질문 한도가 100개(`DAILY_QUESTION_LIMIT`)이고 한 질문에서 `classify_hospital`은 많아야 몇 번 호출되므로 충분하다. 운영계정 신청은 별도 작업이다. 관련 기준: Requirement 9.6.
3. **Q3 `identify_generation` 차이 안내 → 약관 근거만.** 금융감독원 등 외부 자료의 세대별 설명은 넣지 않는다. 관련 기준: Requirement 5.4(유지, 문구만 명확히 함).
4. **Q4 간편가입 비급여 도수치료·주사·MRI → 설계에서 원문 확인 후 결정.** 설계 단계 원문 확인 결과 면책으로 정했다. 간편가입 보통약관 제4조가 네 보장종목 모두에서 비급여 도수치료·체외충격파·증식치료, 비급여 주사료(항암제·항생제·희귀의약품 제외), 비급여 MRI/MRA를 보상하지 않는다(위 "확인한 사실" 참고). 계산기는 이 금액을 보장대상의료비에 넣지 않고 0원으로 표시하며, 면책 사실은 인용문으로 검증한다. 관련 기준: Requirement 1.8, 2.12, 2.13.
5. **Q5 청구 기한 → 치료일 기준 참고 날짜.** `claim_guide`는 치료일 + 소멸시효 기간으로 참고 날짜를 계산하고, "약관상 소멸시효는 청구권을 행사할 수 있는 때부터 진행하므로 실제 기산일은 사안마다 다를 수 있음"을 함께 안내한다. 호출 인자 이름은 `treatment_date`(치료일)로 둔다. 관련 기준: Requirement 6.4, 6.6.

### 설계 단계에서 함께 고친 기준

- Requirement 10.3, 10.4, 10.9, 10.11: 간편가입 제4조에 단일 코드(E66, N39.3 등)와 면책 예외 목록("다만, … 보상합니다")이 있어, 단일 코드·소수점 코드 비교와 면책/예외 구분을 추가했다. 예외 코드를 면책으로 안내하면 오안내가 되기 때문이다.
