"""Slack 알림: 주간 요약과 약관별 메시지를 보내고, 운영자의 "승인" 답글을 읽는다.

.env에 SLACK_BOT_TOKEN(xoxb-…), SLACK_CHANNEL_ID(C…), SLACK_OPERATOR_ID(U…)를 둔다.
운영자가 단 답글 중 앞뒤 공백을 뺀 글이 정확히 "승인"인 것만 승인으로 본다.
"""
import os

APPROVE_WORD = "승인"

STATUS_LINES = {
    "new": "새 약관 {count}건",
    "none": "새 약관 없음",
    "failed": "목록 읽기 실패 ({error})",
    "manual": "자동 확인 불가 (PDF를 data/inbox/에 직접 넣어 주세요)",
}


class Slack:
    def __init__(self, client=None, channel=None, operator=None):
        if client is None:
            from slack_sdk import WebClient

            client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        self.client = client
        self.channel = channel or os.environ["SLACK_CHANNEL_ID"]
        self.operator = operator or os.environ["SLACK_OPERATOR_ID"]

    def post(self, text, thread_ts=None):
        """메시지를 보내고 그 메시지의 ts(답글을 찾을 때 쓰는 번호)를 돌려준다."""
        kwargs = {"channel": self.channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        return self.client.chat_postMessage(**kwargs)["ts"]

    def approved(self, ts, after=None):
        """after가 있으면 그 ts 뒤에 단 답글만 본다(반영 실패 답글 뒤에 다시 단 "승인")."""
        replies = self.client.conversations_replies(channel=self.channel, ts=ts)["messages"][1:]  # 첫 메시지는 부모
        return any(
            m.get("user") == self.operator
            and (m.get("text") or "").strip() == APPROVE_WORD
            and (after is None or float(m.get("ts", 0)) > float(after))
            for m in replies
        )


def _title(item):
    return f'{item["company"]} · {item["product"]} · {item["edition"]}판'


def item_message(item):
    check = item["check"]
    sections = ", ".join(f"{name} {count}" for name, count in check["sections"].items())
    lines = [
        f"[새 약관] {_title(item)}",
        f'조각 {sum(check["sections"].values())}개 · 구역별: {sections}',
    ]
    if check["ok"]:
        notes = check.get("notes") or []
        lines.append(f"점검: 정상(참고 {len(notes)}건)" if notes else "점검: 정상")
    else:
        lines.append("점검: 확인 필요")
        lines += [f"- {reason}" for reason in check["reasons"]]
        if check["suspects"]:
            lines.append("의심 조항:")
            lines += [f"  {s}" for s in check["suspects"]]
    if check.get("notes"):
        lines.append("참고(통과 기준 안):")
        lines += [f"- {note}" for note in check["notes"]]
    lines.append('반영하려면 이 메시지에 "승인"이라고 답글을 달아 주세요. 반영 중 1분 정도 사이트가 멈춥니다.')
    return "\n".join(lines)


def summary_message(results, waiting, today, notes=(), failed=()):
    """notes: 보험사 줄 아래에 덧붙일 말(inbox 파일 이름 오류 등), failed: 반영에 실패한 항목"""
    lines = [f"[주간 약관 확인] {today}"]
    for r in results:
        lines.append(f'{r["company"]}: ' + STATUS_LINES[r["status"]].format(count=r["count"], error=r.get("error")))
    lines += list(notes)
    if waiting:
        lines.append(f"승인 대기 {len(waiting)}건:")
        lines += [f'- {_title(item)} ({item["created"]}부터)' for item in waiting]
    if failed:
        lines.append(f'반영 실패 {len(failed)}건 (다시 하려면 그 메시지의 "반영 실패" 답글 아래에 "승인"을 한 번 더 달아 주세요):')
        lines += [f'- {_title(item)}: {item.get("error")}' for item in failed]
    return "\n".join(lines)
