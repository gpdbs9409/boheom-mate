"""도구 레지스트리와 디스패처(design.md 8절).
- REGISTRY: 도구마다 스키마, 프롬프트 규칙, 사용 가능 조건, 실행 함수, Disclaimer 여부를 둔다.
- bound_tools(ctx): 이번 턴에 이 상품에서 쓸 수 있는 도구만(REGISTRY 순서).
- dispatch(name, args, bound, ctx): 도구 하나를 실행하고 LLM에 돌려줄 Tool_Result(dict)를 만든다.
도구는 source를 인자로 받지 않고 Ctx.source를 쓴다. 근거는 ctx.add_evidence로 turn["evidence"]에 넣는다.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from boheom_mate.core import calculator
from boheom_mate.core import consultation
from boheom_mate.core import memory as session_memory
from boheom_mate.core import policy_rules
from boheom_mate.core import rag
from boheom_mate.integrations import mcp_client
from boheom_mate.store import outbox

DISCLAIMER = calculator.DISCLAIMER

# 디스패처가 잡아 {"error": 메시지}로 돌려주는 예외. 외부 호출 도구를 더할 때 그 오류(HiraError)를 더한다
CAUGHT_ERRORS = (ValueError, TypeError, sqlite3.Error, outbox.OutboxError)


def _new_turn():
    return {"evidence": [], "calculation": None, "summary": None, "searches": [],
            "tools": [], "memory_updates": {}, "consultation": None}


def _evidence_key(e):
    return (e.get("section"), e.get("article_no"), e.get("title"), e.get("text"))


def evidence_label(e):
    """근거 조항을 LLM에 보낼 짧은 라벨로. 예: "보통약관 제4조 보상하지 않는 사항"."""
    return f'{e["section"]} {e["article_no"]} {e["title"]}'


@dataclass
class Ctx:
    source: str
    rule_set: dict                        # policy_rules.load_rule_set(source)
    inventory: dict                       # articles.inventory(source)
    memory: dict | None = None
    turn: dict = field(default_factory=_new_turn)
    user_messages: list = field(default_factory=list)  # 이번 대화의 사용자 메시지 원문 전부
    session_id: str = "no-session"

    def add_evidence(self, items):
        """(section, article_no, title, text)가 같은 항목은 한 번만 turn["evidence"]에 넣는다."""
        seen = {_evidence_key(e) for e in self.turn["evidence"]}
        for e in items:
            key = _evidence_key(e)
            if key not in seen:
                seen.add(key)
                self.turn["evidence"].append(e)


@dataclass
class Tool:
    name: str
    schema: dict                          # OpenAI function 스키마
    rules: str                            # 시스템 프롬프트에 넣을 이 도구의 사용 규칙
    available: Callable[[Ctx], bool]
    run: Callable[[dict, Ctx], dict]      # Tool_Result(dict). 근거는 ctx.add_evidence로
    disclaimer: bool                      # True면 디스패처가 Tool_Result에 DISCLAIMER를 붙인다
    schema_for: Callable[[Ctx], dict] | None = None  # 상품에 따라 스키마가 다르면 이 함수로 고른다

    def schema_in(self, ctx):
        return self.schema_for(ctx) if self.schema_for else self.schema


# ---- search_terms -------------------------------------------------------------------------------

SEARCH_RULES = """[약관 내용]
- 보장 여부, 조건, 한도, 빼는 돈(공제) 같은 약관 내용은 반드시 search_terms 도구 결과로만 말하세요.
  이번 대화에서 이미 찾은 결과는 다시 써도 됩니다. 직접 지어내지 마세요.
- search_terms에는 앞 대화를 반영해 혼자 읽어도 뜻이 통하는 완전한 질문을 넣으세요.
  예: 앞에서 감기 통원 이야기를 했고 "대학병원이면요?"라고 물으면 → "감기로 대학병원에 통원했을 때 보상 내용과 공제금액"
- 도구 결과가 found=false면 "약관에서 확인할 수 없다"고 말하세요.
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


