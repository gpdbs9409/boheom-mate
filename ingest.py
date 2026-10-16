"""기능 1: 약관 넣기.

data/ 폴더의 약관 PDF를 읽어 "제○조" 단위로 자르고,
OpenAI로 임베딩해 chroma_db/에 저장한다.

화면 없이 실행하려면:  python ingest.py
"""
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = BASE_DIR / "chroma_db"
COLLECTION_NAME = "yakgwan"
EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 20

# 목차의 "제1조(목적) ·········· 52" 같은 줄도 조항 제목처럼 잡히기 때문에,
# 제목 바로 뒤에 점선이 오거나 본문이 이보다 짧은 조각은 버린다.
MIN_BODY_CHARS = 20
TOC_LEADER = re.compile(r"[·.…]{5,}")
# 긴 조항은 이 길이 안팎으로 줄 단위로 쪼갠다. 한 조각에 여러 내용이 섞이면
# 검색이 엉뚱한 조각을 고르기 때문이다(예: 3.5만 자짜리 제35조가 모든 질문에서 1위).
CHUNK_CHARS = 1500
# 줄 하나가 아주 긴 예외적인 경우에도 임베딩 입력 제한을 넘지 않도록 자른다.
MAX_EMBED_CHARS = 4000

# 줄 맨 앞의 "제3조(보상하는 손해)" 같은 조항 제목. "제3조의2"도 잡는다.
# 본문 속 "제3조에 따라"는 괄호 제목이 없어서 잡히지 않는다.
ARTICLE_PATTERN = re.compile(
    r"^[ \t]*(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*[(（]([^)）\n]+)[)）]",
    re.MULTILINE,
)

# 약관 안의 큰 구역. 같은 "제3조"라도 보통약관과 특약1, 특약2에 각각 따로 있어서
# 어느 구역의 조항인지 꼬리표를 붙여야 검색과 화면 표시가 헷갈리지 않는다.
# 보통약관이 시작되기 전(표지, 목차, 약관 이용 가이드)은 모두 "안내자료"로 본다.
MAIN_START = re.compile(r"^기본형 실손의료보험", re.MULTILINE)
SECTION_PATTERNS = [
    (re.compile(r"^\d\.\s*(특별약관\d\([^)\n]+\))", re.MULTILINE), lambda m: m.group(1)),
    # 제도성 특약은 "1. 이륜자동차 운전중 상해 부담보" 바로 다음 줄에 제1조가 온다
    (re.compile(r"^\d{1,2}\.\s*([^\n]+?)\s*\n\s*제\s*1\s*조", re.MULTILINE), lambda m: f"제도성 특약: {m.group(1)}"),
    (re.compile(r"^별표\d[\d-]*\s+([^\n]+)", re.MULTILINE), lambda m: f"별표: {m.group(1).strip()}"),
    (re.compile(r"^법규\d+\s+(.+?)\s*\[", re.MULTILINE), lambda m: f"관련 법규: {m.group(1)}"),
]
GUIDE = "안내자료"
MAIN = "보통약관"


def _is_toc(text, pos):
    """목차 줄("제1관 일반사항 ······ 52")인지."""
    line_end = text.find("\n", pos)
    return TOC_LEADER.search(text[pos:line_end if line_end != -1 else None]) is not None


def find_sections(text):
    """(시작 위치, 구역 이름) 목록. 위치 순서로 정렬돼 있다. 목차 줄은 건너뛴다."""
    start = next((m for m in MAIN_START.finditer(text) if not _is_toc(text, m.start())), None)
    if not start:
        return [(0, MAIN)]
    marks = [(0, GUIDE), (start.start(), MAIN)]
    for pattern, label in SECTION_PATTERNS:
        marks += [
            (m.start(), label(m))
            for m in pattern.finditer(text, start.start())
            if not _is_toc(text, m.start())
        ]
    return sorted(marks)


def section_at(marks, pos):
    current = marks[0][1]
    for mark_pos, label in marks:
        if mark_pos > pos:
            break
        current = label
    return current


# 임베딩할 때 조각 앞에 "[구역] 제N조(제목)"을 붙일지. 실험 E1/E1b에서 비교한다
EMBED_WITH_LABEL = True


def embed_text(article):
    """임베딩할 글. 화면과 DB 원문에는 꼬리표를 붙이지 않는다."""
    if not EMBED_WITH_LABEL:
        return article["text"]
    return f'[{article["section"]}] {article["article_no"]}({article["title"]})\n{article["text"]}'


def list_pdfs():
    return sorted(DATA_DIR.glob("*.pdf"))


