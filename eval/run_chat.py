"""이어지는 대화(채팅) 평가. 시나리오마다 여러 턴을 차례로 보내고 턴별로 채점한다.

실행 (my-app 폴더에서):
    .venv/bin/python eval/run_chat.py --note "실험 이름"
    .venv/bin/python eval/run_chat.py --no-plain --note "쉬운 말 규칙 없이"

채점 기준 (턴마다, expect에 적힌 항목만)
- calc: 숫자면 계산기를 불러 그 총액이 나와야 한다. null이면 계산기를 부르지 않아야 한다
- search: true면 이번 턴에 약관 검색을 해야 한다
- ask: true면 되묻는 말(물음표나 "알려 주세요")이 답변에 있어야 한다
- not_covered: true면 "확인할 수 없"/"보상하지 않"/"대상이 아니" 같은 표현이 있어야 한다
- 어려운 용어: 괄호 밖에 쓴 약관 용어 수(급여, 비급여, 본인부담금, 공제, 통원, 산정특례, 상급병실료, 요양급여).
  쉬운 말로 바꾸고 괄호에 약관 용어를 넣은 경우는 세지 않는다. 낮을수록 좋다
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR.parent))

from boheom_mate.ui import chat  # noqa: E402

JARGON = re.compile(r"비급여|(?<!비)급여|본인부담금|공제|통원|산정특례|상급병실료|요양급여")
NOT_COVERED = re.compile(r"확인할 수 없|확인이 어렵|보상하지 않|보상되지 않|대상이 아[니닙]|해당하지 않")
ASK = re.compile(r"\?|알려 ?주세요|알려 ?주시")


def jargon_count(text):
    outside = re.sub(r"\([^)]*\)", "", text)  # 괄호 안 설명은 빼고 센다
    return len(JARGON.findall(outside))


def check(expect, result):
    fails = []
    calc = result["calculation"]
    if "calc" in expect:
        want = expect["calc"]
        if want is None and calc:
            fails.append(f"계산하면 안 되는데 계산함({calc['total']:,})")
        elif want is not None and not calc:
            fails.append("계산기를 부르지 않음")
        elif want is not None and calc["total"] != want:
            fails.append(f"금액 {calc['total']:,} ≠ 기대 {want:,} (값: {calc['inputs']})")
    if expect.get("search") and not result["searches"]:
        fails.append("약관 검색을 안 함")
    if expect.get("ask") and not ASK.search(result["content"]):
        fails.append("되묻지 않음")
    if expect.get("not_covered") and not NOT_COVERED.search(result["content"]):
        fails.append("보상 안 됨/확인 불가를 말하지 않음")
    return fails


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-plain", action="store_true", help="쉬운 말 규칙을 빼고 평가")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    chat.PLAIN_LANGUAGE = not args.no_plain

    scenarios = json.loads((EVAL_DIR / "conversations.json").read_text(encoding="utf-8"))
    rows, passed, total, jargon, seconds = [], 0, 0, [], []
    for sc in scenarios:
        history = []
        print(f"\n[{sc['id']}] {sc['name']}")
        for turn in sc["turns"]:
            history.append({"role": "user", "content": turn["user"]})
            start = time.time()
            result = chat.reply(history, chat.CALCULATOR_SOURCE)
            seconds.append(time.time() - start)
            history.append({"role": "assistant", "content": result["content"], "calculation": result["calculation"]})

            fails = check(turn["expect"], result)
            words = jargon_count(result["content"])
            passed += not fails
            total += 1
            jargon.append(words)
            print(f"  {'OK  ' if not fails else 'FAIL'} {turn['user'][:30]} | 어려운 용어 {words}개 | {seconds[-1]:.1f}초"
                  + (f" | {'; '.join(fails)}" if fails else ""))
            rows.append({"id": sc["id"], "user": turn["user"], "reply": result["content"], "fails": fails,
                         "jargon": words, "searches": result["searches"],
                         "calculation": result["calculation"] and {"inputs": result["calculation"]["inputs"],
                                                                   "total": result["calculation"]["total"]}})

    summary = {
        "turn_pass": f"{passed}/{total}",
        "turn_pass_rate": round(passed / total, 2),
        "jargon_per_reply": round(sum(jargon) / len(jargon), 2),
        "avg_seconds": round(sum(seconds) / len(seconds), 1),
    }
    print(f"\n턴 통과 {summary['turn_pass']} ({summary['turn_pass_rate']:.0%}) | "
          f"답변당 어려운 용어 {summary['jargon_per_reply']}개 | 평균 {summary['avg_seconds']}초")

    out = EVAL_DIR / "results" / f"chat_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "note": args.note,
        "config": {"model": chat.rag.ANSWER_MODEL, "plain_language": chat.PLAIN_LANGUAGE},
        "summary": summary, "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"결과 저장: {out}")


if __name__ == "__main__":
    main()
