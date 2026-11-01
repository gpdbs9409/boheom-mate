"""이어지는 대화(채팅) 평가. 시나리오마다 여러 턴을 차례로 보내고 턴별로 채점한다.

실행 (my-app 폴더에서):
    .venv/bin/python eval/run_chat.py --note "실험 이름"
    .venv/bin/python eval/run_chat.py --no-plain --note "쉬운 말 규칙 없이"
    .venv/bin/python eval/run_chat.py --rescore eval/results/chat_20261101_140633.json  # 저장된 결과로 도구 선택 오류만 다시 센다

채점 기준 (턴마다, expect에 적힌 항목만)
- calc: 숫자면 계산기를 불러 그 총액이 나와야 한다. null이면 계산기를 부르지 않아야 한다
- search: true면 이번 턴에 약관 검색을 해야 한다
- ask: true면 되묻는 말(물음표나 "알려 주세요")이 답변에 있어야 한다
- not_covered: true면 "확인할 수 없"/"보상하지 않"/"대상이 아니" 같은 표현이 있어야 한다
- tool: 이 턴에 불러야 할 도구 이름(문자열 또는 목록)
- no_tool: 이 턴에 부르면 안 되는 도구 이름 목록
- answer_includes: 답변에 모두 들어 있어야 하는 문자열 목록
- evidence_title: 근거 조각 중 제목에 이 문자열이 들어간 것이 1개 이상 있어야 한다
- consultation: true면 상담 신청이 접수되고 접수번호가 RECEIPT_PATTERN에 맞아야 한다
- source: 이 상품(약관 파일)으로 chat.reply를 부른다. 시나리오의 source, 없으면 chat.CALCULATOR_SOURCE
- 어려운 용어: 괄호 밖에 쓴 약관 용어 수(급여, 비급여, 본인부담금, 공제, 통원, 산정특례, 상급병실료, 요양급여).
  쉬운 말로 바꾸고 괄호에 약관 용어를 넣은 경우는 세지 않는다. 낮을수록 좋다

턴 키
- requires: ["hira_key"] / ["kcd_index"]. 조건이 없는 환경이면 expect_fallback(생략 시 {"ask": true})으로 채점한다

시나리오 키
- group: "tools"면 새 도구 문항. 없으면 기존 문항(legacy, C01~C08)
- memory: true면 앱처럼 턴마다 상담 메모를 갱신해 chat.reply에 넘긴다

도구 선택 오류(Tool_Selection_Error): 한 턴에서 기대한 도구를 부르지 않았거나 부르면 안 되는 도구를 부른 경우.
턴 단위로 센다(한 턴에 여러 위반이 있어도 1). 기존 문항은 암묵 기대로 센다:
calc 숫자 → tool calculate_payout, calc null → no_tool [calculate_payout], search true → tool search_terms
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR.parent))

from boheom_mate.ui import chat, tools  # noqa: E402
from boheom_mate.core import memory as session_memory  # noqa: E402
from boheom_mate.store import outbox  # noqa: E402

JARGON = re.compile(r"비급여|(?<!비)급여|본인부담금|공제|통원|산정특례|상급병실료|요양급여")
NOT_COVERED = re.compile(r"확인할 수 없|확인이 어렵|보상하지 않|보상되지 않|대상이 아[니닙]|해당하지 않")
# 되묻기: 물음표, 또는 "알려/보내/말씀해/적어/입력해 주세요·주시겠어요" 같은 요청 표현
ASK = re.compile(r"\?|(?:알려|보내|말씀해|적어|입력해|써) ?주(?:세요|시)")
# core/consultation.py가 생기기 전에 쓰는 같은 형식(C-YYYYMMDD-XXXXXX, 0·O·1·I 뺀 32자)
FALLBACK_RECEIPT_PATTERN = re.compile(r"C-\d{8}-[A-HJ-NP-Z2-9]{6}")
DEFAULT_FALLBACK = {"ask": True}


def jargon_count(text):
    outside = re.sub(r"\([^)]*\)", "", text)  # 괄호 안 설명은 빼고 센다
    return len(JARGON.findall(outside))


def _names(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def tools_called(result):
    """이 턴에 부른 도구 이름 목록. 바꾸기 전 코드(tools 키 없음)는 calculation·searches로 만든다."""
    if result.get("tools") is not None:
        return list(result["tools"])
    tools = []
    if result.get("calculation"):
        tools.append("calculate_payout")
    if result.get("searches"):
        tools.append("search_terms")
    return tools


def tool_expectations(expect):
    """(불러야 할 도구, 부르면 안 되는 도구). 명시 키(tool·no_tool)와 기존 키의 암묵 기대를 합친다."""
    required, forbidden = set(_names(expect.get("tool"))), set(_names(expect.get("no_tool")))
    if "calc" in expect:
        (forbidden if expect["calc"] is None else required).add("calculate_payout")
    if expect.get("search"):
        required.add("search_terms")
    return required, forbidden


def tool_selection_error(expect, result):
    """이 턴이 Tool_Selection_Error면 True(턴당 최대 1)."""
    required, forbidden = tool_expectations(expect)
    called = set(tools_called(result))
    return bool(required - called or forbidden & called)


def _receipt_pattern():
    """접수번호 정규식(컴파일된 re.Pattern). core/consultation.RECEIPT_PATTERN을 쓴다."""
    try:
        from boheom_mate.core.consultation import RECEIPT_PATTERN
        return RECEIPT_PATTERN
    except ImportError:
        return FALLBACK_RECEIPT_PATTERN


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

    called = tools_called(result)
    for name in _names(expect.get("tool")):
        if name not in called:
            fails.append(f"{name}를 부르지 않음")
    for name in _names(expect.get("no_tool")):
        if name in called:
            fails.append(f"부르면 안 되는 {name}를 부름")
    for text in _names(expect.get("answer_includes")):
        if text not in result["content"]:
            fails.append(f"답변에 '{text}' 없음")
    if expect.get("evidence_title"):
        want = expect["evidence_title"]
        if not any(want in (e.get("title") or "") for e in result.get("evidence") or []):
            fails.append(f"근거에 '{want}' 조각 없음")
    if expect.get("consultation"):
        consultation = result.get("consultation")
        receipt = (consultation or {}).get("receipt_number") or ""
        if not consultation:
            fails.append("상담 신청이 접수되지 않음")
        elif not _receipt_pattern().fullmatch(receipt):
            fails.append(f"접수번호 형식이 다름({receipt})")
    return fails


def environment_mode():
    """외부 API 키·로컬 색인이 있는지(bool만 기록한다)."""
    try:
        from boheom_mate.integrations import hira_hospital
        hira_key = bool(hira_hospital.has_key())
    except ImportError:
        hira_key = bool(os.environ.get("HIRA_SERVICE_KEY"))
    try:
        from boheom_mate.store import kcd_index
        kcd = bool(kcd_index.available())
    except ImportError:
        kcd = False
    return {"hira_key": hira_key, "kcd_index": kcd}


def effective_expect(turn, mode):
    """requires 조건이 없는 환경이면 expect_fallback(생략 시 {"ask": true})으로 채점한다."""
    if all(mode.get(need) for need in turn.get("requires", [])):
        return turn["expect"], False
    return turn.get("expect_fallback", DEFAULT_FALLBACK), True


def turn_source(scenario, turn):
    return turn["expect"].get("source") or scenario.get("source") or chat.CALCULATOR_SOURCE


def prompt_chars(source):
    """상품별 시스템 프롬프트 길이(메모 제외). 만들 수 없으면 None."""
    try:
        ctx = tools.make_ctx(source)
        return len(chat._system_prompt(ctx, tools.bound_tools(ctx)))
    except Exception:
        return None


def is_legacy(scenario):
    return not scenario.get("group")


def load_scenarios():
    return json.loads((EVAL_DIR / "conversations.json").read_text(encoding="utf-8"))


def rescore(path):
    """저장된 결과의 searches·calculation(또는 tools)으로 기존 문항의 Tool_Selection_Error를 다시 센다."""
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    expects = {(sc["id"], turn["user"]): turn["expect"]
               for sc in load_scenarios() if is_legacy(sc) for turn in sc["turns"]}
    errors, total = 0, 0
    for row in saved["rows"]:
        expect = expects.get((row["id"], row["user"]))
        if expect is None:
            continue
        total += 1
        wrong = tool_selection_error(expect, row)
        errors += wrong
        print(f"  {'ERR ' if wrong else 'OK  '} [{row['id']}] {row['user'][:30]} | 부른 도구 {tools_called(row)}")
    print(f"\n{path}: 기존 문항 {total}턴 중 도구 선택 오류 {errors}턴")
    return {"legacy_turns": total, "legacy_tool_selection_errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-plain", action="store_true", help="쉬운 말 규칙을 빼고 평가")
    parser.add_argument("--note", default="")
    parser.add_argument("--rescore", metavar="결과파일", help="저장된 결과로 기존 문항의 도구 선택 오류만 다시 센다")
    args = parser.parse_args()
    if args.rescore:
        rescore(args.rescore)
        return
    chat.PLAIN_LANGUAGE = not args.no_plain
    # 상담 신청은 임시 outbox에 쓰고 끝나면 지운다. Slack 알림은 보내지 않는다
    tmp_outbox = tempfile.mkdtemp(prefix="eval-outbox-")
    saved_outbox, saved_notify = outbox.OUTBOX_DIR, tools.CONSULT_NOTIFY
    outbox.OUTBOX_DIR, tools.CONSULT_NOTIFY = Path(tmp_outbox), False
    try:
        run(args)
    finally:
        outbox.OUTBOX_DIR, tools.CONSULT_NOTIFY = saved_outbox, saved_notify
        shutil.rmtree(tmp_outbox, ignore_errors=True)


def run(args):
    mode = environment_mode()
    scenarios = load_scenarios()
    rows, passed, total, jargon, seconds = [], 0, 0, [], []
    legacy_passed = legacy_total = selection_errors = legacy_selection_errors = 0
    sources = []
    for sc in scenarios:
        history, memory = [], None
        legacy = is_legacy(sc)
        print(f"\n[{sc['id']}] {sc['name']}")
        for turn in sc["turns"]:
            expect, fallback = effective_expect(turn, mode)
            source = turn_source(sc, turn)
            if source not in sources:
                sources.append(source)
            if sc.get("memory"):
                memory = session_memory.update(memory, turn["user"])
            history.append({"role": "user", "content": turn["user"]})
            start = time.time()
            result = chat.reply(history, source, memory if sc.get("memory") else None, session_id=f"eval-{sc['id']}")
            seconds.append(time.time() - start)
            history.append({"role": "assistant", "content": result["content"], "calculation": result["calculation"]})
            if sc.get("memory"):
                if result["calculation"]:
                    memory = session_memory.merge_calculation(memory, result["calculation"].get("inputs"))
                if result.get("memory_updates") and hasattr(session_memory, "merge_updates"):
                    memory = session_memory.merge_updates(memory, result["memory_updates"])

            fails = check(expect, result)
            wrong_tool = tool_selection_error(expect, result)
            words = jargon_count(result["content"])
            passed += not fails
            total += 1
            selection_errors += wrong_tool
            if legacy:
                legacy_passed += not fails
                legacy_total += 1
                legacy_selection_errors += wrong_tool
            jargon.append(words)
            called = tools_called(result)
            print(f"  {'OK  ' if not fails else 'FAIL'} {turn['user'][:30]} | 도구 {called}"
                  + (" (대체 기대)" if fallback else "") + (" | 도구 선택 오류" if wrong_tool else "")
                  + f" | 어려운 용어 {words}개 | {seconds[-1]:.1f}초"
                  + (f" | {'; '.join(fails)}" if fails else ""))
            rows.append({"id": sc["id"], "group": sc.get("group"), "source": source, "user": turn["user"],
                         "reply": result["content"], "fails": fails, "fallback": fallback,
                         "tool_selection_error": wrong_tool, "tools": called,
                         "jargon": words, "searches": result["searches"],
                         "calculation": result["calculation"] and {"inputs": result["calculation"]["inputs"],
                                                                   "total": result["calculation"]["total"]}})

    summary = {
        "turn_pass": f"{passed}/{total}",
        "turn_pass_rate": round(passed / total, 2),
        "legacy_turn_pass": f"{legacy_passed}/{legacy_total}",
        "tool_selection_errors": selection_errors,
        "legacy_tool_selection_errors": legacy_selection_errors,
        "jargon_per_reply": round(sum(jargon) / len(jargon), 2),
        "avg_seconds": round(sum(seconds) / len(seconds), 1),
        "mode": mode,
        "prompt_chars": {source: prompt_chars(source) for source in sources},
    }
    print(f"\n턴 통과 {summary['turn_pass']} ({summary['turn_pass_rate']:.0%}) | "
          f"기존 문항 {summary['legacy_turn_pass']} | 도구 선택 오류 {selection_errors}턴(기존 {legacy_selection_errors}턴) | "
          f"답변당 어려운 용어 {summary['jargon_per_reply']}개 | 평균 {summary['avg_seconds']}초 | 모드 {mode}")

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
