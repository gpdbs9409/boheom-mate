"""보험메이트 화면.

- 질문하기: 기능 2. 약관을 근거로 답하고, 질문과 답변을 저장해 목록으로 다시 본다
  (목록은 지금 브라우저 세션의 질문만 보이고, 새로고침하면 새 세션이 된다. 하루 전체 질문 수에 한도가 있다)
- 약관 넣기: 기능 1

실행:  streamlit run app.py
"""
import os
import threading
import uuid

import streamlit as st

import display
import ingest
import keyword_search
import question_store
import rag

st.set_page_config(page_title="보험메이트 - 실손보험 약관 AI 질의응답")
st.title("보험메이트")
st.caption("보험약관 기반 AI 상담 도우미")

tab_questions, tab_ingest = st.tabs(["질문하기", "약관 넣기"])

DEFAULT_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"
DAILY_LIMIT = int(os.getenv("DAILY_QUESTION_LIMIT", "100"))  # 모든 방문자를 합친 하루 질문 수

# 로그인이 없으므로 브라우저 세션마다 id를 하나 만들어, 질문 기록을 세션별로 나눈다
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
session_id = st.session_state.session_id


@st.cache_resource
def _warm_up():
    """키워드 검색 색인(BM25)을 처음 만드는 데 서버(t3.micro)에서 약 18초 걸린다.
    앱이 뜬 뒤 첫 방문 때 뒤에서 미리 만들어, 질문하는 사람이 기다리지 않게 한다."""
    def build():
        try:
            keyword_search.search(["보험"], DEFAULT_SOURCE, 1)
        except Exception:
            pass  # 미리 못 만들어도 첫 질문 때 다시 만든다
    threading.Thread(target=build, daemon=True).start()


_warm_up()


def short_name(source):
    return source.removesuffix(".pdf").removesuffix("_약관").replace("_", " ")


# ---------- 질문하기 ----------
def render_questions():
    sources = rag.list_sources()
    if not sources:
        st.info("아직 DB에 저장된 약관이 없습니다. [약관 넣기] 탭에서 먼저 저장하세요.")
        return

    with st.form("question_form", clear_on_submit=True):
        # 개선 작업은 기본 약관으로 먼저 하고 있어서, 처음에는 기본 약관을 골라 둔다
        default = sources.index(DEFAULT_SOURCE) if DEFAULT_SOURCE in sources else 0
        source = st.selectbox("가입한 상품", sources, index=default, format_func=short_name)
        text = st.text_area(
            "질문",
            placeholder="예: 맹장 수술로 입원했는데 실손보험금을 받을 수 있나요?",
            height=100,
        )
        submitted = st.form_submit_button("질문하기", type="primary", width="stretch")

    if submitted:
        if not text.strip():
            st.warning("질문을 입력해 주세요.")
        elif not question_store.use_daily_quota(DAILY_LIMIT):
            st.warning(f"오늘 질문 한도({DAILY_LIMIT}개)를 모두 사용했습니다. 내일 다시 이용해 주세요.")
        else:
            with st.spinner("약관을 찾아 답변을 만드는 중..."):
                try:
                    result = rag.answer(text.strip(), source)
                except Exception as e:
                    st.error(f"답변을 만들지 못했습니다: {e}")
                else:
                    question_store.add(
                        text.strip(), session_id, source, result["answer"], result["found"], result["evidence"],
                        result["summary"],
                    )

    st.caption(f"오늘 남은 질문 {max(DAILY_LIMIT - question_store.used_today(), 0)}개 (모든 방문자 합계)")

    questions = question_store.list_mine(session_id)
    st.subheader(f"내 질문 기록 ({len(questions)}개)")
    st.caption("이 브라우저 창에서 한 질문만 보이며, 새로고침하면 목록이 비워집니다.")

    if not questions:
        st.info("아직 질문이 없습니다. 위에 질문을 입력해 보세요.")

    for q in questions:
        with st.container(border=True):
            col_text, col_button = st.columns([5, 1], vertical_alignment="center")
            col_text.markdown(f"**Q. {q['text']}**")
            col_text.caption(
                " · ".join(filter(None, [q["created_at"], q["source"] and short_name(q["source"])]))
            )
            if q.get("summary"):
                col_text.caption(f"질문 요지: {q['summary']}")
            if col_button.button("삭제", key=f"delete_{q['id']}", width="stretch"):
                question_store.delete(q["id"], session_id)
                st.rerun()
            if q["found"] == 0:
                st.warning(q["answer"])
            elif q["answer"]:
                st.markdown(q["answer"])
            groups = display.group_evidence(q["evidence"])
            if groups:
                st.caption(f"근거 조항 {len(groups)}개 · 눌러서 원문 보기")
            for n, group in enumerate(groups, start=1):
                with st.expander(f"근거 {n} · {display.evidence_label(group)}"):
                    for i, text in enumerate(group["texts"]):
                        if i > 0:
                            st.caption("⋯ 같은 조항의 다른 부분 ⋯")
                        st.markdown(display.to_markdown(text))

    st.caption("이 답변은 약관 내용을 안내하는 참고용이며, 실제 보험금 지급 여부는 보험사 심사로 확정됩니다.")


with tab_questions:
    render_questions()


# ---------- 약관 넣기 ----------
@st.cache_data
def load_articles(path):
    return ingest.split_articles(ingest.extract_text(path))


def render_ingest():
    pdfs = ingest.list_pdfs()
    if not pdfs:
        st.info("`data/` 폴더에 약관 PDF를 넣고 새로고침하세요.")
        return

    pdf = st.selectbox("약관 파일", pdfs, format_func=lambda p: p.name)
    articles, skipped = load_articles(str(pdf))
    if not articles:
        st.error("텍스트를 추출하지 못했습니다. 스캔 이미지 PDF인지 확인하세요.")
        return

    # 1단계: 자른 결과 확인
    col1, col2, col3 = st.columns(3)
    col1.metric("조항 수", len(articles))
    col2.metric("제외한 조각", skipped, help="목차 줄처럼 본문이 거의 없는 조각")
    col3.metric("가장 긴 조항(글자)", max(len(a["text"]) for a in articles))

    st.dataframe(
        [
            {"구역": a["section"], "번호": a["article_no"], "제목": a["title"], "글자 수": len(a["text"])}
            for a in articles
        ],
        width="stretch",
        hide_index=True,
    )

    picked = st.selectbox(
        "조항 원문 미리보기",
        range(len(articles)),
        format_func=lambda i: f'[{articles[i]["section"]}] {articles[i]["article_no"]} {articles[i]["title"]}',
    )
    st.text_area("원문", articles[picked]["text"], height=250, disabled=True)

    # 2단계: 벡터 DB에 저장
    st.subheader("벡터 DB에 저장")
    st.write("자른 결과가 괜찮으면 저장하세요. OpenAI 임베딩 요금이 소액 발생합니다.")

    if st.button("저장하기", type="primary", key="ingest_save"):
        bar = st.progress(0.0, text="임베딩 중...")
        try:
            total = ingest.build_index(
                articles, pdf.name, on_progress=lambda p: bar.progress(p, text="임베딩 중...")
            )
        except Exception as e:
            bar.empty()
            st.error(f"저장하지 못했습니다: {e}")
        else:
            bar.empty()
            st.success(f"{len(articles)}개 조항을 저장했습니다. DB 전체 {total}개")


with tab_ingest:
    render_ingest()
