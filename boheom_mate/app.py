"""보험메이트 화면.

- 상담하기: 기능 2·3. 이어지는 채팅으로 약관을 근거로 답하고, 영수증 금액으로 예상 보험금을 계산한다
  (대화와 상담 메모는 지금 브라우저 세션 것만 보이고, 새로고침하면 새 대화가 된다. 하루 전체 질문 수에 한도가 있다)
- 약관 넣기: 기능 1

실행:  streamlit run boheom_mate/app.py
"""
import os
import sys
import threading
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

# streamlit run은 스크립트 폴더(boheom_mate/)만 import 경로에 넣으므로 프로젝트 루트를 직접 넣는다
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boheom_mate.ui import chat
from boheom_mate.ui import display
from boheom_mate.core import ingest
from boheom_mate.core import keyword_search
from boheom_mate.core import memory as session_memory
from boheom_mate.core import outbound
from boheom_mate.integrations import mcp_client
from boheom_mate.store import products
from boheom_mate.store import question_store
from boheom_mate.core import rag

# streamlit은 프로젝트 루트에서 실행하므로(WorkingDirectory=루트) 자산 경로는 CWD 기준 그대로 둔다
LOGO_TEXT = "assets/logo_text.svg"
LOGO_CHARACTER = "assets/logo_character.svg"

st.set_page_config(page_title="보험메이트 - 실손보험 약관 AI 질의응답", page_icon=LOGO_CHARACTER)
st.logo(LOGO_TEXT, size="large")  # 사이드바가 없으면 icon_image가 대신 쓰이므로 텍스트 로고만 넘긴다
# 글꼴: Pretendard (아이콘 글꼴은 건드리지 않도록 글자 요소에만 적용)
st.html("""<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
html, body, p, div, span, label, input, textarea, button, h1, h2, h3, h4, li, td, th {
  font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}
[data-testid="stIconMaterial"], .material-symbols-rounded { font-family: "Material Symbols Rounded" !important; }
img[data-testid="stHeaderLogo"] { height: 3rem; max-width: none; }  /* 기본 32px는 영문 글자가 안 읽혀서 키운다 */
</style>""")
st.markdown("#### 실손보험, 보장되는지 얼마 받는지 바로 알려 드려요")
st.caption("가입한 보험의 약관을 근거로 답하고, 영수증 금액으로 예상 보험금을 계산해요.")

# 약관 넣기는 운영자용이다. 배포판에서는 주간 약관 확인(Slack 승인)이 넣으므로, 로컬에서 SHOW_INGEST_TAB=1일 때만 보인다
SHOW_INGEST = os.getenv("SHOW_INGEST_TAB") == "1"
if SHOW_INGEST:
    tab_questions, tab_ingest = st.tabs(["상담하기", "약관 넣기"])
else:
    (tab_questions,) = st.tabs(["상담하기"])

DEFAULT_SOURCE = chat.CALCULATOR_SOURCE  # 첫 방문 때 미리 색인을 만들 약관
DAILY_LIMIT = int(os.getenv("DAILY_QUESTION_LIMIT", "100"))  # 모든 방문자를 합친 하루 질문 수

# 로그인이 없으므로 브라우저 세션마다 id를 하나 만들어, 대화를 세션별로 나눈다
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
session_id = st.session_state.session_id


@st.cache_resource
def _warm_up():
    """키워드 검색기(LangChain BM25Retriever)를 처음 만드는 데 서버(t3.micro)에서 시간이 걸린다.
    앱이 뜬 뒤 첫 방문 때 뒤에서 미리 만들어, 질문하는 사람이 기다리지 않게 한다."""
    def build():
        try:
            rag.warm_up(DEFAULT_SOURCE)
        except Exception:
            pass  # 미리 못 만들어도 첫 질문 때 다시 만든다
    threading.Thread(target=build, daemon=True).start()


_warm_up()


# ---------- 상담하기 ----------
EXAMPLES = [
    "감기로 동네 병원 갔는데 실손 돼요?",
    "도수치료 10만 원 냈는데 얼마 돌려받아요?",
    "맹장 수술로 입원했어요. 보장되나요?",
]


def render_calculation(calc):
    st.markdown(f"**💰 예상 보험금 :red[{calc['total']:,}원]**")
    table = pd.DataFrame(
        [{"항목": i["label"], "받는 돈": f"{i['amount']:,}원", "계산": i["how"], "근거": i["article"]}
         for i in calc["items"]]
    )
    red = "color: #D92D20; font-weight: 600"
    st.dataframe(table.style.map(lambda _: red, subset=["받는 돈", "근거"]), hide_index=True, width="stretch")
    for note in calc["notes"]:
        st.caption("· " + note.replace("~", "\\~"))  # "2009.10~2017.3"의 ~가 취소선으로 바뀌지 않게


