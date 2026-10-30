---
name: 보험사 추가 (add-insurer)
description: 새 보험사를 주간 약관 확인에 추가하는 절차. 공시실 자동 수집 가능 여부를 판별해 자동(ENABLED) 또는 수동(MANUAL)으로 등록한다.
---

# 보험사 추가 절차

새 보험사의 실손 약관을 서비스에 포함할 때 따른다. 핵심 판단: **공시실을 자동으로 긁을 수 있는가**.

## 1. 자동 수집 가능 여부 정찰
1. 보험사 공시실 URL을 찾는다(상품공시/약관 목록 페이지).
2. `curl` 또는 요청으로 목록 페이지를 받아 **서버가 완성된 HTML을 주는지** 확인한다.
   - HTML에 상품 목록·약관 링크가 들어 있다 → 자동 가능(KB 방식).
   - 빈 껍데기만 오고 JavaScript로 그린다 / 봇을 막는다(연결 실패) → 자동 불가.
3. 참고: 삼성화재·DB손보는 JS 렌더링, 현대해상은 봇 차단으로 **자동 불가**였다. KB만 자동 가능.

## 2a. 자동 가능이면: 보험사 모듈 작성
4. `boheom_mate/integrations/insurers/`에 `kb.py`를 본떠 모듈을 만든다.
   필수: `COMPANY`, `fetch() -> list[Listing]`, `download(listing, dest)`. 목록 실패 시 반드시 `ListingError`.
5. `insurers/__init__.py`의 `ENABLED`에 등록한다.
6. 고정 HTML 샘플로 파싱 테스트를 만든다(`tests/test_kb_listing.py` 방식, 네트워크 없이).

## 2b. 자동 불가면: MANUAL 등록
7. `insurers/__init__.py`의 `MANUAL`에 보험사 이름을 추가하고, 왜 자동 불가인지 주석으로 남긴다.
8. 운영자는 공시실에서 PDF를 받아 `data/inbox/`에 `보험사__상품이름__판.pdf`(판=YY.MM)로 넣는다.
   주간 확인이 분할·점검·Slack 알림 → 승인 → DB 저장까지 처리한다.

## 3. 공통: 브라우저 자동화는 쓰지 않는다
- Selenium/Playwright는 의존성·유지보수·공시실 접근 정책 문제로 도입하지 않는다(결정 사항).

## 4. 검증
9. `.venv/bin/python tests/test_weekly_check.py` 등 관련 테스트가 통과하는지 확인한다.
10. 가짜 Slack·temp dir로 주간 확인을 모의 실행해 새 보험사가 요약에 바르게 뜨는지 본다.
