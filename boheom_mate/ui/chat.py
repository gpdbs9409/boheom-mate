"""이어지는 대화: GPT가 매 턴 할 일을 고른다(도구 호출).

- search_terms: 약관을 찾아 답한다. 기존 rag.answer(검색 → 근거 기반 답변)를 그대로 쓴다.
  GPT가 앞 대화를 반영해 "혼자 읽어도 뜻이 통하는 질문"으로 바꿔서 넘기므로
  "그럼 대학병원은요?" 같은 이어지는 질문도 제대로 검색된다.
- calculate_payout: 예상 보험금을 계산한다. 금액은 GPT가 아니라 calculator.py가 계산한다.
- 정보가 모자라면 도구를 부르지 않고 사용자에게 되묻는다.

LangChain ChatOpenAI.bind_tools로 도구 호출(tool calling) 루프를 돈다.
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from boheom_mate.core import memory as session_memory
from boheom_mate.core import policy_rules
from boheom_mate.core import rag
from boheom_mate.ui import tools
from boheom_mate.ui.tools import CALCULATE_SIMPLE_TOOL, CALCULATE_TOOL, SEARCH_TOOL  # noqa: F401 (기존 이름 유지)

CALCULATOR_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"  # 기본 상품(첫 화면, 평가에서 쓰는 약관)
MAX_TOOL_ROUNDS = 4
HISTORY_TURNS = 12  # GPT에 보내는 최근 메시지 수

# 답변 말투. False면 쉬운 말 규칙을 빼고 답한다(평가에서 전후 비교용)
PLAIN_LANGUAGE = True

HEADER = "당신은 '보험메이트', 실손의료보험 약관 상담 도우미입니다. 사용자와 이어지는 대화를 합니다.\n\n"
SCOPE = """[범위]
- 실손보험과 관계없는 질문에는 이 앱이 답할 수 있는 범위를 짧게 안내하세요.
"""
# 도구 규칙 문단은 ui/tools.py의 Tool.rules에 있고, _system_prompt가 bound된 도구의 것만 넣는다
# 계산기가 꺼진 상품(calculate_payout이 bound되지 않음)에는 [금액 계산] 규칙 대신 이 안내를 넣는다
CALCULATOR_OFF_NOTE = ("[금액 계산]\n"
                       "- 이 상품은 약관에서 계산에 필요한 값을 확인하지 못해 계산기를 쓸 수 없습니다. "
                       "금액 질문에는 약관 내용만 안내하세요.\n")
PLAIN_RULES = """
[말투]
- 보험을 전혀 모르는 사람도 바로 알아듣게 쉬운 말로, 짧게 답하세요.
- 첫 문장에 결론을 쓰세요. 예: "네, 보상받을 수 있어요." / "예상 보험금은 **5,000원**이에요."
- 약관 용어는 쉬운 말로 바꾸고, 꼭 필요하면 괄호에 넣으세요.
  급여 → 건강보험 되는 진료비, 비급여 → 건강보험 안 되는 진료비, 본인부담금 → 내가 낸 돈,
  공제금액 → 빼는 돈, 통원 → 외래 진료, 상급병실료 차액 → 1·2인실 추가 비용, 산정특례 → 중증 질환
