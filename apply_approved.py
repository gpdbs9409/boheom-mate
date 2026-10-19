"""승인 반영: 서버(systemd timer)가 5분마다 실행한다.

승인 대기 약관의 Slack 메시지에 운영자가 "승인" 답글을 달았으면
사이트를 잠깐 멈추고 → 임베딩해 chroma_db/에 저장하고 → 상품 목록에 등록한 뒤 → 사이트를 다시 켠다.
사이트를 멈추는 이유: 사이트(Streamlit)와 이 스크립트가 같은 Chroma 파일에 동시에 쓰지 않게 하려는 것이다.
승인된 약관이 없으면 아무것도 하지 않는다.

실행: .venv/bin/python apply_approved.py
"""
import subprocess
from pathlib import Path

import ingest
import products
import sync_state
from slack_notify import Slack

SERVICE = "boheom-mate"


def site(action):
    subprocess.run(["sudo", "systemctl", action, SERVICE], check=True)


def _default_split(path):
    return ingest.split_articles(ingest.extract_text(path))[0]


def run(slack=None, state_path=sync_state.STATE_FILE, products_path=products.DATA_FILE, data_dir=ingest.DATA_DIR,
        build_index=ingest.build_index, split=None, site=site):
    """반환값: 반영한 약관 키 목록"""
    split = split or _default_split
    slack = slack or Slack()
    state = sync_state.load(state_path)
    approved = [
        item for item in state["pending"]
        if item["status"] == "pending" and item["slack_ts"] and slack.approved(item["slack_ts"])
    ]
    if not approved:
        return []

    applied = []
    site("stop")
    try:
        for item in approved:
            try:
                total = build_index(split(Path(data_dir) / item["pdf"]), item["pdf"])
                products.add({k: item[k] for k in ("company", "category", "product", "edition")} | {"source": item["pdf"]},
                             path=products_path)
            except Exception as e:
                item["status"], item["error"] = "failed", repr(e)
                slack.post(f"반영 실패: {e}", thread_ts=item["slack_ts"])
                continue
            item["status"] = "applied"
            applied.append(item["key"])
            slack.post(f"반영 완료 · DB 전체 {total}조각", thread_ts=item["slack_ts"])
    finally:
        site("start")
        sync_state.save(state, state_path)
        sync_state.log({"event": "apply_approved", "applied": applied,
                        "failed": [i["key"] for i in approved if i["status"] == "failed"]},
                       path=Path(state_path).parent / "runs.log")
    return applied


def main():
    from dotenv import load_dotenv

    load_dotenv(ingest.BASE_DIR / ".env")
    run()


if __name__ == "__main__":
    main()
