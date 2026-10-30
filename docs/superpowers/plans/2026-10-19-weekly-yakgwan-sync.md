# 주간 실손 약관 자동 수집·반영 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매주 월요일 9시에 EC2가 보험사 공시실에서 새 실손 약관을 찾아 받고, 조항 나누기를 점검해 Slack에 올리고, 운영자가 "승인" 답글을 달면 사이트에 반영한다.

**Architecture:** 기존 평평한 파일 구조를 따른다. 상품 목록은 코드(`products.py`)에서 서버 소유 데이터 파일(`state/products.json`)로 옮긴다. 새 모듈은 점검(`split_check.py`), 상태 기록(`sync_state.py`), Slack(`slack_notify.py`), 보험사별 목록 읽기(`insurers/`)다. 실행 파일은 주간 확인(`weekly_check.py`)과 승인 반영(`apply_approved.py`) 두 개이고, 둘 다 systemd timer로 돈다. 반영은 "사이트 멈춤 → 임베딩·저장 → 상품 등록 → 사이트 시작" 순서로 한다. Chroma 파일에 두 프로세스가 동시에 쓰지 않게 하려는 것이다.

**Tech Stack:** Python 3.12, 기존 `ingest.py`(pypdf, OpenAI 임베딩, Chroma), Streamlit, `slack_sdk`, `requests`, systemd timer(Ubuntu 24.04)

**Spec:** `docs/superpowers/specs/2026-10-19-weekly-yakgwan-sync-design.md`

**작업 브랜치:** 지금 `main`이다. 시작 전에 `feature/weekly-yakgwan-sync` 브랜치를 만든다.

## Global Constraints

- 대상은 실손의료보험만. 판매 중 상품의 최신 판만. 단체 실손·실손 특약 제외.
- 운영자 승인("승인" 답글) 없이 `chroma_db/`나 상품 목록을 바꾸지 않는다. "정상"이든 "확인 필요"든 같다.
- "확인 필요"를 "정상"으로 표시하지 않는다. "목록 읽기 실패"를 "새 약관 없음"으로 표시하지 않는다.
- 같은 약관(회사 + 상품 + 판)을 두 번 알리지 않는다.
- 새 판이 나와도 옛 판 조각과 상품 항목을 지우지 않는다.
- 공시실 접근은 주 1회, 보험사당 몇 번의 요청만. `robots.txt`나 이용 조건이 막으면 자동 확인하지 않는다. t3.micro에 Chromium/Playwright를 올리지 않는다.
- 비밀값(`OPENAI_API_KEY`, `SLACK_BOT_TOKEN`)은 `.env`에만 둔다.
- LangChain 같은 프레임워크를 쓰지 않는다. 운영자가 면접에서 설명할 수 있게 단순하게 유지한다.
- 테스트는 기존 방식(`tests/test_calculator.py`)을 따른다: pytest 없이 `assert`만 쓰는 스크립트이고, `.venv/bin/python tests/test_<이름>.py`로 실행해 마지막에 `OK`를 출력한다.
- `state/`, `data/`, `chroma_db/`는 서버가 원본이다. 코드 배포가 이것들을 덮어쓰면 안 된다.

## Review Focus

- **승인 답글의 변형:** "승인합니다", " 승인 ", 운영자가 아닌 사람의 "승인"이 오는 경우다. 운영자(`SLACK_OPERATOR_ID`)가 단 답글 중 앞뒤 공백을 뺀 글이 정확히 "승인"인 것만 승인으로 본다. → Task 5 테스트
- **같은 주에 수동으로 다시 실행:** 새 약관 메시지가 다시 나가면 안 된다. 요약에는 "승인 대기"로만 나온다. → Task 7 테스트
- **반영 도중 실패(임베딩 오류 등):** 사이트가 반드시 다시 켜져야 한다. 항목은 "failed"로 남기고 Slack에 이유를 알린다. → Task 8 테스트
- **상품 이름에 `/`나 공백 같은 특수문자가 있는 경우:** 파일 이름이 안전하게 바뀌어 `data/` 밖에 쓰이지 않아야 한다. → Task 7 테스트
- **공시실 화면이 바뀌어 목록이 0건으로 읽히는 경우:** "없음"이 아니라 "목록 읽기 실패"로 보고한다. → Task 6 테스트

---

### Task 1: 상품 목록을 데이터 파일로 옮기고 판 고르기 추가

**Files:**
- Create: `products_seed.json`
- Modify: `products.py` (전체), `app.py:113-133` (`pick_product`), `.gitignore`
- Test: `tests/test_products.py`