- 금액은 굵게 쓰세요. 조항 원문은 화면에 따로 보이므로 길게 인용하지 마세요.
- 도움이 되면 마지막에 다음에 할 수 있는 것을 한 줄로 제안하세요. 예: "영수증 금액을 알려 주시면 받을 금액을 계산해 드릴게요."
"""

# 상품 구조 안내([약관 내용] 끝). 프로필을 모르면 넣지 않는다
PRODUCT_NOTES = {
    policy_rules.PROFILE_BASE: (
        "- 이 상품은 건강보험 되는 진료비(급여)는 보통약관에서, 건강보험 안 되는 진료비(비급여)는\n"
        "  특별약관1(중증)·2(비중증)에서 보상합니다. 검색 결과가 한쪽 조항만 보여 줘도 \"한쪽만 된다\"고 단정하지 마세요.\n"),
    policy_rules.PROFILE_SIMPLE: (
        "- 이 상품은 급여·비급여를 보통약관 한 담보에서 보상하고, "
        "비급여 도수치료·주사·MRI는 보상하지 않습니다(제4조).\n"),
}


def _load_rules(source):
    """계산에 쓸 수 있는(ok) Rule_Set(v2). 없거나 읽지 못하면 None."""
    try:
        return policy_rules.load(source)
    except Exception:
        return None


def _profile(source, rules=None):
    """상품 프로필(기본형/간편가입). 계산기가 꺼진 상품도 Rule_Set에 프로필이 있으면 돌려준다."""
    if rules is not None:
        return rules.get("profile")
    try:
        return policy_rules.load_rule_set(source).get("profile")
    except Exception:
        return None


def product_note(profile):
    """프로필에 맞는 상품 구조 한 줄. 프로필이 None(또는 모름)이면 빈 문자열."""
    return PRODUCT_NOTES.get(profile, "")


def calculator_available(source):
    """이 상품 약관에서 계산에 필요한 값을 모두 뽑고 원문으로 검증했으면 True."""
    return _load_rules(source) is not None


def _calculate_tool(profile):
    return tools.calculate_schema(profile)


def bound_tool_names(source):
    """이 상품에서 이번 턴에 쓸 수 있는 도구 이름(REGISTRY 순서)."""
    return [t.name for t in tools.bound_tools(tools.make_ctx(source))]


def _system_prompt(ctx, bound):
    """HEADER + bound 도구 규칙 + product_note + SCOPE + PLAIN_RULES + 메모.
    product_note는 [약관 내용](search_terms 규칙) 끝에 붙는다. 도구 규칙 문단 사이는 빈 줄 하나.
    기본형에서 search_terms·calculate_payout 두 개가 bound면 이전 BASE_PROMPT와 같은 글이 된다."""
    note = product_note((ctx.rule_set or {}).get("profile"))
    names = [t.name for t in bound]
    parts = [HEADER]
    for t in bound:
        parts.append(t.rules + (note if t.name == "search_terms" else "") + "\n")
    if "search_terms" not in names and note:
        parts.append(note + "\n")
    if "calculate_payout" not in names:
        parts.append(CALCULATOR_OFF_NOTE + "\n")
    parts.append(SCOPE)
    if PLAIN_LANGUAGE:
        parts.append(PLAIN_RULES)
    parts.append(session_memory.prompt_block(ctx.memory))
    return "".join(parts)


def _model_history(history):
    """화면에 쌓인 대화를 GPT에 보낼 형태로. 계산에 쓴 값은 다음 턴에서 이어 쓰도록 붙여 둔다."""
    messages = []
    for m in history[-HISTORY_TURNS:]:
        content = m["content"]
        calc = m.get("calculation")
        if m["role"] == "assistant" and calc:
            content += f"\n[계산에 쓴 값: {json.dumps(calc['inputs'], ensure_ascii=False)} → 총 {calc['total']:,}원]"
        messages.append(AIMessage(content=content) if m["role"] == "assistant" else HumanMessage(content=content))
    return messages


def reply(history, source, memory=None, *, session_id=None):
    """history: [{"role": "user"|"assistant", "content": str, "calculation": dict|None}, ...] (마지막이 사용자 질문)
    memory: 이 대화의 상담 메모(core.memory). 프롬프트에 넣어 이미 말한 정보를 다시 묻지 않게 한다
    반환값: {"content": 답변, "evidence": [조항], "calculation": 계산 결과|None,
             "summary": 질문 요지|None, "searches": [GPT가 검색한 질문],
             "tools": [이번 턴에 실행한 도구 이름(호출 순서)], "memory_updates": {도구가 남긴 메모 갱신},
             "consultation": 이번 턴에 접수한 상담 신청|None}
    session_id: 상담 접수 건수를 세는 세션 id. None이면 "no-session"
    도구 실행은 ui/tools.py의 레지스트리·디스패처가 맡는다.
    """
    ctx = tools.make_ctx(source, memory, history, session_id=session_id)
    bound = tools.bound_tools(ctx)
    llm = rag._llm.bind_tools(tools.schemas(bound, ctx))
    messages = [SystemMessage(content=_system_prompt(ctx, bound))] + _model_history(history)

    def _result(content):
        keys = ("evidence", "calculation", "summary", "searches", "tools", "memory_updates", "consultation")
        return {"content": content, **{k: ctx.turn[k] for k in keys}}

    for _ in range(MAX_TOOL_ROUNDS):
        message = llm.invoke(messages)
        if not message.tool_calls:
            content = message.content if isinstance(message.content, str) else message.text
            return _result(content or "")
        messages.append(message)
        for call in message.tool_calls:
            output = tools.dispatch(call["name"], call["args"], bound, ctx)
            messages.append(ToolMessage(content=json.dumps(output, ensure_ascii=False), tool_call_id=call["id"]))
    return _result("죄송해요, 답을 정리하지 못했어요. 질문을 조금 바꿔서 다시 물어봐 주세요.")