def run_search(args, ctx):
    result = rag.answer(args["question"], ctx.source)
    turn = ctx.turn
    turn["searches"].append(args["question"])
    turn["summary"] = turn["summary"] or result["summary"]
    ctx.add_evidence(result["evidence"])
    return {
        "found": result["found"],
        "answer": result["answer"],
        "evidence": [evidence_label(e) for e in result["evidence"]],
    }


# ---- calculate_payout ---------------------------------------------------------------------------

CALCULATE_RULES = """[금액 계산]
- "얼마 받아요?" 같은 금액 질문은 calculate_payout 도구로만 계산하세요. 직접 계산하지 마세요.
- 계산에 꼭 필요한 정보: 통원/입원, 병원 종류(동네 의원·병원 / 종합병원·대학병원), 낸 금액.
  빠진 것이 있으면 도구를 부르지 말고 한 번에 짧게 물어보세요.
- 가입 세대를 모르면 한 번 물어보세요(가입 시기: 2009.10 이전=1세대, ~2017.3=2세대, ~2021.6=3세대, 2021.7 이후=4세대).
  모르거나 안 알려 주면 generation은 비워 두고 최신 기준으로 계산하되, 세대에 따라 실제가 다를 수 있다고 안내하세요.
  1~3세대라고 하면 generation에 그 값을 넣으세요. 지금은 최신 기준 '참고 상한'만 제공되며 실제와 차이가 큽니다.
- 통원 금액이 여러 번 방문의 합계면 방문 횟수(visits)를 물어 함께 넣으세요. 공제는 방문 1건당 뗍니다.
- 실손을 2개 이상 가입했다고 하면 has_other_insurance=true로 넣으세요(비례보상 안내).
- 사용자가 가입 시기(연도·월)를 말했으면 joined_year, joined_month에 넣고 generation은 직접 정하지 마세요.
  세대는 코드가 가입 시기로 정합니다. [이 대화에서 기억하는 정보]에 가입 세대가 있으면 그 값을 generation에 넣으세요.
- 낸 금액은 영수증의 '급여 본인부담금'과 '비급여' 칸에 있다고 알려 주세요.
  사용자가 "도수치료 10만 원"처럼 대략 말하면 그 금액으로 계산하세요(도수치료·주사·MRI는 해당 칸에).
- 암, 뇌혈관·심장 질환, 희귀·난치 질환 같은 중증 질환 치료라고 말하지 않았으면 severe=false로 계산하세요.
- 사용자가 조건만 바꿔 다시 물으면(예: "대학병원이었으면요?") 앞의 값을 그대로 쓰고 바뀐 것만 고쳐 다시 계산하세요.
- 계산 결과의 항목별 표는 화면에 따로 보여 주므로, 답변에는 총액과 핵심 이유만 쓰세요.
"""

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
                               "description": "가입 세대. 1세대: 2009년 9월까지, 2세대: 2009년 10월~2017년 3월, 3세대: 2017년 4월~2021년 6월, 4세대: 2021년 7월부터"},
                "has_other_insurance": {"type": "boolean", "description": "실손보험을 2개 이상 가입했는지"},
                "joined_year": {"type": "integer", "description": "사용자가 말한 실손 가입 연도. 말했으면 generation 대신 이 값을 넣는다"},
                "joined_month": {"type": "integer", "description": "사용자가 말한 실손 가입 월(1~12). 모르면 넣지 않는다"},
            },
            "required": ["care", "hospital"],
        },
    },
}

