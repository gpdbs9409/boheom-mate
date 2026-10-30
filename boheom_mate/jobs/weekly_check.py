"""주간 약관 확인: 매주 월요일 9시에 서버(systemd timer)가 실행한다.

1) 보험사 공시실에서 판매 중인 실손 약관 목록을 읽고, 처음 보는 약관(회사 + 상품 + 판)의 PDF를 data/에 받는다
2) 운영자가 data/inbox/에 직접 넣은 PDF(보험사__상품 이름__판.pdf)도 같이 처리한다
3) 조항을 나눠 점검하고, Slack에 주간 요약 1건과 약관별 메시지를 올린다
DB에는 넣지 않는다. 운영자가 Slack에서 "승인"하면 apply_approved.py가 넣는다.

실행: .venv/bin/python -m boheom_mate.jobs.weekly_check
"""
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from boheom_mate.core import ingest
from boheom_mate.integrations import insurers
from boheom_mate.jobs import split_check
from boheom_mate.store import sync_state
from boheom_mate.integrations.insurers import ListingError
from boheom_mate.integrations.slack_notify import Slack, item_message, summary_message

INBOX = ingest.DATA_DIR / "inbox"
INBOX_NAME = re.compile(r"^(.+?)__(.+)__(\d{2}\.\d{2})$")
UNSAFE = re.compile(r"[\s/\\:]+")


def pdf_name(company, product, edition):
    return UNSAFE.sub("_", f"{company}_{product}_{edition}") + ".pdf"


def _default_split(path):
    return ingest.split_articles(ingest.extract_text(path))[0]


def _today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


def _new_item(company, category, product, edition, pdf, split, today):
    try:
        check = split_check.check(split(pdf))
    except Exception as e:  # 깨진 PDF 등: 멈추지 않고 확인 필요로 올린다
        check = {"ok": False, "reasons": [f"PDF를 읽지 못함: {e}"], "notes": [], "sections": {}, "suspects": []}
    return {
        "key": sync_state.key(company, product, edition),
        "company": company, "category": category, "product": product, "edition": edition,
        "pdf": Path(pdf).name, "check": check, "slack_ts": None,
        "status": "pending", "created": today, "error": None,
    }


def run(enabled=insurers.ENABLED, manual=insurers.MANUAL, slack=None, state_path=sync_state.STATE_FILE,
        data_dir=ingest.DATA_DIR, inbox=INBOX, split=None, today=None):
    """반환값: 보험사별 결과 [{"company", "status", "count", "error"}]"""
    with sync_state.lock(state_path):
        return _run(enabled, manual, slack, state_path, Path(data_dir), Path(inbox), split or _default_split, today or _today())


def _run(enabled, manual, slack, state_path, data_dir, inbox, split, today):
    state = sync_state.load(state_path)
    known = set(state["known"])
    waiting = [p for p in state["pending"] if p["status"] == "pending"]
    results, notes, new_items = [], [], []

    def accept(item):
        new_items.append(item)
        state["pending"].append(item)
        state["known"].append(item["key"])
        known.add(item["key"])

    for company, module in enabled.items():
        try:
            listings = module.fetch()
        except ListingError as e:
            results.append({"company": company, "status": "failed", "count": 0, "error": str(e)})
            continue
        count, failed = 0, 0
        for listing in listings:
            if sync_state.key(listing.company, listing.product, listing.edition) in known:
                continue
            dest = data_dir / pdf_name(listing.company, listing.product, listing.edition)
            try:
                module.download(listing, dest)
            except ListingError as e:  # 알지 못한 것으로 두어 다음 주에 다시 시도한다
                failed += 1
                notes.append(f"{company} · {listing.product} · {listing.edition}판 내려받기 실패: {e}")
                continue
            accept(_new_item(listing.company, listing.category, listing.product, listing.edition, dest, split, today))
            count += 1
        if failed and not count:
            results.append({"company": company, "status": "failed", "count": 0, "error": f"새 약관 {failed}건 내려받기 실패"})
        else:
            results.append({"company": company, "status": "new" if count else "none", "count": count, "error": None})

    for company in manual:
        results.append({"company": company, "status": "manual", "count": 0, "error": None})

    for pdf in sorted(inbox.glob("*.pdf")) if inbox.exists() else []:
        m = INBOX_NAME.match(pdf.stem)
        if not m:
            notes.append(f"inbox 파일 이름 규칙 오류: {pdf.name} (보험사__상품 이름__판.pdf, 예: 삼성화재__무배당 실손__26.10.pdf)")
            continue
        company, product, edition = m.groups()
        if sync_state.key(company, product, edition) in known:
            notes.append(f"inbox: 이미 있는 약관이라 건너뜀: {pdf.name}")
            continue
        dest = data_dir / pdf_name(company, product, edition)
        shutil.move(str(pdf), dest)
        accept(_new_item(company, "실손의료보험", product, edition, dest, split, today))
        notes.append(f"inbox: 직접 넣은 약관 접수: {company} · {product} · {edition}판")

    # Slack이 실패해도 받은 약관을 잃지 않도록 먼저 저장한다
    sync_state.save(state, state_path)
    try:
        if slack is None:
            slack = Slack()
        failed = [p for p in state["pending"] if p["status"] == "failed"]
        slack.post(summary_message(results, waiting, today, notes, failed))
        for item in state["pending"]:
            if item["status"] == "pending" and not item["slack_ts"]:
                item["slack_ts"] = slack.post(item_message(item))
    finally:
        sync_state.save(state, state_path)
        sync_state.log({"event": "weekly_check", "results": results, "new": [i["key"] for i in new_items], "notes": notes},
                       path=Path(state_path).parent / "runs.log")
    return results


def main():
    from dotenv import load_dotenv

    load_dotenv(ingest.BASE_DIR / ".env")
    try:
        run()
    except Exception as e:
        sync_state.log({"event": "weekly_check_failed", "error": repr(e)})
        try:
            Slack().post(f"[주간 약관 확인] 실행 실패: {e}")
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
