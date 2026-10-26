"""평가셋으로 RAG 성능을 채점한다.

실행 (my-app 폴더에서):
    .venv/bin/python eval/run_eval.py            # 개발용(dev) 20문항 × 3회
    .venv/bin/python eval/run_eval.py --split test   # 최종 확인용 10문항. 자주 돌리지 않는다
    .venv/bin/python eval/run_eval.py --split all
    .venv/bin/python eval/run_eval.py --retrieval-only --note "실험 이름"   # 검색만 채점. 빠르고 싸다

채점 기준
- 검색 적중: 검색된 조각 중 하나라도 evidence_contains 문구를 포함하는가 (답이 있는 질문만)
- 정답 순위(MRR): 정답 조각이 몇 등으로 나왔는가. 1등이면 1, 2등이면 1/2, 못 찾으면 0의 평균
- 상위 3 적중: 정답 조각이 3등 안에 들었는가. 답변 GPT는 앞쪽 조각을 더 잘 쓴다
- 거절 정확: 답이 있는 질문엔 답하고, 없는 질문엔 "확인 불가"라고 했는가 (전체)
- 답변 키워드: 답변에 answer_must_include 문구가 모두 들어 있는가 (답이 있는 질문만)
- 근거 일치: 같은 질문을 여러 번 돌렸을 때 근거 조항이 매번 같았는가 (--repeat 2 이상)

결과는 eval/results/에 날짜별로 저장되어, 고치기 전후를 비교할 수 있다.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR.parent))

from boheom_mate.core import rag  # noqa: E402

DEFAULT_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"


def norm(text):
    """PDF 줄바꿈·띄어쓰기 차이를 무시하고 비교하려고 공백을 모두 지운다."""
    return re.sub(r"\s+", "", text)


def grade(q, source, retrieval_only=False):
    debug = {}
    articles = rag.search(q["question"], source, debug=debug)
    if retrieval_only:
        result = {"found": None, "answer": "", "evidence": []}
    else:
        result = rag.answer(q["question"], source, articles=articles)

    retrieved = [norm(a["text"]) for a in articles]
    hit = rank = rr = top3 = None
    keywords_ok = None
    if q["answerable"]:
        ranks = [i for i, t in enumerate(retrieved, start=1) if any(norm(s) in t for s in q["evidence_contains"])]
        rank = ranks[0] if ranks else None
        hit = rank is not None
        rr = 1 / rank if rank else 0.0
        top3 = hit and rank <= 3
        if retrieval_only:
            pass
        elif result["found"]:
            keywords_ok = all(norm(k) in norm(result["answer"]) for k in q["answer_must_include"])
        else:
            keywords_ok = False

    return {
        "id": q["id"],
        "question": q["question"],
        "answerable": q["answerable"],
        "retrieval_hit": hit,
        "rank": rank,
        "rr": rr,
        "top3": top3,
        "refusal_ok": None if retrieval_only else result["found"] == q["answerable"],
        "keywords_ok": keywords_ok,
        "found": result["found"],
        "answer": result["answer"],
        "queries": debug.get("queries"),
        "keywords": debug.get("keywords"),
        "retrieved": [f'{a.get("section", "")} {a["article_no"]} {a["title"]}'.strip() for a in articles],
        "evidence": sorted(f'{a["article_no"]} {a["title"]}' for a in result["evidence"]),
    }


METRICS = [
    ("retrieval_hit", "검색 적중률"),
    ("top3", "상위 3 적중"),
    ("rr", "정답 순위(MRR)"),
    ("refusal_ok", "거절 정확도"),
    ("keywords_ok", "답변 키워드"),
]


def mark(values):
    """한 문항을 여러 번 돌린 결과를 "2/3"처럼 표시한다. 해당 없으면 "-"."""
    values = [v for v in values if v is not None]
    return f"{sum(values)}/{len(values)}" if values else " - "


def rate(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return sum(values) / len(values) if values else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--retrieval-only", action="store_true", help="답변 생성 없이 검색만 채점한다")
    parser.add_argument("--note", default="", help="실험 이름. 결과 파일에 함께 저장된다")
    parser.add_argument(
        "--repeat", type=int, default=None,
        help="같은 문항을 몇 번 돌릴지. GPT 답이 매번 조금씩 달라서 한 번만 보면 점수가 흔들린다",
    )
    args = parser.parse_args()
    if args.repeat is None:
        # 검색은 결과가 고정돼 있어서 1번이면 충분하다
        args.repeat = 1 if args.retrieval_only else 3

    questions = json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))
    if args.split != "all":
        questions = [q for q in questions if q["split"] == args.split]

    print(f"평가: {args.split} {len(questions)}문항 × {args.repeat}회 / 상품: {args.source}\n")
    print("ID   검색  순위  거절  키워드  근거일치  질문")
    runs = [[] for _ in range(args.repeat)]
    consistent = []
    for q in questions:
        graded = [grade(q, args.source, args.retrieval_only) for _ in range(args.repeat)]
        for run, row in zip(runs, graded):
            run.append(row)
        same = len({tuple(r["evidence"]) for r in graded}) == 1
        consistent.append(same)
        print(
            f'{q["id"]}  {mark([r["retrieval_hit"] for r in graded])}  '
            f'{str(graded[0]["rank"] or "-"):>3}  '
            f'{mark([r["refusal_ok"] for r in graded])}  '
            f'{mark([r["keywords_ok"] for r in graded])}   {"O" if same else "X"}       {q["question"]}'
        )

    summary = {}
    print()
    for key, label in METRICS:
        if args.retrieval_only and key in ("refusal_ok", "keywords_ok"):
            continue
        per_run = [rate(run, key) for run in runs]
        mean = sum(per_run) / len(per_run)
        summary[key] = {"mean": mean, "min": min(per_run), "max": max(per_run)}
        if key == "rr":
            print(f"{label}: {mean:.2f}  (1.00이 만점)")
        else:
            print(f"{label}: 평균 {mean:.0%} (회차별 {min(per_run):.0%} ~ {max(per_run):.0%})")
    if not args.retrieval_only and args.repeat > 1:
        summary["evidence_consistent"] = sum(consistent) / len(consistent)
        print(f"근거 일치율: {summary['evidence_consistent']:.0%} (같은 질문 {args.repeat}번에 근거가 모두 같았던 문항 비율)")
    rows = [row for run in runs for row in run]

    results_dir = EVAL_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    mode = "retrieval" if args.retrieval_only else "full"
    out = results_dir / f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_{args.split}_{mode}.json'
    settings = {
        "note": args.note,
        "mode": mode,
        "repeat": args.repeat,
        "answer_model": rag.ANSWER_MODEL,
        "top_k": rag.TOP_K,
        "chunk_chars": rag.ingest.CHUNK_CHARS,
        "temperature": rag.TEMPERATURE,
        "glossary_terms": len(rag.glossary.TERMS),
        **rag.SEARCH_SETTINGS,
    }
    out.write_text(
        json.dumps({"settings": settings, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n상세 결과 저장: {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
