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
    """반환값: 반영한 약관 키 목록. 주간 확인이 상태 파일을 쓰는 중이면 이번 차례는 건너뛴다(5분 뒤 다시 실행된다)."""
    try:
        with sync_state.lock(state_path, wait=False):
            return _run(slack or Slack(), state_path, products_path, data_dir, build_index, split or _default_split, site)
    except sync_state.Busy:
        return []


def _is_approved(slack, item, log_path):
    """대기 항목은 "승인" 답글, 반영 실패 항목은 실패 답글 뒤에 다시 단 "승인"이 있으면 참."""
    if not item["slack_ts"]:
        return False
    try:
        if item["status"] == "pending":
            return slack.approved(item["slack_ts"])
        if item["status"] == "failed" and item.get("failed_reply_ts"):
            return slack.approved(item["slack_ts"], after=item["failed_reply_ts"])
    except Exception as e:  # 지워진 메시지 등: 이 항목만 건너뛰고 나머지는 계속 반영한다
        sync_state.log({"event": "apply_check_failed", "key": item["key"], "error": repr(e)},
                       path=log_path)
    return False


def _run(slack, state_path, products_path, data_dir, build_index, split, site):
    state = sync_state.load(state_path)
    log_path = Path(state_path).parent / "runs.log"
    approved = [item for item in state["pending"] if _is_approved(slack, item, log_path)]
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
                item["failed_reply_ts"] = slack.post(
                    f'반영 실패: {e}\n다시 하려면 이 답글 아래에 "승인"을 한 번 더 달아 주세요.', thread_ts=item["slack_ts"])
                continue
            item["status"], item["error"] = "applied", None
            applied.append(item["key"])
            slack.post(f"반영 완료 · DB 전체 {total}조각", thread_ts=item["slack_ts"])
    finally:
        # 사이트 시작이 실패해도 반영 결과는 남겨야 5분마다 다시 임베딩하지 않는다. 저장이 실패해도 사이트는 켠다
        try:
            sync_state.save(state, state_path)
            sync_state.log({
                "event": "apply_approved", "applied": applied,
                "failed": [i["key"] for i in approved if i["status"] == "failed"],
                "approved_with_warning": [i["key"] for i in approved if i["status"] == "applied" and not i["check"].get("ok", True)],
            }, path=log_path)
        finally:
            site("start")
    return applied


def main():
    from dotenv import load_dotenv

    load_dotenv(ingest.BASE_DIR / ".env")
    try:
        run()
    except Exception as e:
        sync_state.log({"event": "apply_failed", "error": repr(e)})
        try:
            Slack().post(f"[승인 반영] 실행 실패: {e}")
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