**Interfaces:**
- Produces:
  - 상품 항목은 `dict`이고 키는 `company, category, product, edition, source`(모두 `str`)다. `product`에는 판을 넣지 않는다(예: `"KB손보 실손의료비보장보험(무배당)"`, `edition="26.09"`).
  - `products.DATA_FILE = BASE_DIR / "state" / "products.json"`, `products.SEED_FILE = BASE_DIR / "products_seed.json"`
  - `products.load(path=DATA_FILE) -> list[dict]`: 파일이 없으면 시드를 읽는다.
  - `products.search(company=ALL, category=ALL, keyword="", items=None) -> list[dict]`: (company, product)마다 최신 판 하나만 돌려준다.
  - `products.editions(entry, items=None) -> list[dict]`: 같은 company·product의 판들을 최신 판부터 돌려준다.
  - `products.add(entry: dict, path=DATA_FILE) -> None`: (company, product, edition)이 같으면 `source`만 바꾸고, 아니면 추가한다. 파일이 없으면 현재 목록(시드)에 더해 새로 쓴다.
  - `products.base_name(name: str) -> str`: 끝의 `(YY.MM)`과 앞뒤 공백을 뗀다.
  - `products.label(entry) -> str`: 상품 이름을 돌려준다.
  - `COMPANIES`, `CATEGORIES`, `ALL`은 그대로 둔다. `PRODUCTS = load()`

- [ ] **Step 1: 시드 파일 작성.** `products_seed.json`에는 지금 DB에 있는 2개만 넣는다. `source`가 None이던 KB 3개는 1주차 주간 확인에서 새 약관으로 올라오므로 넣지 않는다.

```json
[
  {"company": "KB손해보험", "category": "실손의료보험", "product": "KB손보 실손의료비보장보험(무배당)", "edition": "26.09", "source": "KB_실손의료비보장보험_26.09_약관.pdf"},
  {"company": "KB손해보험", "category": "실손의료보험", "product": "KB손보 간편가입 실손의료비보장보험", "edition": "26.09", "source": "KB_간편가입실손의료비보장보험_26.09_약관.pdf"}
]
```

- [ ] **Step 2: 실패하는 테스트 작성** (`tests/test_products.py`, 임시 폴더 사용)

```python
items = [
    {"company": "KB손해보험", "category": "실손의료보험", "product": "A", "edition": "26.09", "source": "a1.pdf"},
    {"company": "KB손해보험", "category": "실손의료보험", "product": "A", "edition": "27.01", "source": "a2.pdf"},
    {"company": "삼성화재", "category": "실손의료보험", "product": "B", "edition": "26.10", "source": "b.pdf"},
]
assert [e["edition"] for e in products.search(items=items) if e["product"] == "A"] == ["27.01"]
assert [e["edition"] for e in products.editions(items[0], items=items)] == ["27.01", "26.09"]
assert products.base_name(" KB손보 실손의료비보장보험(무배당)(26.09) ") == "KB손보 실손의료비보장보험(무배당)"
# add: 없는 파일 → 시드 + 새 항목, 같은 키 다시 add → 개수 그대로, source만 바뀜
products.add({**items[2]}, path=tmp / "products.json")
assert len(products.load(tmp / "products.json")) == len(products.load(products.SEED_FILE)) + 1
products.add({**items[2], "source": "b2.pdf"}, path=tmp / "products.json")
assert [e["source"] for e in products.load(tmp / "products.json") if e["product"] == "B"] == ["b2.pdf"]
```

- [ ] **Step 3: 실행해서 실패 확인.** `.venv/bin/python tests/test_products.py` → `AttributeError`(`base_name` 없음)
- [ ] **Step 4: `products.py` 구현.** 위 Interfaces대로 구현한다. 파일 쓰기는 임시 파일에 쓴 뒤 `os.replace`로 바꿔치기한다. 판 정렬은 문자열 내림차순이다(`YY.MM` 형식이라 그대로 맞다).
- [ ] **Step 5: `app.py`의 `pick_product` 수정.** 상품 selectbox 다음에 `products.editions(product)`가 2개 이상이면 `st.selectbox("약관 판 (가입한 시점)", ..., format_func=lambda e: f'{e["edition"]}판')`을 보여 주고, 고른 판의 `source`를 쓴다. 판이 하나면 selectbox를 보이지 않는다. "준비 중" 분기(`source not in sources`)는 유지한다.
- [ ] **Step 6: `.gitignore`에 `state/` 추가**
- [ ] **Step 7: 테스트 통과 확인.** `.venv/bin/python tests/test_products.py` → `OK`. `.venv/bin/python tests/test_calculator.py` → 기존처럼 통과.
- [ ] **Step 8: 화면 확인.** `.venv/bin/streamlit run app.py`를 실행해 "내 보험 고르기"에 KB 2개가 보이고 상담이 되는지 확인한다.
- [ ] **Step 9: Commit** `git add products.py products_seed.json app.py .gitignore tests/test_products.py && git commit -m "상품 목록을 데이터 파일로 옮기고 판 고르기 추가"`

### Task 2: 공개 사이트에서 "약관 넣기" 탭 숨기기

**Files:**
- Modify: `app.py:41` (탭 생성), `app.py:252-253`, `.env.example`