CALCULATE_SIMPLE_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_payout",
        "description": "영수증 금액으로 예상 실손 보험금을 계산한다(간편가입 약관 제3조 공식).",
        "parameters": {
            "type": "object",
            "properties": {
                "care": {"type": "string", "enum": ["통원", "입원"]},
                "covered_copay": {"type": "integer", "description": "급여 본인부담금(원). 없으면 0"},
                "non_covered": {"type": "integer",
                                "description": "비급여(원) 중 도수치료·주사·MRI·병실료를 뺀 나머지. 약값(처방조제비)은 넣지 않는다"},
                "manual_therapy": {"type": "integer", "description": "비급여 도수치료·체외충격파·증식치료(원)"},
                "injection": {"type": "integer",
                              "description": "비급여 주사(원). 항암제·항생제·희귀의약품 주사는 non_covered에 넣는다"},
                "mri": {"type": "integer", "description": "비급여 MRI/MRA(원)"},
                "room_fee": {"type": "integer"},
                "room_days": {"type": "integer"},
                "visits": {"type": "integer", "description": "통원 방문 횟수. 모르면 1"},
                "prior_deductible": {"type": "integer",
                                     "description": "올해(계약해당일부터 1년) 이미 낸 입원 공제액. 사용자가 말했을 때만"},
                "nhis_applied": {"type": "boolean",
                                 "description": "국민건강보험을 적용받았는지. 적용받지 못했다고 말했을 때만 false"},
                "hospital": {"type": "string", "enum": ["의원", "종합병원"],
                             "description": "이 상품은 공제에 쓰지 않는다. 말했으면 넣는다"},
                "generation": {"type": "string", "enum": ["1세대", "2세대", "3세대", "4세대"]},
                "has_other_insurance": {"type": "boolean"},
                "joined_year": {"type": "integer"},
                "joined_month": {"type": "integer"},
            },
            "required": ["care"],
        },
    },
}


def calculate_schema(profile):
    """프로필에 맞는 calculate_payout 스키마. 간편가입이 아니면(모름 포함) 기본형."""
    return CALCULATE_SIMPLE_TOOL if profile == policy_rules.PROFILE_SIMPLE else CALCULATE_TOOL


def run_calculate(args, ctx):
    """계산 값은 이 상품 약관에서 뽑아 검증한 것(ctx.rule_set, ok)을 쓰고, 계산에 쓴 약관 구절을 근거로 붙인다."""
    rules = ctx.rule_set if (ctx.rule_set or {}).get("ok") else None
    if rules is None:
        raise ValueError("이 상품은 계산에 필요한 약관 값을 확인하지 못해 계산할 수 없습니다.")
    inputs = {k: v for k, v in args.items() if v is not None}
    # 가입 시기를 말했으면 세대는 GPT가 아니라 코드가 정한다(경계 연도에 월을 모르면 세대를 비워 둔다)
    year, month = inputs.pop("joined_year", None), inputs.pop("joined_month", None)
    if year is not None:
        generation = session_memory.generation_of(year, month)
        if generation:
            inputs["generation"] = generation
        else:
            inputs.pop("generation", None)
    if rules.get("profile") == policy_rules.PROFILE_SIMPLE:
        result = calculator.calculate_simple(**inputs, rules=rules["values"], articles=rules["articles"],
                                             exclusion_quotes=rules.get("exclusion_items") or {})
    else:
        result = calculator.calculate(**inputs, rules=rules["values"], articles=rules["articles"])
    ctx.turn["calculation"] = {**result, "inputs": inputs}
    ctx.add_evidence(policy_rules.evidence(rules, result["rules_used"]))
    return {k: v for k, v in result.items() if k != "rules_used"}


