"""주간 약관 확인 테스트. 가짜 보험사·가짜 Slack·임시 폴더로 시험한다(네트워크·OpenAI 없음).

실행: .venv/bin/python tests/test_weekly_check.py
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync_state
import weekly_check
from insurers import Listing, ListingError
from weekly_check import pdf_name


class FakeSlack:
    def __init__(self, fail=False):
        self.posts = []
        self.fail = fail

    def post(self, text, thread_ts=None):
        if self.fail:
            raise RuntimeError("slack down")
        self.posts.append(text)
        return f"{len(self.posts)}.0"


def company(name, listings=(), fetch_error=None, download_error=None):
    def fetch():
        if fetch_error:
            raise ListingError(fetch_error)
        return list(listings)

    def download(listing, dest):
        if company.download_error:
            raise ListingError(company.download_error)
        Path(dest).write_bytes(b"%PDF-1.4 fake")

    company.download_error = download_error
    return SimpleNamespace(COMPANY=name, fetch=fetch, download=download, ctl=company)


GOOD = {"ok": True, "reasons": [], "notes": [], "sections": {"보통약관": 3}, "suspects": []}
fake_split = lambda path: [{"section": "보통약관", "article_no": f"제{i}조", "title": "t", "text": "본문"} for i in (1, 2, 3)]

assert pdf_name("A", "B/C 실손", "26.09") == "A_B_C_실손_26.09.pdf"

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    state_path, data, inbox = d / "state" / "sync.json", d / "data", d / "data" / "inbox"
    data.mkdir()
    inbox.mkdir()
    run = lambda enabled, slack, manual=(): weekly_check.run(
        enabled=enabled, manual=list(manual), slack=slack, state_path=state_path,
        data_dir=data, inbox=inbox, split=fake_split, today="2026-10-26")

    new = Listing(company="삼성화재", product="삼성 실손/다이렉트", edition="26.10", pdf_url="http://x/a.pdf")
    samsung = company("삼성화재", [new])

    # 1) 새 약관 1건: 대기 항목, 이미 아는 목록, Slack 요약 + 약관별 메시지
    slack = FakeSlack()
    run({"삼성화재": samsung}, slack, manual=["현대해상"])
    state = sync_state.load(state_path)
    assert len(state["pending"]) == 1 and state["pending"][0]["slack_ts"] == "2.0", state
    item = state["pending"][0]
    assert sync_state.key("삼성화재", new.product, "26.10") in state["known"]
    assert (data / item["pdf"]).exists() and item["pdf"] == "삼성화재_삼성_실손_다이렉트_26.10.pdf"
    assert item["status"] == "pending" and item["created"] == "2026-10-26" and item["category"] == "실손의료보험"
    assert len(slack.posts) == 2
    assert "삼성화재: 새 약관 1건" in slack.posts[0] and "현대해상: 자동 확인 불가" in slack.posts[0]
    assert slack.posts[1].startswith("[새 약관] 삼성화재")

    # 2) 같은 주에 다시 실행: 약관별 메시지 없이 요약에 승인 대기만
    slack = FakeSlack()
    run({"삼성화재": samsung}, slack)
    assert len(slack.posts) == 1 and "삼성화재: 새 약관 없음" in slack.posts[0] and "승인 대기 1건" in slack.posts[0]
    assert len(sync_state.load(state_path)["pending"]) == 1

    # 3) 목록 읽기 실패는 "없음"이 아니다
    slack = FakeSlack()
    run({"현대해상": company("현대해상", fetch_error="timeout")}, slack)
    line = [l for l in slack.posts[0].splitlines() if l.startswith("현대해상")][0]
    assert "목록 읽기 실패 (timeout)" in line and "없음" not in line

    # 4) 내려받기 실패: 알지 못한 것으로 남아 다음 주에 다시 시도
    db_new = Listing(company="DB손해보험", product="DB 실손", edition="26.10", pdf_url="http://x/b.pdf")
    db = company("DB손해보험", [db_new], download_error="404")
    slack = FakeSlack()
    run({"DB손해보험": db}, slack)
    assert sync_state.key("DB손해보험", "DB 실손", "26.10") not in sync_state.load(state_path)["known"]
    assert "DB손해보험: 목록 읽기 실패" in slack.posts[0] and "내려받기 실패" in slack.posts[0]
    db.ctl.download_error = None
    run({"DB손해보험": db}, FakeSlack())
    assert sync_state.key("DB손해보험", "DB 실손", "26.10") in sync_state.load(state_path)["known"]

    # 5) inbox: 규칙에 맞는 파일은 처리해 data/로 옮기고, 틀린 이름은 남겨 두고 알린다
    (inbox / "메리츠화재__무배당 실손__26.10.pdf").write_bytes(b"%PDF-1.4")
    (inbox / "잘못된이름.pdf").write_bytes(b"%PDF-1.4")
    slack = FakeSlack()
    run({}, slack)
    state = sync_state.load(state_path)
    assert any(p["company"] == "메리츠화재" and p["edition"] == "26.10" for p in state["pending"])
    assert (data / "메리츠화재_무배당_실손_26.10.pdf").exists() and not (inbox / "메리츠화재__무배당 실손__26.10.pdf").exists()
    assert (inbox / "잘못된이름.pdf").exists() and "inbox 파일 이름 규칙 오류: 잘못된이름.pdf" in slack.posts[0]

    # 6) Slack이 죽어도 상태는 저장되고, 메시지를 못 보낸 항목은 다음 실행 때 다시 보낸다
    hk = company("한화손해보험", [Listing(company="한화손해보험", product="한화 실손", edition="26.10", pdf_url="u")])
    try:
        run({"한화손해보험": hk}, FakeSlack(fail=True))
    except RuntimeError:
        pass
    state = sync_state.load(state_path)
    hanwha = [p for p in state["pending"] if p["company"] == "한화손해보험"]
    assert len(hanwha) == 1 and hanwha[0]["slack_ts"] is None
    slack = FakeSlack()
    run({}, slack)
    assert any(p.startswith("[새 약관] 한화손해보험") for p in slack.posts)

# 반영 실패한 항목도 주간 요약에 계속 보인다
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    state_path = d / "state" / "sync.json"
    sync_state.save({"known": [], "pending": [{"key": "k", "company": "삼성화재", "category": "실손의료보험", "product": "P",
                     "edition": "26.10", "pdf": "p.pdf", "check": GOOD, "slack_ts": "1", "status": "failed",
                     "created": "2026-10-19", "error": "timeout"}]}, state_path)
    slack = FakeSlack()
    weekly_check.run(enabled={}, manual=[], slack=slack, state_path=state_path, data_dir=d, inbox=d / "inbox",
                     split=fake_split, today="2026-10-26")
    assert "반영 실패 1건" in slack.posts[0]
print("OK")