**Interfaces:**
- Produces: 환경변수 `SHOW_INGEST_TAB`. `"1"`일 때만 "약관 넣기" 탭이 보인다. 기본값은 숨김이고, 서버 `.env`에는 넣지 않는다.

- [ ] **Step 1: 구현.** `SHOW_INGEST = os.getenv("SHOW_INGEST_TAB") == "1"`로 정한다. 참이면 탭 두 개, 거짓이면 "상담하기" 하나만 만들고 `render_ingest()`를 호출하지 않는다. 앱이 `.env`를 읽지 않는다면 `load_dotenv`를 `app.py` 맨 위에서 호출한다(`rag.py`에서 이미 읽는지 먼저 확인).
- [ ] **Step 2: `.env.example`에** `# SHOW_INGEST_TAB=1   # 로컬에서만. 서버에는 넣지 않는다` 줄을 추가한다.
- [ ] **Step 3: 확인.** `.env`에 값 없이 실행하면 탭이 "상담하기" 하나만 보인다. `SHOW_INGEST_TAB=1 .venv/bin/streamlit run app.py`로 실행하면 두 개가 보인다.
- [ ] **Step 4: Commit** `git commit -am "배포판에서 약관 넣기 탭 숨기기"`

### Task 3: 조항 나누기 점검

**Files:**
- Create: `split_check.py`
- Test: `tests/test_split_check.py`

**Interfaces:**
- Consumes: `ingest.split_articles`의 반환 항목 `{"section","article_no","title","text"}`, `ingest.MAIN`, `ingest.GUIDE`
- Produces: `split_check.check(articles: list[dict]) -> dict`. 반환값은 `{"ok": bool, "reasons": list[str], "sections": dict[str, int], "suspects": list[str]}`이다. `sections`는 구역별 조각 수이고, `suspects`는 문제 근처 조항의 "구역 제N조(제목): 첫 줄 40자"이며 최대 5개다. `split_check.MIN_ARTICLES: int`

점검 규칙(하나라도 걸리면 `ok=False`):
1. 조항이 0개면 `"텍스트를 추출하지 못함"`
2. `보통약관` 구역이 없거나, 안내자료를 뺀 모든 조각이 한 구역에 있으면 `"구역을 찾지 못함"`
3. 구역마다 `제N조`/`제N조의M` 형식의 조항 번호만 순서대로 본다. `_split_long` 때문에 같은 번호가 **연달아** 나오는 건 정상이다. 그 밖의 경우는 이렇게 보고한다.
   - 번호가 건너뛰면 `"{구역}: 제{a}조 다음 제{b}조"` (번호 빠짐)
   - 앞에 나온 번호가 떨어진 곳에서 다시 나오면 `"{구역}: 제{n}조 겹침"`
   - 번호가 작아지면 `"{구역}: 제{a}조 다음 제{b}조 (순서 뒤집힘)"`
   - `제N조의M`은 제N조와 제N+1조 사이에서 정상이다.
4. 조각 수가 `MIN_ARTICLES`보다 적으면 `"조항 수가 너무 적음({n}개)"`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def a(section, no):
    return {"section": section, "article_no": no, "title": "t", "text": f"{no} 본문"}

MAIN = "보통약관"; SP = "특별약관1(상급병실료차액)"
base = [a(MAIN, f"제{i}조") for i in range(1, 6)] + [a(SP, f"제{i}조") for i in range(1, 4)]
ORIGINAL_MIN = split_check.MIN_ARTICLES
split_check.MIN_ARTICLES = 3  # 작은 예제로 시험하려고 낮춘다
r = check(base)
assert r["ok"] and r["sections"] == {MAIN: 5, SP: 3}
assert "텍스트를 추출하지 못함" in check([])["reasons"]
assert "구역을 찾지 못함" in check([a(MAIN, f"제{i}조") for i in range(1, 9)])["reasons"]
gap = [a(MAIN, "제1조"), a(MAIN, "제2조"), a(MAIN, "제4조")] + base[5:]
assert "보통약관: 제2조 다음 제4조" in check(gap)["reasons"]
dup = [a(MAIN, "제1조"), a(MAIN, "제2조"), a(MAIN, "제3조"), a(MAIN, "제2조")] + base[5:]
assert any("제2조 겹침" in x for x in check(dup)["reasons"])
cont = [a(MAIN, "제1조"), a(MAIN, "제1조"), a(MAIN, "제2조"), a(MAIN, "제2조의2"), a(MAIN, "제3조")] + base[5:]
assert check(cont)["ok"]
# 실제 KB 기본 약관(있을 때만): 통과해야 한다
pdf = ingest.DATA_DIR / "KB_실손의료비보장보험_26.09_약관.pdf"
if pdf.exists():
    split_check.MIN_ARTICLES = ORIGINAL_MIN
    real = check(ingest.split_articles(ingest.extract_text(pdf))[0])
    assert real["ok"], real["reasons"]
