"""승인 반영 테스트. 가짜 Slack·가짜 임베딩·가짜 사이트 재시작으로 시험한다(OpenAI·systemctl 없음).

실행: .venv/bin/python tests/test_apply_approved.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apply_approved
import products
import sync_state


class FakeSlack:
    def __init__(self, approved_ts):
        self.approved_ts = set(approved_ts)
        self.replies = []

    def approved(self, ts):
        return ts in self.approved_ts

    def post(self, text, thread_ts=None):
        self.replies.append((thread_ts, text))
        return "x"


def item(ts, product, status="pending"):
    return {"key": f"삼성화재|{product}|26.10", "company": "삼성화재", "category": "실손의료보험", "product": product,
            "edition": "26.10", "pdf": f"{product}.pdf", "check": {}, "slack_ts": ts, "status": status,
            "created": "2026-10-26", "error": None}


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    state_path, products_path = d / "state" / "sync.json", d / "state" / "products.json"
    sync_state.save({"known": [], "pending": [item("1", "A"), item("2", "B"), item("3", "C", status="applied"), item(None, "D")]}, state_path)
    built, site_calls = [], []

    def fake_build(articles, source):
        built.append(source)
        if source == "B.pdf":
            raise RuntimeError("OpenAI 오류")
        return 300

    def run(slack):
        return apply_approved.run(slack=slack, state_path=state_path, products_path=products_path, data_dir=d,
                                  build_index=fake_build, split=lambda path: ["조항"], site=site_calls.append)

    # 승인 안 됨 → 사이트도 건드리지 않는다
    assert run(FakeSlack([])) == [] and site_calls == [] and built == []

    # A 승인, B 승인(반영 중 실패), C는 이미 반영됨, D는 Slack 메시지가 없음
    slack = FakeSlack(["1", "2", "3"])
    applied = run(slack)
    assert applied == ["삼성화재|A|26.10"], applied
    assert built == ["A.pdf", "B.pdf"] and site_calls == ["stop", "start"]
    state = {p["product"]: p for p in sync_state.load(state_path)["pending"]}
    assert state["A"]["status"] == "applied" and state["B"]["status"] == "failed" and "OpenAI 오류" in state["B"]["error"]
    assert state["C"]["status"] == "applied" and state["D"]["status"] == "pending"
    assert ("1", "반영 완료 · DB 전체 300조각") in slack.replies
    assert any(ts == "2" and text.startswith("반영 실패: ") for ts, text in slack.replies)
    entries = products.load(products_path)
    assert {"company": "삼성화재", "category": "실손의료보험", "product": "A", "edition": "26.10", "source": "A.pdf"} in entries
    assert not any(e["product"] == "B" for e in entries)

    # 사이트를 멈춘 뒤 무슨 일이 있어도 다시 켠다
    site_calls.clear()
    sync_state.save({"known": [], "pending": [item("9", "E")]}, state_path)

    def broken_split(path):
        raise KeyboardInterrupt  # 예외 처리로 잡히지 않는 중단

    try:
        apply_approved.run(slack=FakeSlack(["9"]), state_path=state_path, products_path=products_path, data_dir=d,
                           build_index=fake_build, split=broken_split, site=site_calls.append)
    except KeyboardInterrupt:
        pass
    assert site_calls == ["stop", "start"], site_calls

print("OK")