# ---- request_consultation -----------------------------------------------------------------------
# 회신 이메일이 새지 않도록 이 어댑터는 print·logging을 쓰지 않는다(Req 11.6, 14.6).
CONSULT_NOTIFY = True            # 평가(run_chat)는 False로 끈다
NOTICE_TIMEOUT_SECONDS = 5
CONSULT_RULES = """[상담 신청]
- 사용자가 사람 상담사와 상담하고 싶다고 직접 말한 경우에만 request_consultation을 부르세요.
  상담 신청을 위해 되물은 값(이메일 등)을 사용자가 다음 턴에 답한 경우도 같은 요청으로 봅니다.
  약관 질문, 금액 질문, 불만 표현만으로는 부르지 마세요. 먼저 상담 신청을 권하며 부르지 마세요.
- 문의 요약이나 회신 이메일이 없으면 부르지 말고 한 번에 짧게 물어보세요.
  이메일은 사용자가 대화에 직접 쓴 주소만 그대로 넣고, 지어내거나 고치지 마세요.
- 약관에서 확인할 수 없어 답하지 못했으면 답변 끝에 "원하시면 상담사 상담을 신청할 수 있어요" 한 줄만 붙일 수 있습니다.
  그 턴에는 request_consultation을 부르지 마세요.
- 결과에 need가 있으면 그 값을 다시 물어보세요. error가 limit_reached면 message를 그대로 전하세요.
  accepted=false이고 다른 error면 "접수하지 못했어요"라고 안내하세요.
- 접수되면 접수번호와 "실제 메일은 보내지 않는 데모"임을 짧게 알리세요. 접수 내용은 화면에 따로 보입니다.
"""
CONSULT_TOOL = {
    "type": "function",
    "function": {
        "name": "request_consultation",
        "description": ("사용자가 사람 상담사와 상담하고 싶다고 직접 요청했을 때만 상담 신청을 접수한다. "
                        "실제 메일은 보내지 않는 데모다. 약관·금액 질문에는 쓰지 않는다."),
        "parameters": {
            "type": "object",
            "required": ["summary", "reply_email"],
            "properties": {
                "summary": {"type": "string",
                            "description": "대화에서 정리한 문의 요약(상담사에게 묻고 싶은 내용). 주민번호·전화번호는 넣지 않는다"},
                "reply_email": {"type": "string",
                                "description": "사용자가 대화에 직접 쓴 회신 이메일 주소를 그대로. 지어내거나 고치지 않는다"},
                "preferred_time": {"type": "string",
                                   "description": "사용자가 말한 상담 선호 시간대. 말하지 않았으면 넣지 않는다"},
            },
        },
    },
}
CONSULT_ARGS = ("summary", "reply_email", "preferred_time")
def _notify(request):
    """Outbox_File을 쓴 뒤 고정 형식 알림을 보낸다. 설정 없음·MCPError(시간 초과 포함)는 False."""
    if not CONSULT_NOTIFY or "consult_notice" not in mcp_client.available_actions():
        return False
    text = consultation.notice_text(request["receipt_number"], request["product_name"], request["summary"])
    try:
        mcp_client.execute("consult_notice", {"text": text}, timeout=NOTICE_TIMEOUT_SECONDS)
        return True
    except mcp_client.MCPError:
        return False  # 오류 문구는 Tool_Result에 넣지 않는다(토큰 비노출)
def run_consultation(args, ctx):
    """design.md 9.10 실행 순서: 한도 → 요약 → 이메일 있음 → 사용자 원문에 있음 → 형식 → Outbox 쓰기 → 알림."""
    args = {k: args.get(k) for k in CONSULT_ARGS}  # 스키마 밖 인자(to, source 등)는 무시한다
    outbox.purge_expired()
    if outbox.count_for_session(ctx.session_id) >= consultation.CONSULTATION_LIMIT:
        return {"accepted": False, "error": "limit_reached", "message": consultation.LIMIT_MESSAGE}
    summary = consultation.clean_text(args["summary"], consultation.MAX_SUMMARY_CHARS)
    if not summary:
        return {"accepted": False, "need": "summary"}
    raw_email = args["reply_email"]
    reply_email = raw_email.strip() if isinstance(raw_email, str) else ""
    if not reply_email:
        return {"accepted": False, "need": "reply_email"}
    if not consultation.email_in_messages(reply_email, ctx.user_messages):
        return {"accepted": False, "need": "reply_email", "reason": "사용자가 직접 입력한 이메일만 쓸 수 있음"}
    if not consultation.is_valid_email(reply_email):
        return {"accepted": False, "need": "valid_reply_email"}
    preferred_time = consultation.clean_text(args["preferred_time"], consultation.MAX_PREFERRED_TIME_CHARS) or None
    now = datetime.now(consultation.KST)
    try:
        request = outbox.write(
            lambda receipt: consultation.build_request(
                summary=summary, reply_email=reply_email, preferred_time=preferred_time,
                source=ctx.source, session_id=ctx.session_id, now=now, receipt=receipt),
            make_receipt=lambda: consultation.new_receipt(now), now=now)
    except outbox.OutboxError as e:
        return {"accepted": False, "error": str(e)}
    record = {
        "receipt_number": request["receipt_number"],
        "summary": request["summary"],
        "reply_email": request["reply_email"],
        "preferred_time": request["preferred_time"],
        "mock_notice": consultation.MOCK_NOTICE,
        "retention_hours": outbox.RETENTION_HOURS,
        "notified": _notify(request),
    }
    ctx.turn["consultation"] = record  # 한 턴에 두 번 접수되면 마지막 것
    return {"accepted": True, **record}