```

- [ ] **Step 2: 실행해서 실패 확인.** `.venv/bin/python tests/test_split_check.py` → `ModuleNotFoundError: split_check`
- [ ] **Step 3: `check` 구현** (규칙은 위와 같다)
- [ ] **Step 4: `MIN_ARTICLES` 정하기.** KB 약관 5개 각각의 조각 수를 출력한다(`for p in ingest.list_pdfs(): print(p.name, len(ingest.split_articles(ingest.extract_text(p))[0]))`). 가장 작은 값의 절반을 `MIN_ARTICLES`로 정하고, 근거를 주석 한 줄로 남긴다. 5개에 대한 `check` 결과도 출력해 기록해 둔다. 기본 약관은 정상이어야 하고, 간편가입은 "구역을 찾지 못함"으로 걸리는 게 예상된 동작이다(CLAUDE.md 참고).
- [ ] **Step 5: 테스트 통과 확인** → `OK`
- [ ] **Step 6: Commit** `git add split_check.py tests/test_split_check.py && git commit -m "조항 나누기 점검 추가"`

### Task 4: 실행 기록과 승인 대기 상태

**Files:**
- Create: `sync_state.py`
- Test: `tests/test_sync_state.py`

**Interfaces:**
- Consumes: `products.load()`, `products.base_name()`
- Produces:
  - `sync_state.STATE_FILE = BASE_DIR / "state" / "sync.json"`, `sync_state.LOG_FILE = BASE_DIR / "state" / "runs.log"`
  - `key(company: str, product: str, edition: str) -> str`: `f"{company}|{products.base_name(product)}|{edition}"`. 비교할 때는 상품 이름의 공백을 모두 뺀다.
  - `load(path=STATE_FILE) -> dict`: `{"known": list[str], "pending": list[dict]}`를 돌려준다. 파일이 없으면 `known`을 `products.load()`의 모든 항목 키로 채운다(이미 사이트에 있는 약관이므로 다시 알리지 않는다).
  - `save(state: dict, path=STATE_FILE) -> None`: 임시 파일에 쓴 뒤 `os.replace`
  - 대기 항목 `dict`: `key, company, category, product, edition, pdf`(`data/` 안의 파일 이름), `check`(Task 3의 결과), `slack_ts`(`str|None`), `status`(`"pending"|"applied"|"failed"`), `created`(ISO 날짜), `error`(`str|None`)
  - `log(event: dict, path=LOG_FILE) -> None`: 시각을 붙여 JSON 한 줄을 덧붙인다.

- [ ] **Step 1: 실패하는 테스트 작성.** 임시 폴더에서 다음을 확인한다.
  - 파일이 없으면 `load()`의 `known`에 `key("KB손해보험","KB손보 실손의료비보장보험(무배당)","26.09")`가 들어 있다.
  - `key("A","KB 실손(26.09)","26.09") == key("A","KB실손","26.09")`
  - save 후 load한 결과가 같다.
  - `log`를 두 번 부르면 줄이 2개 생기고, 각 줄은 `json.loads`로 읽힌다.
- [ ] **Step 2: 실패 확인** → `ModuleNotFoundError`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** → `OK`
- [ ] **Step 5: Commit** `git add sync_state.py tests/test_sync_state.py && git commit -m "주간 확인 상태 기록 추가"`

### Task 5: Slack 보내기와 승인 읽기

**Files:**
- Create: `slack_notify.py`
- Modify: `requirements.txt` (`slack_sdk`, `requests` 추가. pip가 설치한 정확한 버전으로 고정), `.env.example`
- Test: `tests/test_slack_notify.py`

**Interfaces:**
- Consumes: 대기 항목 dict(Task 4)
- Produces:
  - `class Slack(client=None, channel=None, operator=None)`: 인자가 없으면 각각 `slack_sdk.WebClient(token=SLACK_BOT_TOKEN)`, `SLACK_CHANNEL_ID`, `SLACK_OPERATOR_ID` 환경변수에서 가져온다.
  - `Slack.post(text: str, thread_ts: str | None = None) -> str`: `chat_postMessage`를 호출하고 `ts`를 돌려준다.
  - `Slack.approved(ts: str) -> bool`: `conversations_replies(channel, ts)`의 답글(부모 메시지 제외) 중 `user == operator`이고 `text.strip() == "승인"`인 것이 있으면 참이다.
  - `item_message(item: dict) -> str`: 요약 결과(`check`)를 담은 약관별 메시지다.
  - `summary_message(results: list[dict], waiting: list[dict], today: str) -> str`: `results` 항목은 `{"company": str, "status": "new"|"none"|"failed"|"manual", "count": int, "error": str|None}`이고, `waiting`은 이전 주부터 남은 대기 항목이다.

메시지 문구(고정):
- 약관별 메시지 첫 줄: `[새 약관] {company} · {product} · {edition}판`. 그 아래에 조각 수, 구역별 조각 수가 온다.
  - 정상: `점검: 정상`
  - 확인 필요: `점검: 확인 필요`, 그 아래에 `- {reason}` 줄들과 `의심 조항:` 아래 suspects를 단다.
  - 마지막 줄: `반영하려면 이 메시지에 "승인"이라고 답글을 달아 주세요. 반영 중 1분 정도 사이트가 멈춥니다.`
- 요약 첫 줄: `[주간 약관 확인] {today}`. 그 아래 보험사별 한 줄이다.
  - `{company}: 새 약관 {count}건` / `새 약관 없음` / `목록 읽기 실패 ({error})` / `자동 확인 불가 (PDF를 data/inbox/에 직접 넣어 주세요)`
  - 대기 항목이 있으면 `승인 대기 {n}건:`과 항목 목록을 단다.

- [ ] **Step 1: 실패하는 테스트 작성.** 가짜 client는 `chat_postMessage`와 `conversations_replies`를 흉내 낸다.

```python
fake = FakeClient(replies=[{"user": "U1", "ts": "1"}, {"user": "U2", "text": "승인"}, {"user": "U1", "text": "승인합니다"}])
s = Slack(client=fake, channel="C1", operator="U1")
assert s.approved("1") is False                       # 다른 사람의 "승인", 운영자의 "승인합니다"
fake.replies.append({"user": "U1", "text": " 승인 \n"})
assert s.approved("1") is True
assert s.post("hi", thread_ts="1") == fake.last_ts and fake.calls[-1]["thread_ts"] == "1"
ok_item = {"company": "KB손해보험", "product": "P", "edition": "26.09", "check": {"ok": True, "reasons": [], "sections": {"보통약관": 10}, "suspects": []}}
assert "점검: 정상" in item_message(ok_item) and "승인" in item_message(ok_item)
bad = {**ok_item, "check": {"ok": False, "reasons": ["구역을 찾지 못함"], "sections": {"보통약관": 10}, "suspects": ["보통약관 제1조(목적): ..."]}}
assert "확인 필요" in item_message(bad) and "구역을 찾지 못함" in item_message(bad) and "정상" not in item_message(bad)
msg = summary_message([{"company": "KB손해보험", "status": "failed", "count": 0, "error": "timeout"}], [], "2026-10-26")
assert "목록 읽기 실패" in msg and "없음" not in msg
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현.** `pip install slack_sdk requests`를 실행하고 `requirements.txt`에 `==버전`으로 고정한다. `.env.example`에 `SLACK_BOT_TOKEN=`, `SLACK_CHANNEL_ID=`, `SLACK_OPERATOR_ID=`를 추가한다.
- [ ] **Step 4: 통과 확인** → `OK`
- [ ] **Step 5: Commit** `git add slack_notify.py requirements.txt .env.example tests/test_slack_notify.py && git commit -m "Slack 알림과 승인 읽기 추가"`

