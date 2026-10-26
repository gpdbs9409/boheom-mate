"""이어지는 대화: GPT가 매 턴 할 일을 고른다(도구 호출).

- search_terms: 약관을 찾아 답한다. 기존 rag.answer(검색 → 근거 기반 답변)를 그대로 쓴다.
  GPT가 앞 대화를 반영해 "혼자 읽어도 뜻이 통하는 질문"으로 바꿔서 넘기므로
  "그럼 대학병원은요?" 같은 이어지는 질문도 제대로 검색된다.
- calculate_payout: 예상 보험금을 계산한다. 금액은 GPT가 아니라 calculator.py가 계산한다.
- 정보가 모자라면 도구를 부르지 않고 사용자에게 되묻는다.

LangChain 없이 OpenAI 도구 호출(tool calling)로 직접 구현했다.
"""
import json

from boheom_mate.core import calculator
from boheom_mate.core import rag

CALCULATOR_SOURCE = "KB_실손의료비보장보험_26.09_약관.pdf"  # 계산기 공식은 기본 약관에서 옮겼다
MAX_TOOL_ROUNDS = 4
HISTORY_TURNS = 12  # GPT에 보내는 최근 메시지 수

# 답변 말투. False면 쉬운 말 규칙을 빼고 답한다(평가에서 전후 비교용)
PLAIN_LANGUAGE = True

BASE_PROMPT = """당신은 '보험메이트', 실손의료보험 약관 상담 도우미입니다. 사용자와 이어지는 대화를 합니다.

[약관 내용]
- 보장 여부, 조건, 한도, 빼는 돈(공제) 같은 약관 내용은 반드시 search_terms 도구 결과로만 말하세요.
  이번 대화에서 이미 찾은 결과는 다시 써도 됩니다. 직접 지어내지 마세요.
- search_terms에는 앞 대화를 반영해 혼자 읽어도 뜻이 통하는 완전한 질문을 넣으세요.
  예: 앞에서 감기 통원 이야기를 했고 "대학병원이면요?"라고 물으면 → "감기로 대학병원에 통원했을 때 보상 내용과 공제금액"
- 도구 결과가 found=false면 "약관에서 확인할 수 없다"고 말하세요.
- 이 상품은 건강보험 되는 진료비(급여)는 보통약관에서, 건강보험 안 되는 진료비(비급여)는
  특별약관1(중증)·2(비중증)에서 보상합니다. 검색 결과가 한쪽 조항만 보여 줘도 "한쪽만 된다"고 단정하지 마세요.

[금액 계산]
- "얼마 받아요?" 같은 금액 질문은 calculate_payout 도구로만 계산하세요. 직접 계산하지 마세요.
- 계산에 꼭 필요한 정보: 통원/입원, 병원 종류(동네 의원·병원 / 종합병원·대학병원), 낸 금액.
  빠진 것이 있으면 도구를 부르지 말고 한 번에 짧게 물어보세요.
- 가입 세대를 모르면 한 번 물어보세요(가입 시기: 2009.10 이전=1세대, ~2017.3=2세대, ~2021.6=3세대, 2021.7 이후=4세대).
  모르거나 안 알려 주면 generation은 비워 두고 최신 기준으로 계산하되, 세대에 따라 실제가 다를 수 있다고 안내하세요.
  1~3세대라고 하면 generation에 그 값을 넣으세요. 지금은 최신 기준 '참고 상한'만 제공되며 실제와 차이가 큽니다.
- 통원 금액이 여러 번 방문의 합계면 방문 횟수(visits)를 물어 함께 넣으세요. 공제는 방문 1건당 뗍니다.
- 실손을 2개 이상 가입했다고 하면 has_other_insurance=true로 넣으세요(비례보상 안내).
- 낸 금액은 영수증의 '급여 본인부담금'과 '비급여' 칸에 있다고 알려 주세요.
  사용자가 "도수치료 10만 원"처럼 대략 말하면 그 금액으로 계산하세요(도수치료·주사·MRI는 해당 칸에).
- 암, 뇌혈관·심장 질환, 희귀·난치 질환 같은 중증 질환 치료라고 말하지 않았으면 severe=false로 계산하세요.
- 사용자가 조건만 바꿔 다시 물으면(예: "대학병원이었으면요?") 앞의 값을 그대로 쓰고 바뀐 것만 고쳐 다시 계산하세요.
- 계산 결과의 항목별 표는 화면에 따로 보여 주므로, 답변에는 총액과 핵심 이유만 쓰세요.
{calculator_note}
[범위]
- 실손보험과 관계없는 질문에는 이 앱이 답할 수 있는 범위를 짧게 안내하세요.
"""

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

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_terms",
        "description": "가입한 실손보험 약관에서 관련 조항을 찾아 근거와 함께 답을 받는다.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "앞 대화를 반영한, 혼자 읽어도 뜻이 통하는 완전한 질문"},
            },
            "required": ["question"],
        },
    },
}