def render_evidence(evidence):
    # 결론을 뒷받침한 핵심 문장이 확인된 근거를 먼저 보여 준다
    groups = sorted(display.group_evidence(evidence), key=lambda g: not any(g["highlights"]))
    if groups:
        st.caption(f"근거 조항 {len(groups)}개 · 눌러서 원문 보기")
    for n, group in enumerate(groups, start=1):
        with st.expander(f":red[**근거 {n}**] · {display.evidence_label_md(group)}"):
            for i, (text, highlights) in enumerate(zip(group["texts"], group["highlights"])):
                if i > 0:
                    st.caption("⋯ 같은 조항의 다른 부분 ⋯")
                st.markdown(display.to_markdown(text, highlights))


def avatar(role):
    return LOGO_CHARACTER if role == "assistant" else None


def render_message(m):
    with st.chat_message(m["role"], avatar=avatar(m["role"])):
        st.markdown(display.highlight_answer(m["content"]) if m["role"] == "assistant" else m["content"])
        if m.get("calculation"):
            render_calculation(m["calculation"])
        if m.get("summary"):
            st.caption(f"질문 요지: {m['summary']}")
        render_evidence(m.get("evidence") or [])


def pick_product(sources):
    """보험사 → 보험 종류 → 상품 이름(검색) 순서로 고른다. 상담할 수 있으면 약관 파일 이름을 반환한다."""
    with st.container(border=True):
        st.markdown("**내 보험 고르기**")
        col_company, col_category = st.columns(2)
        company = col_company.selectbox("보험사", [products.ALL] + products.COMPANIES)
        category = col_category.selectbox("보험 종류", [products.ALL] + products.CATEGORIES)
        keyword = st.text_input("보험 이름 검색", placeholder="예: 실손, 다이렉트, 간편")
        found = products.search(company, category, keyword)
        if not found:
            st.info("조건에 맞는 보험을 아직 준비하지 못했어요. 지금은 KB손해보험 실손의료보험부터 준비하고 있어요.")
            return None
        col_product, col_new = st.columns([4, 1], vertical_alignment="bottom")
        product = col_product.selectbox(f"보험 상품 ({len(found)}개)", found, format_func=products.label)
        if col_new.button("새 대화", width="stretch"):
            question_store.clear_memory(st.session_state.session_id)  # 화면 안내대로 이전 대화의 기억을 지운다
            st.session_state.session_id = uuid.uuid4().hex
            st.rerun()
        # 실손은 가입한 시점의 약관이 적용되므로, 판이 여러 개면 가입한 판을 고르게 한다
        versions = products.editions(product)
        if len(versions) > 1:
            product = st.selectbox("약관 판 (가입한 시점)", versions, format_func=lambda e: f'{e["edition"]}판')
    if product["source"] not in sources:
        st.info(f"'{product['product']}' 약관은 아직 준비 중이에요. 준비된 상품을 골라 주세요.")
        return None
    return product["source"]


def start_outbound(action, messages, source, memory, reason=""):
    """보내기 미리보기를 만든다. 실제 전송은 render_outbound에서 사용자가 버튼을 눌렀을 때만 한다."""
    last_q = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    last_a = next((m for m in reversed(messages) if m["role"] == "assistant"), {})
    if action == "report_issue":
        draft = outbound.issue_draft(last_q, last_a.get("content", ""), last_a.get("evidence"), source, reason)
    else:
        draft = outbound.handoff_draft(last_q, session_memory.describe(memory), source, reason)
    for key in ("outbound_text", "outbound_agree"):  # 새 초안이 이전 입력칸 값에 덮이지 않게
        st.session_state.pop(key, None)
    st.session_state.outbound = {"action": action, "text": outbound.preview_text(action, draft)}


def render_outbound():
    """MCP로 보내기 전 미리보기. 사용자가 내용을 고치고 동의해야만 보낸다(GPT는 보낼 수 없다)."""
    pending = st.session_state.get("outbound")
    if not pending:
        return
    action = pending["action"]
    sent = st.session_state.get("outbound_sent", 0)
    with st.container(border=True):
        st.markdown(f"**{mcp_client.ACTIONS[action]['title']} 미리보기**")
        st.caption("아래 내용이 그대로 전송됩니다. 병명·연락처 같은 개인정보는 지우거나 고쳐 주세요. "
                   "주민번호·전화번호·이메일은 자동으로 가립니다.")
        text = st.text_area("보낼 내용", pending["text"], height=220, key="outbound_text")
        agree = st.checkbox("내용을 확인했고, 이 내용을 보내는 데 동의합니다.", key="outbound_agree")
        col_send, col_cancel = st.columns(2)
        if col_send.button("보내기", type="primary", disabled=not agree or sent >= outbound.MAX_SENDS_PER_SESSION):
            try:
                mcp_client.execute(action, outbound.from_preview(action, text))
                st.session_state.outbound_sent = sent + 1
                st.session_state.outbound = None
                st.success("보냈어요.")
            except mcp_client.MCPError as e:
                st.error(f"보내지 못했어요: {e}")
        if col_cancel.button("취소"):
            st.session_state.outbound = None
            st.rerun()
        if sent >= outbound.MAX_SENDS_PER_SESSION:
            st.caption(f"이 대화에서는 {outbound.MAX_SENDS_PER_SESSION}번까지만 보낼 수 있어요.")