### Task 6: 보험사 목록 읽기 틀과 KB손해보험

**Files:**
- Create: `insurers/__init__.py`, `insurers/kb.py`, `tests/fixtures/kb_listing.<html|json>`
- Test: `tests/test_kb_listing.py`

**Interfaces:**
- Produces:
  - `insurers.Listing`(dataclass): `company: str, product: str, edition: str, pdf_url: str, category: str = "실손의료보험"`. `product`에는 판을 넣지 않는다.
  - `insurers.ListingError(Exception)`
  - `insurers.ENABLED: dict[str, module]`: 자동 확인할 보험사다. 처음에는 `{"KB손해보험": kb}`
  - `insurers.MANUAL: list[str]`: 자동 확인 불가로 정한 보험사다. 처음에는 `[]`
  - 보험사 모듈마다:
    - `COMPANY: str`
    - `parse(raw: str) -> list[Listing]`: 네트워크를 쓰지 않는다. 실손·판매 중·최신 판만 남기고, 0건이면 `ListingError`를 낸다.
    - `fetch() -> list[Listing]`: 가져오기와 `parse`를 함께 한다. 실패하면 `ListingError`를 낸다.
    - `download(listing: Listing, dest: Path) -> None`: PDF가 아니면(`%PDF`로 시작하지 않으면) `ListingError`를 낸다.
  - `requests` 호출에는 `timeout=30`과 `User-Agent: boheom-mate-weekly-check (+https://boheommate.duckdns.org)`를 붙인다.

- [ ] **Step 1: 접근 허용 확인.** `https://www.kbinsure.co.kr/robots.txt`와 공시실 이용 조건을 확인한다. 자동 접근을 막고 있으면 여기서 멈추고 운영자에게 알린다. 그 경우 KB를 `MANUAL`로 옮기고 이 Task의 `kb.py`는 만들지 않는다. 대신 Task 9 서버 설치 때 받아 둔 KB 3개(계약전환용, 다이렉트, 다이렉트간편)를 `data/inbox/`에 이름 규칙(`KB손해보험__상품이름__26.09.pdf`)대로 넣어 1주차 승인 대기로 올라오게 한다.
- [ ] **Step 2: 공시실 구조 조사.** 공시실(`https://www.kbinsure.co.kr/CG802030001.ec`)의 실손 약관 목록이 HTML에 들어 있는지, 아니면 브라우저가 따로 호출하는 공개 JSON 주소가 있는지 확인한다. 헤드리스 브라우저가 필요하면 Step 1처럼 `MANUAL`로 돌리고 운영자에게 알린다. 응답 하나를 `tests/fixtures/`에 저장한다.
- [ ] **Step 3: 실패하는 테스트 작성**

