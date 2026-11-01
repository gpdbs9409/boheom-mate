"""상병마스터 CSV → data/kcd/kcd_index.sqlite3 (오프라인 일회성 명령, Req 10.12).

    .venv/bin/python -m boheom_mate.jobs.build_kcd_index data/kcd/<상병마스터>.csv \\
        --downloaded 2026-11-XX --version "<파일 버전>" --license "<운영자가 확인한 이용 조건>"
    .venv/bin/python -m boheom_mate.jobs.build_kcd_index --check   # 색인 요약·샘플 조회(스모크)

입력은 CSV(UTF-8 또는 CP949 자동 판별)만 받는다. 원본이 xlsx면 엑셀에서 CSV로 저장한다(새 의존성 없음).
--license는 운영자가 hira.or.kr 자료실에서 이용 조건을 확인한 뒤에만 채운다(G4). 없으면 만들지 않는다.
외부 호출이 없다.
"""
import argparse
import csv
import io
import sys
from pathlib import Path

from boheom_mate.store import kcd_index

# 상병마스터 열 이름 가정(G4). 실제 파일을 받아 --check로 확인한 뒤 여기 한 곳만 고친다.
KCD_COLUMNS = {"code": "상병기호", "name_ko": "한글명", "name_en": "영문명"}
REQUIRED = ("code", "name_ko")
ENCODINGS = ("utf-8-sig", "cp949")
SAMPLES = ("N39.3", "감기", "우울")


class ColumnError(ValueError):
    """CSV에 KCD_COLUMNS의 필수 열이 없다. columns에 실제 열 목록을 담는다."""

    def __init__(self, missing, columns):
        self.missing = list(missing)
        self.columns = list(columns)
        super().__init__(f"필요한 열이 없습니다: {', '.join(self.missing)}. "
                         f"실제 열: {', '.join(self.columns) or '(없음)'}. KCD_COLUMNS를 고치세요")


def decode(data: bytes) -> str:
    """UTF-8(BOM 포함)을 먼저 시도하고, 안 되면 CP949로 읽는다."""
    for enc in ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 인코딩을 알 수 없습니다(시도: {', '.join(ENCODINGS)})")


def read_rows(csv_path, columns=None):
    """CSV를 읽어 [{"code", "name_ko", "name_en"}]로 돌려준다. 필수 열이 없으면 ColumnError."""
    columns = columns or KCD_COLUMNS
    path = Path(csv_path)
    if path.suffix.lower() != ".csv":
        raise ValueError(f"CSV 파일만 받습니다: {path.name}")
    reader = csv.DictReader(io.StringIO(decode(path.read_bytes()), newline=""))
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    missing = [columns[k] for k in REQUIRED if columns[k] not in header]
    if missing:
        raise ColumnError(missing, header)
    en = columns.get("name_en")
    has_en = bool(en) and en in header
    return [
        {"code": row.get(columns["code"]) or "",
         "name_ko": row.get(columns["name_ko"]) or "",
         "name_en": (row.get(en) or "") if has_en else ""}
        for row in reader
    ]


def build(csv_path, *, downloaded, version, license, path=None):
    """CSV를 읽어 색인을 만든다. --license가 비면 CSV를 읽기 전에 거부한다."""
    if not (license or "").strip():
        raise ValueError("--license(운영자가 확인한 이용 조건)가 없으면 색인을 만들지 않습니다")
    rows = read_rows(csv_path)
    return kcd_index.build(rows, source_file=Path(csv_path).name, downloaded=downloaded,
                           version=version, license=license, path=path)


def check(path=None, out=None):
    """색인 요약과 샘플 조회 결과를 출력한다. 색인을 쓸 수 없으면 False."""
    out = out or sys.stdout
    if not kcd_index.available(path):
        print(f"색인 없음 또는 이용 조건(meta.license) 없음: {kcd_index._path(path)}", file=out)
        return False
    for key, value in kcd_index.meta(path).items():
        print(f"{key}: {value}", file=out)
    print(f"source_note: {kcd_index.source_note(path)}", file=out)
    for text in SAMPLES:
        hits = kcd_index.lookup(text, limit=3, path=path)
        shown = ", ".join(f"{h['code']} {h['name_ko']}" for h in hits) or "(없음)"
        print(f"lookup({text!r}): {shown}", file=out)
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="build_kcd_index", description="상병마스터 CSV로 KCD 로컬 색인을 만든다")
    p.add_argument("csv", nargs="?", help="상병마스터 CSV 경로(UTF-8 또는 CP949)")
    p.add_argument("--downloaded", help="파일을 받은 날짜(YYYY-MM-DD)")
    p.add_argument("--version", help="파일 버전")
    p.add_argument("--license", help="운영자가 hira.or.kr에서 확인한 이용 조건")
    p.add_argument("--check", action="store_true", help="색인 요약·샘플 조회만 한다")
    p.add_argument("--index", help=f"색인 파일 경로(기본 {kcd_index.INDEX_PATH})")
    args = p.parse_args(argv)

    if args.check and not args.csv:
        return 0 if check(args.index) else 1
    if not args.csv:
        p.error("CSV 경로가 필요합니다(또는 --check)")
    missing = [f"--{k}" for k in ("license", "downloaded", "version") if not (getattr(args, k) or "").strip()]
    if missing:
        p.error(f"{', '.join(missing)}가 필요합니다. --license 없이는 색인을 만들지 않습니다")
    try:
        result = build(args.csv, downloaded=args.downloaded, version=args.version,
                       license=args.license, path=args.index)
    except ColumnError as e:
        print(str(e), file=sys.stderr)
        print("실제 열 목록:", file=sys.stderr)
        for name in e.columns:
            print(f"  - {name}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(f"색인 생성 실패: {e}", file=sys.stderr)
        return 1
    print(f"색인 생성: {result['path']} (행 {result['row_count']}, 건너뜀 {result['skipped']})")
    if args.check:
        return 0 if check(args.index) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