def render_questions():
    sources = rag.list_sources()
    if not sources:
        st.info("아직 DB에 저장된 약관이 없습니다. [약관 넣기] 탭에서 먼저 저장하세요.")
        return

    source = pick_product(sources)
    if not source:
        return
    if not chat.calculator_available(source):
        st.caption("이 상품은 약관에서 계산 기준을 확인하지 못해 보험금 계산 없이 약관 내용만 안내해 드려요.")

    messages = question_store.list_messages(session_id)
    memory = question_store.get_memory(session_id)
    remembered = session_memory.describe(memory)
    if remembered:
        with st.expander(f"이 대화에서 기억하는 내용 ({len(remembered)}개)"):
            st.markdown("\n".join(f"- {line}" for line in remembered))
            st.caption("이 대화 안에서만 기억하고, 새 대화를 시작하거나 하루가 지나면 지워져요.")
            if st.button("기억 지우기"):
                question_store.clear_memory(session_id)
                st.rerun()
    if not messages:
        with st.chat_message("assistant", avatar=LOGO_CHARACTER):
            st.markdown(
                "안녕하세요, 보험메이트예요. 실손보험으로 **보장되는지**, **얼마 받는지** 물어보세요.\n\n"
                "병원 영수증의 금액을 알려 주시면 예상 보험금도 계산해 드려요."
            )
            st.caption("병명·주민번호 같은 개인정보는 적지 마세요. 질문은 답변을 만들기 위해 OpenAI로 전송됩니다.")
        cols = st.columns(len(EXAMPLES))
        for col, example in zip(cols, EXAMPLES):
            if col.button(example, width="stretch"):
                st.session_state.pending = example
                st.rerun()
    for m in messages:
        render_message(m)
    actions = mcp_client.available_actions()
    if messages and actions and not st.session_state.get("outbound"):
        cols = st.columns(len(actions))
        for col, action in zip(cols, actions):
            label = "이 답이 틀렸어요" if action == "report_issue" else "설계사와 상담하고 싶어요"
            if col.button(label, key=f"start_{action}"):
                start_outbound(action, messages, source, memory)
                st.rerun()
    render_outbound()

    remaining = max(DAILY_LIMIT - question_store.used_today(), 0)
    text = st.chat_input(f"질문을 입력하세요 (오늘 남은 질문 {remaining}개)") or st.session_state.pop("pending", None)
    if not text or not text.strip():
        st.caption("이 답변은 약관 내용을 안내하는 참고용이며, 실제 보험금 지급 여부는 보험사 심사로 확정됩니다.")
        return
    text = text.strip()
    if not question_store.use_daily_quota(DAILY_LIMIT):
        st.warning(f"오늘 질문 한도({DAILY_LIMIT}개)를 모두 사용했습니다. 내일 다시 이용해 주세요.")
        return

    render_message({"role": "user", "content": text})
    with st.chat_message("assistant", avatar=LOGO_CHARACTER):
        with st.spinner("약관을 찾아보는 중..."):
            try:
                # 이번 질문에서 말한 사실(가입 시기 등)을 먼저 메모에 넣어, 같은 턴의 계산에도 쓰게 한다
                memory = session_memory.update(memory, text)
                result = chat.reply(messages + [{"role": "user", "content": text}], source, memory)
            except Exception as e:
                st.error(f"답변을 만들지 못했습니다: {e}")
                return
    question_store.add_message(session_id, "user", text, source)
    question_store.add_message(
        session_id, "assistant", result["content"], source,
        result["evidence"], result["calculation"], result["summary"],
    )
    if result["calculation"]:
        memory = session_memory.merge_calculation(memory, result["calculation"].get("inputs"))
    question_store.save_memory(session_id, memory)
    if result.get("pending_action"):
        done = messages + [{"role": "user", "content": text},
                           {"role": "assistant", "content": result["content"], "evidence": result["evidence"]}]
        start_outbound(result["pending_action"]["action"], done, source, memory, result["pending_action"]["reason"])
    st.rerun()


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


if SHOW_INGEST:
    with tab_ingest:
        render_ingest()