```python
items = kb.parse(FIXTURE.read_text())
names = {(i.product, i.edition) for i in items}
assert ("KB손보 실손의료비보장보험(무배당)", "26.09") in names        # 시드 이름과 정확히 같아야 한다
assert ("KB손보 간편가입 실손의료비보장보험", "26.09") in names
assert all(i.pdf_url.startswith("http") for i in items)
assert all("단체" not in i.product for i in items)
# 판매중지·과거 판·실손 아닌 상품은 빠진다 (fixture에 실제로 있는 예 하나씩으로 assert)
try:
    kb.parse(EMPTY_OR_CHANGED_FIXTURE); assert False
except ListingError:
    pass
```

- [ ] **Step 4: 실패 확인 → 구현 → 통과 확인.** 공시실이 보여 주는 상품 이름이 `products_seed.json`의 이름과 다르면, 공시실 표기에 맞춰 **시드를 고친다**. 그래야 이미 있는 2개가 새 약관으로 다시 올라오지 않는다.
- [ ] **Step 5: 실제 확인(네트워크 1회).** `.venv/bin/python -c "import insurers.kb as k; [print(x) for x in k.fetch()]"`를 실행한다. 결과에 시드 2개와 계약전환용, 다이렉트, 다이렉트간편 3개가 26.09판으로 보이면 된다.
- [ ] **Step 6: Commit** `git add insurers tests/test_kb_listing.py tests/fixtures products_seed.json && git commit -m "KB손해보험 공시실 실손 약관 목록 읽기"`

### Task 7: 주간 확인 실행 파일

**Files:**
- Create: `weekly_check.py`
- Test: `tests/test_weekly_check.py`

