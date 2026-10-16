"""표현만 다른 같은 질문이 같은 근거를 찾는지 잰다.

실행 (my-app 폴더에서):
    .venv/bin/python eval/run_paraphrase.py --note "실험 이름"

채점 기준
- 근거 겹침: 같은 묶음의 질문끼리 상위 5개 조항(구역+조항 번호)이 얼마나 겹치는가.
  두 질문씩 짝지어 (겹치는 조항 수 / 합친 조항 수)를 구해 평균한다. 100%면 완전히 같다.
- 1등 일치: 묶음 안의 질문들이 모두 같은 조항을 1등으로 찾았는가
"""
import argparse
import json
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR.parent))

import rag  # noqa: E402

DEFAULT_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"
TOP_N = 5


def article_key(a):
    return f'{a["section"]} {a["article_no"]}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    groups = json.loads((EVAL_DIR / "paraphrases.json").read_text(encoding="utf-8"))
    rows = []
    overlaps, top1_same = [], []
    for g in groups:
        results = []
        for q in g["questions"]:
            debug = {}
            articles = rag.search(q, args.source, debug=debug)
            keys = []
            for a in articles:
                if article_key(a) not in keys:
                    keys.append(article_key(a))
            results.append({"question": q, "analysis": debug.get("analysis"), "top": keys[:TOP_N]})
        pair_scores = [
            len(set(a["top"]) & set(b["top"])) / len(set(a["top"]) | set(b["top"]))
            for a, b in combinations(results, 2)
        ]
        overlap = sum(pair_scores) / len(pair_scores)
        same = len({r["top"][0] for r in results}) == 1
        overlaps.append(overlap)
        top1_same.append(same)
        rows.append({"id": g["id"], "overlap": overlap, "top1_same": same, "results": results})
        print(f'{g["id"]}  근거 겹침 {overlap:.0%}  1등 일치 {"O" if same else "X"}')
        for r in results:
            print(f'      {r["question"]}')
            print(f'        → {" / ".join(r["top"][:3])}')

    summary = {
        "overlap": sum(overlaps) / len(overlaps),
        "top1_same": sum(top1_same) / len(top1_same),
    }
    print(f'\n근거 겹침 평균: {summary["overlap"]:.0%}')
    print(f'1등 일치: {summary["top1_same"]:.0%}')

    results_dir = EVAL_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    out = results_dir / f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_paraphrase.json'
    settings = {"note": args.note, **rag.SEARCH_SETTINGS}
    out.write_text(
        json.dumps({"settings": settings, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n상세 결과 저장: {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
