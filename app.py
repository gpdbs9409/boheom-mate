"""보험메이트 화면.

- 질문하기: 기능 2. 약관을 근거로 답하고, 질문과 답변을 저장해 목록으로 다시 본다 (새로고침해도 남음)
- 약관 넣기: 기능 1

실행:  streamlit run app.py
"""
import streamlit as st

import display
import ingest
import question_store
import rag

st.set_page_config(page_title="보험메이트")
st.title("보험메이트")
st.caption("보험약관 기반 AI 상담 도우미")

tab_questions, tab_ingest = st.tabs(["질문하기", "약관 넣기"])

DEFAULT_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"


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
        else:
            with st.spinner("약관을 찾아 답변을 만드는 중..."):
                try:
                    result = rag.answer(text.strip(), source)
                except Exception as e:
                    st.error(f"답변을 만들지 못했습니다: {e}")
                else:
                    question_store.add(
                        text.strip(), source, result["answer"], result["found"], result["evidence"],
                        result["summary"],
                    )

    questions = question_store.list_all()
    st.subheader(f"질문 기록 ({len(questions)}개)")

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
                question_store.delete(q["id"])
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