**Interfaces:**
- Consumes: `insurers.ENABLED/MANUAL/ListingError/Listing`, `split_check.check`, `sync_state.load/save/log/key`, `slack_notify.Slack/item_message/summary_message`, `ingest.extract_text/split_articles/DATA_DIR`
- Produces:
  - `weekly_check.INBOX = ingest.DATA_DIR / "inbox"`
  - `weekly_check.pdf_name(company, product, edition) -> str`: `"{company}_{product}_{edition}.pdf"`이고, 공백·`/`·`\`·`:`는 `_`로 바꾼다.
  - `weekly_check.run(enabled=insurers.ENABLED, manual=insurers.MANUAL, slack=None, state_path=sync_state.STATE_FILE, data_dir=ingest.DATA_DIR, inbox=INBOX, split=None, today=None) -> list[dict]`: `split(path) -> list[articles]`(기본값은 추출 + 분할)을 쓰고, 요약용 `results`를 돌려준다.
  - `main()`: `run()` 실행, 예외가 나면 Slack에 `[주간 약관 확인] 실행 실패: {예외}`를 보내고 종료 코드 1로 끝난다.
  - 수동 입력 파일 이름 규칙: `data/inbox/{보험사}__{상품 이름}__{판}.pdf` (밑줄 두 개)

흐름:
1. 상태를 불러온다.
2. `enabled`의 보험사마다 `fetch()`를 실행한다. `ListingError`가 나면 `failed`로 기록한다.
3. 목록에서 `key`가 `known`에 없는 항목마다:
   1. `download`로 `data_dir/pdf_name(...)`에 받는다. 실패하면 이유를 결과에 남기고 `known`에 넣지 않는다(다음 주에 다시 시도).
   2. 점검하고, 대기 항목을 만들고, `known`에 추가한다.
4. `inbox`의 PDF도 같은 방식으로 처리한다. 이름 규칙이 맞으면 `data_dir`로 옮기고, 맞지 않으면 그대로 두고 요약에 `inbox 파일 이름 규칙 오류: {파일명}`을 덧붙인다.
5. `manual` 보험사는 `manual` 상태로 기록한다.
6. **요약 메시지 1건**을 보낸 뒤 이번에 새로 생긴 항목마다 약관별 메시지를 보내고, 그 `ts`를 항목에 저장한다.
7. 상태를 저장하고 `log`를 남긴다.

`slack.post`가 실패하면 그 항목은 `slack_ts=None`으로 남긴다. 다음 실행 때 `slack_ts`가 없는 대기 항목은 약관별 메시지를 다시 보낸다.

- [ ] **Step 1: 실패하는 테스트 작성.** 가짜 보험사 모듈(`fetch`/`download`), 가짜 Slack(보낸 글 목록), 가짜 `split`, 임시 폴더를 쓴다.
  - 새 항목 1개를 실행하면: `pending` 1개, `known`에 키가 들어감, Slack 글 2개(요약에 "새 약관 1건", 약관별 메시지), 항목의 `slack_ts`가 채워짐
  - 같은 조건으로 다시 실행하면: 약관별 메시지가 없고, 요약에 "승인 대기 1건"이 나옴
  - `fetch`가 `ListingError("x")`를 내면: 요약에 "목록 읽기 실패"가 있고 그 회사 줄에 "없음"이 없음
  - `download`가 실패하면: `known`에 들어가지 않고, 다음 실행에서 다시 시도함
  - `pdf_name("A", "B/C 실손", "26.09") == "A_B_C_실손_26.09.pdf"`이고, 파일은 `data_dir` 안에 생김
  - inbox의 `삼성화재__무배당 실손__26.10.pdf`는 처리되어 `data_dir`로 옮겨짐. `잘못된이름.pdf`는 inbox에 남고 요약에 오류 줄이 나옴
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** → `OK`
- [ ] **Step 5: Commit** `git add weekly_check.py tests/test_weekly_check.py && git commit -m "주간 약관 확인 실행 파일 추가"`

### Task 8: 승인된 약관 반영

**Files:**
- Create: `apply_approved.py`
- Test: `tests/test_apply_approved.py`

**Interfaces:**
- Consumes: `sync_state`, `slack_notify.Slack`, `products.add`, `ingest.build_index(articles, source) -> int`, `ingest.extract_text/split_articles`
- Produces:
  - `apply_approved.site(action: str) -> None`: `subprocess.run(["sudo", "systemctl", action, "boheom-mate"], check=True)`
  - `apply_approved.run(slack=None, state_path=sync_state.STATE_FILE, products_path=products.DATA_FILE, data_dir=ingest.DATA_DIR, build_index=ingest.build_index, split=None, site=site) -> list[str]`: 반영한 키 목록을 돌려준다.
  - `main()`

흐름:
1. `status == "pending"`이고 `slack_ts`가 있는 항목 중 `slack.approved(ts)`가 참인 것만 모은다.
2. 하나도 없으면 아무것도 하지 않는다. 사이트도 건드리지 않는다.
3. 있으면 `site("stop")`을 한 번 호출한다.
4. 항목마다:
   1. `build_index(split(pdf), item["pdf"])`
   2. `products.add({company, category, product, edition, source: pdf})`
   3. `status = "applied"`로 바꾸고, 스레드에 `반영 완료 · DB 전체 {n}조각`을 단다.
   4. 실패하면 `status = "failed"`, `error`를 남기고 스레드에 `반영 실패: {error}`를 단 뒤 다음 항목으로 넘어간다.
5. 마지막에 반드시(`finally`) `site("start")`를 호출한다.
6. 상태를 저장하고 `log`를 남긴다.

- [ ] **Step 1: 실패하는 테스트 작성.** 가짜 Slack(`approved`가 정해진 ts에만 참), 가짜 `build_index`, 가짜 `site`(호출 기록)를 쓴다.
  - 승인된 항목 1개 → `build_index`가 그 pdf 이름으로 호출되고, products 파일에 항목이 생기고, status는 `applied`, 스레드에 "반영 완료", `site` 호출은 `["stop", "start"]`
  - 승인 안 됨 → `site` 호출 없음, status 그대로
  - `build_index`가 예외를 내면 → status `failed`, 스레드에 "반영 실패", `site` 호출은 여전히 `["stop", "start"]`
  - 이미 `applied`인 항목은 다시 반영하지 않음
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** → `OK`
- [ ] **Step 5: Commit** `git add apply_approved.py tests/test_apply_approved.py && git commit -m "Slack 승인된 약관 반영 추가"`

### Task 9: 서버 설치(타이머, 배포 스크립트, 문서)

**Files:**
- Create: `deploy/weekly-check.service`, `deploy/weekly-check.timer`, `deploy/apply-approved.service`, `deploy/apply-approved.timer`, `deploy/push.sh`, `docs/slack-setup.md`
- Modify: `deploy/setup.sh`, `CLAUDE.md`(배포·현재 상태), `README.md`(약관 넣기 설명), `docs/업무절차.md`(자동화 후 절차 덧붙이기)

**Interfaces:**
- 서비스는 `User=ubuntu`, `WorkingDirectory=/home/ubuntu/boheom-mate`, `Type=oneshot`이다. 실행 명령은 각각 `.venv/bin/python weekly_check.py`, `.venv/bin/python apply_approved.py`이다.
- `weekly-check.timer`: `OnCalendar=Mon *-*-* 09:00:00 Asia/Seoul`, `Persistent=true`
- `apply-approved.timer`: `OnCalendar=*:0/5`(5분마다)
- `deploy/push.sh`: 맥에서 `rsync -avz --exclude-from` 방식으로 코드만 올린다. 제외 목록은 `.venv/ .git/ __pycache__/ .env chroma_db/ data/ state/ questions.db`이고, 대상은 `ubuntu@3.36.241.30:/home/ubuntu/boheom-mate/`, 키는 `~/.ssh/boheom-mate.pem`이다.

- [ ] **Step 1: unit 파일 4개와 `push.sh` 작성.** `setup.sh`에 다음을 추가한다: unit 복사, `systemctl enable --now weekly-check.timer apply-approved.timer`, `mkdir -p data/inbox state`.
- [ ] **Step 2: `docs/slack-setup.md` 작성 (운영자가 할 일).** 순서는 이렇다.
  1. api.slack.com에서 앱을 만든다.
  2. Bot 권한 `chat:write`, `channels:history`(비공개 채널이면 `groups:history`)를 준다.
  3. 워크스페이스에 설치하고 `xoxb-` 토큰을 복사한다.
  4. 채널에 봇을 초대한다(`/invite @앱이름`).
  5. 채널 ID와 운영자 본인의 멤버 ID를 확인한다.
  6. 서버 `.env`에 세 값을 넣는다.
- [ ] **Step 3: 문서 수정.**
  - CLAUDE.md "배포": "로컬 `chroma_db/`를 rsync로 올린다"를 "서버가 `chroma_db/`·`data/`·`state/`의 원본, 코드는 `deploy/push.sh`로만 올린다"로 바꾼다.
  - README "약관 넣기": 자동 확인 + Slack 승인 흐름을 3줄로 적는다.
  - `docs/업무절차.md`: "자동화 후" 절을 추가한다(Slack 요약 확인 → "승인" 답글).
- [ ] **Step 4: 서버 첫 설치 (운영자 집 네트워크에서).** 순서대로 실행한다.
  1. 서버의 현재 `chroma_db/`를 맥으로 백업한다: `rsync -avz -e "ssh -i ~/.ssh/boheom-mate.pem" ubuntu@3.36.241.30:/home/ubuntu/boheom-mate/chroma_db/ ~/boheom-mate-backup/chroma_db/`
  2. `deploy/push.sh`
  3. 맥의 `data/*.pdf`를 서버 `data/`에 한 번 올린다.
  4. 서버 `.env`에 Slack 값 3개를 넣는다.
  5. `bash deploy/setup.sh`
- [ ] **Step 5: 서버에서 수동 실행으로 확인.**
  - `sudo systemctl start weekly-check.service && journalctl -u weekly-check -n 50`
  - 동시에 다른 창에서 `free -m`으로 메모리를 지켜본다.
  - Slack 요약에 "KB손해보험: 새 약관 3건"(계약전환용, 다이렉트, 다이렉트간편)이 나오고, 약관별 메시지 3개가 와야 한다.
  - 사이트(https://boheommate.duckdns.org)는 계속 응답해야 한다.
- [ ] **Step 6: 승인 흐름 확인.**
  1. 운영자가 메시지 하나에 "승인" 답글을 단다.
  2. 5분 안에 "반영 완료"가 달리는지 본다.
  3. 사이트 "내 보험 고르기"에 그 상품이 보이고 상담이 되는지 확인한다.
  4. `systemctl list-timers | grep -E "weekly|apply"`로 다음 실행이 월요일 09:00 KST(00:00 UTC)로 잡혔는지 확인한다.
- [ ] **Step 7: Commit** `git add deploy docs CLAUDE.md README.md && git commit -m "주간 약관 확인 서버 설치와 문서"`

### Task 10: 보험사 추가 (2~4주차, 보험사마다 반복)

추가 순서: 삼성화재 → 현대해상 → DB손해보험 → 메리츠화재 → 한화손해보험 → 롯데손해보험 → 흥국화재 → NH농협손해보험이다. 주마다 2~3곳씩 하고, 막히는 곳은 건너뛴다.

**Files:** `insurers/<영문약칭>.py`, `insurers/__init__.py`, `tests/fixtures/<약칭>_listing.*`, `tests/test_<약칭>_listing.py`

- [ ] **Step 1: 접근 허용 확인.** Task 6 Step 1과 같다. 자동 접근이 막혔거나 헤드리스 브라우저가 필요하면 `MANUAL`에 회사 이름만 추가하고 커밋한 뒤 끝낸다. 운영자는 그 보험사 약관을 `data/inbox/`에 직접 넣는다.
- [ ] **Step 2: 조사, fixture 저장, 테스트 작성.** Task 6 Step 3과 같은 assert를 쓴다. 판매 중인 실손 상품 1개 이상, 판 형식, PDF 주소, 단체 실손 제외, 빈 응답이면 `ListingError`를 확인한다.
- [ ] **Step 3: 구현 → 통과 → `ENABLED`에 등록**
- [ ] **Step 4: 이번 주 실행 전에 실제 확인 1회.** `fetch()` 출력이 실손·판매 중·최신 판만인지 운영자와 함께 본다(첫 주에는 그 회사 약관이 모두 새 약관으로 올라온다).
- [ ] **Step 5: Commit 후 `deploy/push.sh`**