# ---- 레지스트리와 디스패처 --------------------------------------------------------------------------

REGISTRY = [
    Tool(name="search_terms", schema=SEARCH_TOOL, rules=SEARCH_RULES,
         available=lambda ctx: True, run=run_search, disclaimer=False),
    Tool(name="calculate_payout", schema=CALCULATE_TOOL, rules=CALCULATE_RULES,
         available=lambda ctx: bool((ctx.rule_set or {}).get("ok")), run=run_calculate, disclaimer=True,
         schema_for=lambda ctx: calculate_schema((ctx.rule_set or {}).get("profile"))),
    Tool(name="request_consultation", schema=CONSULT_TOOL, rules=CONSULT_RULES,
         available=lambda ctx: True, run=run_consultation, disclaimer=False),
]


def _safe_rule_set(source):
    try:
        return policy_rules.load_rule_set(source)
    except Exception:
        return {"ok": False, "profile": None}


def _safe_inventory(source):
    from boheom_mate.core import articles
    try:
        return articles.inventory(source)
    except Exception:
        return {}


def make_ctx(source, memory=None, history=(), session_id=None):
    """한 턴의 Ctx. Rule_Set·조항 목록을 읽지 못하면 계산기 없음·조항 없음으로 본다."""
    return Ctx(source=source, rule_set=_safe_rule_set(source), inventory=_safe_inventory(source),
               memory=memory, user_messages=[m["content"] for m in history if m.get("role") == "user"],
               session_id=session_id or "no-session")


def bound_tools(ctx):
    """REGISTRY 순서대로, 이 상품에서 사용 가능 조건을 만족한 도구만."""
    return [t for t in REGISTRY if t.available(ctx)]


def schemas(bound, ctx):
    """bind_tools에 넘길 스키마 목록(bound 순서)."""
    return [t.schema_in(ctx) for t in bound]


def _for_llm(result):
    """근거 원문은 LLM에 다시 보내지 않는다. evidence에 조항 dict가 있으면 라벨로 바꾼다."""
    evidence = result.get("evidence")
    if isinstance(evidence, list) and any(isinstance(e, dict) for e in evidence):
        result = {**result, "evidence": [evidence_label(e) if isinstance(e, dict) else e for e in evidence]}
    return result


def dispatch(name, args, bound, ctx):
    """도구 하나를 실행해 Tool_Result를 돌려준다.
    1) bound에 없으면 실행하지 않고 {"error": "없는 도구: <이름>"}
    2) 실행한 도구 이름을 turn["tools"]에 호출 순서대로 더한다
    3) CAUGHT_ERRORS는 {"error": 메시지}로 바꿔 대화를 계속한다
    4) disclaimer 도구는 오류 결과에도 DISCLAIMER를 붙인다
    5) 근거 원문 대신 라벨만 LLM에 돌려준다"""
    tool = next((t for t in bound if t.name == name), None)
    if tool is None:
        return {"error": f"없는 도구: {name}"}
    ctx.turn["tools"].append(name)
    try:
        result = tool.run(dict(args or {}), ctx)
    except CAUGHT_ERRORS as e:  # 잘못된 값이면 GPT가 고쳐서 다시 부르거나 사용자에게 묻게 한다
        result = {"error": str(e)}
    result = _for_llm(result)
    if tool.disclaimer:
        result = {**result, "disclaimer": DISCLAIMER}
    return result
