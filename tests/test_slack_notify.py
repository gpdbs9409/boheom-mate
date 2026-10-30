"""Slack 알림 테스트. 실제 Slack 대신 가짜 client를 쓴다.

실행: .venv/bin/python tests/test_slack_notify.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from boheom_mate.integrations.slack_notify import Slack, item_message, summary_message


class FakeClient:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self.last_ts = None

    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        self.last_ts = f"{len(self.calls)}.0"
        return {"ok": True, "ts": self.last_ts}

    def conversations_replies(self, channel, ts):
        return {"ok": True, "messages": self.replies}


# 첫 메시지는 부모(운영자가 올린 것이 아니어도), 다른 사람의 "승인"과 "승인합니다"는 승인이 아니다
fake = FakeClient(replies=[{"user": "U1", "ts": "1", "text": "승인"}, {"user": "U2", "text": "승인"}, {"user": "U1", "text": "승인합니다"}])
s = Slack(client=fake, channel="C1", operator="U1")
assert s.approved("1") is False
fake.replies.append({"user": "U1", "text": " 승인 \n"})
assert s.approved("1") is True

# after: 이 ts 뒤에 단 답글만 본다(반영 실패 뒤 다시 승인)
fake.replies[:] = [{"user": "U1", "ts": "1", "text": "x"}, {"user": "U1", "ts": "2.0", "text": "승인"}, {"user": "B", "ts": "3.0", "text": "반영 실패"}]
assert s.approved("1") is True and s.approved("1", after="3.0") is False
fake.replies.append({"user": "U1", "ts": "4.0", "text": "승인"})
assert s.approved("1", after="3.0") is True
assert s.post("hi", thread_ts="1") == fake.last_ts
assert fake.calls[-1] == {"channel": "C1", "text": "hi", "thread_ts": "1"}

ok_item = {"company": "KB손해보험", "product": "P", "edition": "26.09",
           "check": {"ok": True, "reasons": [], "notes": ["보통약관: 제52조 다음 제54조"], "sections": {"보통약관": 10}, "suspects": []}}
m = item_message(ok_item)
assert m.startswith("[새 약관] KB손해보험 · P · 26.09판"), m
assert "점검: 정상(참고 1건)" in m and "보통약관 10" in m and "제52조 다음 제54조" in m
assert '"승인"이라고 답글' in m and "1분 정도 사이트가 멈춥니다" in m

bad = {**ok_item, "check": {"ok": False, "reasons": ["구역을 찾지 못함"], "notes": [], "sections": {"보통약관": 10},
                             "suspects": ["보통약관 제1조(목적): 이 계약은"]}}
m = item_message(bad)
assert "점검: 확인 필요" in m and "- 구역을 찾지 못함" in m and "보통약관 제1조(목적)" in m and "정상" not in m

msg = summary_message([{"company": "KB손해보험", "status": "failed", "count": 0, "error": "timeout"}], [], "2026-10-26")
assert msg.startswith("[주간 약관 확인] 2026-10-26") and "KB손해보험: 목록 읽기 실패 (timeout)" in msg and "없음" not in msg
msg = summary_message([{"company": "KB손해보험", "status": "new", "count": 3, "error": None},
                       {"company": "삼성화재", "status": "none", "count": 0, "error": None},
                       {"company": "현대해상", "status": "manual", "count": 0, "error": None}],
                      [{**ok_item, "created": "2026-10-19"}], "2026-10-26")
assert "KB손해보험: 새 약관 3건" in msg and "삼성화재: 새 약관 없음" in msg
assert "현대해상: 자동 확인 불가 (PDF를 data/inbox/에 직접 넣어 주세요)" in msg
assert "승인 대기 1건:" in msg and "KB손해보험 · P · 26.09판" in msg

failed = {**ok_item, "created": "2026-10-19", "status": "failed", "error": "timeout"}
msg = summary_message([], [], "2026-10-26", failed=[failed])
assert "반영 실패 1건" in msg and "KB손해보험 · P · 26.09판" in msg and '"승인"을 한 번 더' in msg
print("OK")