def extract_text(pdf_path):
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def split_articles(text):
    """약관 전문을 조항 단위로 나눈다.

    조항은 다음 조항 제목이나 다음 구역(특약, 별표, 법규) 시작 지점에서 끝난다.
    조항 제목이 없는 구역(별표 등)은 구역 전체를 하나의 조항처럼 다룬다.
    첫 조항 앞의 내용(표지 등)은 "머리말"로 묶는다.
    반환값: (조항 목록, 너무 짧아서 버린 조각 수)
    """
    # "제5조의2([별표5]...)"처럼 괄호 안이 대괄호로 시작하면 본문 속 인용이지 조항 제목이 아니다
    matches = [m for m in ARTICLE_PATTERN.finditer(text) if not m.group(2).lstrip().startswith(("[", "［"))]
    marks = find_sections(text)
    articles = []
    skipped = 0

    head = text[: matches[0].start()] if matches else text
    if head.strip():
        articles.append({"section": GUIDE, "article_no": "머리말", "title": "", "text": head.strip()})

    # 조항 시작점과 구역 시작점을 한 줄로 세워, 각 조각이 다음 시작점 직전까지 가게 한다
    starts = [(m.start(), m) for m in matches] + [(pos, None) for pos, _ in marks[1:]]
    starts.sort(key=lambda x: x[0])
    for i, (pos, m) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        section = section_at(marks, pos)
        chunk = text[pos:end].strip()
        if m is None:
            # 구역 시작부터 첫 조항 전까지. 별표처럼 조항이 없는 구역은 여기에 내용이 다 들어 있다
            if len(chunk) < 200:
                continue
            kind, _, name = section.partition(": ")
            article_no, title = (kind, name) if name else (section, "")
        else:
            body = text[m.end():end].strip()
            first_line = body.split("\n", 1)[0]
            if len(body) < MIN_BODY_CHARS or TOC_LEADER.search(first_line):
                skipped += 1
                continue
            if section == GUIDE:
                # 가이드 페이지 속 "제14조 (알릴 의무...) P61" 같은 줄은 진짜 조항 제목이 아니다
                article_no, title = GUIDE, "약관 이용 가이드"
            else:
                article_no, title = re.sub(r"\s+", "", m.group(1)), m.group(2).strip()
        articles.extend(_split_long({
            "section": section,
            "article_no": article_no,
            "title": title,
            "text": chunk,
        }))
    return articles, skipped


def _split_long(article):
    """CHUNK_CHARS보다 긴 조항을 줄 단위로 나눈다. 두 번째 조각부터는 조항 제목을 앞에 붙인다."""
    if len(article["text"]) <= CHUNK_CHARS:
        return [article]

    pieces, current = [], ""
    for line in article["text"].split("\n"):
        if current and len(current) + len(line) > CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        pieces.append(current)

    header = f'{article["article_no"]}({article["title"]}) (이어서)'
    if article["section"] == GUIDE:
        header = "(약관 이용 가이드 이어서)"
    return [
        {**article, "text": piece.strip() if i == 0 else f"{header}\n{piece.strip()}"}
        for i, piece in enumerate(pieces)
    ]


def build_index(articles, source, on_progress=None):
    """조항을 임베딩해 Chroma에 저장한다. 같은 파일을 다시 넣으면 기존 것을 교체한다.

    반환값: 저장 후 DB에 있는 전체 조항 수
    """
    import chromadb
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(BASE_DIR / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(".env 파일에 OPENAI_API_KEY가 없습니다.")

    client = OpenAI()
    texts = [a["text"] for a in articles]
    embeddings = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = [embed_text(a)[:MAX_EMBED_CHARS] for a in articles[start:start + BATCH_SIZE]]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        embeddings.extend(item.embedding for item in response.data)
        if on_progress:
            on_progress(min(start + BATCH_SIZE, len(texts)) / len(texts))

    db = chromadb.PersistentClient(path=str(DB_DIR))
    collection = db.get_or_create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    old = collection.get(where={"source": source})
    if old["ids"]:
        collection.delete(ids=old["ids"])

    collection.add(
        ids=[f"{source}-{i}" for i in range(len(articles))],
        documents=texts,
        embeddings=embeddings,
        metadatas=[
            {"source": source, "section": a["section"], "article_no": a["article_no"], "title": a["title"]}
            for a in articles
        ],
    )
    return collection.count()


def main():
    pdfs = list_pdfs()
    if not pdfs:
        print("data/ 폴더에 약관 PDF를 넣어주세요.")
        return
    for pdf in pdfs:
        articles, skipped = split_articles(extract_text(pdf))
        if not articles:
            print(f"{pdf.name}: 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF인지 확인하세요.")
            continue
        total = build_index(articles, pdf.name)
        print(f"{pdf.name}: 조항 {len(articles)}개 저장 (짧은 조각 {skipped}개 제외), DB 전체 {total}개")


if __name__ == "__main__":
    main()