CALCULATE_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_payout",
        "description": "영수증 금액으로 예상 실손 보험금을 계산한다(기본 약관 제3조 공식).",
        "parameters": {
            "type": "object",
            "properties": {
                "care": {"type": "string", "enum": ["통원", "입원"]},
                "hospital": {"type": "string", "enum": ["의원", "종합병원"],
                             "description": "의원: 동네 의원·병원·치과·한의원·요양병원·약국 / 종합병원: 종합병원·대학병원(상급종합병원)"},
                "covered_copay": {"type": "integer", "description": "급여 본인부담금(원). 없으면 0"},
                "nhis_paid": {"type": "integer", "description": "공단부담금(원). 사용자가 말했을 때만 넣는다"},
                "non_covered": {"type": "integer", "description": "비급여(원) 중 도수치료·주사·MRI·병실료를 뺀 나머지. 없으면 0"},
                "manual_therapy": {"type": "integer", "description": "비급여 도수치료·체외충격파(원)"},
                "injection": {"type": "integer", "description": "비급여 주사(원)"},
                "mri": {"type": "integer", "description": "비급여 MRI(원)"},
                "severe": {"type": "boolean", "description": "암 등 중증(산정특례 대상) 질환 치료인지"},
                "room_fee": {"type": "integer", "description": "입원 시 1·2인실 추가 비용(원)"},
                "room_days": {"type": "integer", "description": "입원 일수(1·2인실 비용이 있을 때)"},
                "visits": {"type": "integer", "description": "통원 방문 횟수. 금액이 여러 번 방문의 합계면 그 횟수. 모르면 1"},
                "generation": {"type": "string", "enum": ["1세대", "2세대", "3세대", "4세대"],
                               "description": "가입 세대. 1세대: 2009.10 이전, 2세대: ~2017.3, 3세대: ~2021.6, 4세대: 2021.7 이후"},
                "has_other_insurance": {"type": "boolean", "description": "실손보험을 2개 이상 가입했는지"},
            },
            "required": ["care", "hospital"],
        },
    },
}


def _system_prompt(source):
    if source == CALCULATOR_SOURCE:
        note = ""
    else:
        note = "- 이 상품은 계산기를 지원하지 않습니다. 금액 질문에는 약관 내용만 안내하고, 계산은 기본형 상품에서만 된다고 알려 주세요.\n"
    return BASE_PROMPT.format(calculator_note=note) + (PLAIN_RULES if PLAIN_LANGUAGE else "")


def _model_history(history):
    """화면에 쌓인 대화를 GPT에 보낼 형태로. 계산에 쓴 값은 다음 턴에서 이어 쓰도록 붙여 둔다."""
    messages = []
    for m in history[-HISTORY_TURNS:]:
        content = m["content"]
        calc = m.get("calculation")
        if m["role"] == "assistant" and calc:
            content += f"\n[계산에 쓴 값: {json.dumps(calc['inputs'], ensure_ascii=False)} → 총 {calc['total']:,}원]"
        messages.append({"role": m["role"], "content": content})
    return messages


def _run_search(args, source, turn):
    result = rag.answer(args["question"], source)
    turn["searches"].append(args["question"])
    turn["summary"] = turn["summary"] or result["summary"]
    for e in result["evidence"]:
        if e not in turn["evidence"]:
            turn["evidence"].append(e)
    return {
        "found": result["found"],
        "answer": result["answer"],
        "evidence": [f'{e["section"]} {e["article_no"]} {e["title"]}' for e in result["evidence"]],
    }


def _run_calculate(args, turn):
    inputs = {k: v for k, v in args.items() if v is not None}
    result = calculator.calculate(**inputs)
    turn["calculation"] = {**result, "inputs": inputs}
    return result


def reply(history, source):
    """history: [{"role": "user"|"assistant", "content": str, "calculation": dict|None}, ...] (마지막이 사용자 질문)

    반환값: {"content": 답변, "evidence": [조항], "calculation": 계산 결과|None,
             "summary": 질문 요지|None, "searches": [GPT가 검색한 질문]}
    """
    tools = [SEARCH_TOOL] + ([CALCULATE_TOOL] if source == CALCULATOR_SOURCE else [])
    messages = [{"role": "system", "content": _system_prompt(source)}] + _model_history(history)
    turn = {"evidence": [], "calculation": None, "summary": None, "searches": []}

    for _ in range(MAX_TOOL_ROUNDS):
        response = rag._client.chat.completions.create(
            model=rag.ANSWER_MODEL, temperature=rag.TEMPERATURE, messages=messages, tools=tools,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            return {"content": message.content or "", **turn}

        messages.append(message.model_dump(exclude_none=True))
        for call in message.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
                if call.function.name == "search_terms":
                    output = _run_search(args, source, turn)
                elif call.function.name == "calculate_payout":
                    output = _run_calculate(args, turn)
                else:
                    output = {"error": f"없는 도구: {call.function.name}"}
            except (ValueError, TypeError) as e:  # 잘못된 값이면 GPT가 고쳐서 다시 부르거나 사용자에게 묻게 한다
                output = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(output, ensure_ascii=False)})

    return {"content": "죄송해요, 답을 정리하지 못했어요. 질문을 조금 바꿔서 다시 물어봐 주세요.", **turn}
