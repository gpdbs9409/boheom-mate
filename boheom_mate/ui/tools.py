"""도구 레지스트리와 디스패처(design.md 8절).
- REGISTRY: 도구마다 스키마, 프롬프트 규칙, 사용 가능 조건, 실행 함수, Disclaimer 여부를 둔다.
- bound_tools(ctx): 이번 턴에 이 상품에서 쓸 수 있는 도구만(REGISTRY 순서).
- dispatch(name, args, bound, ctx): 도구 하나를 실행하고 LLM에 돌려줄 Tool_Result(dict)를 만든다.
도구는 source를 인자로 받지 않고 Ctx.source를 쓴다. 근거는 ctx.add_evidence로 turn["evidence"]에 넣는다.
"""
import re
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

# 디스패처가 잡아 {"error": 메시지}로 돌려주는 예외. 외부 호출 도구를 더할 때 그 오류(예: OutboxError)를 더한다
CAUGHT_ERRORS = (ValueError, TypeError, outbox.OutboxError)


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
        # 사용자가 이번에 말하지 않았으면 메모의 올해 누적 공제액·통원 횟수를 쓴다(작업 11.6)
        memo = session_memory.clean(ctx.memory)
        if "prior_deductible" not in inputs and memo["deductible_this_year"] is not None:
            inputs["prior_deductible"] = memo["deductible_this_year"]
        if memo["outpatient_visits_this_year"] is not None:
            inputs.setdefault("prior_visits", memo["outpatient_visits_this_year"])
        result = calculator.calculate_simple(**inputs, rules=rules["values"], articles=rules["articles"],
                                             exclusion_quotes=rules.get("exclusion_items") or {})
    else:
        result = calculator.calculate(**inputs, rules=rules["values"], articles=rules["articles"])
    ctx.turn["calculation"] = {**result, "inputs": inputs}
    ctx.add_evidence(policy_rules.evidence(rules, result["rules_used"]))
    return {k: v for k, v in result.items() if k != "rules_used"}


# ---- remaining_limit ----------------------------------------------------------------------------

REMAINING_RULES = """[남은 한도]
- 올해 이미 받은 보험금·낸 공제액·통원 횟수를 반영해 남은 한도를 물을 때만 remaining_limit을 부르세요.
  이번 진료의 예상 금액은 calculate_payout, 한도 조건 설명은 search_terms를 쓰세요.
- 결과에 need가 있으면 그 값(올해 받은 보험금, 올해 낸 입원 공제액)을 한 번 물어보세요.
- remaining이 null인 항목은 숫자를 지어내지 말고 notes를 전하세요. warning이 있으면 그대로 전하세요.
"""

REMAINING_TOOL = {
    "type": "function",
    "function": {
        "name": "remaining_limit",
        "description": "올해 이미 받은 보험금·낸 공제액·방문 횟수를 반영해 남은 한도를 물을 때.",
        "parameters": {"type": "object", "properties": {}},
    },
}
REMAINING_NEED = ["received_this_year", "deductible_this_year"]
POLICY_YEAR_WARNING = "올해는 달력 연도가 아니라 계약일부터 1년 단위이므로 기준일에 따라 실제와 다를 수 있음"


def run_remaining(args, ctx):
    """design.md 9.7: 인자 없이 메모의 올해 기수령액·공제액·통원 횟수로 calculator.remaining_limit을 부른다."""
    rules = ctx.rule_set if (ctx.rule_set or {}).get("ok") else None
    if rules is None:
        raise ValueError("이 상품은 약관 한도 값을 확인하지 못해 남은 한도를 계산할 수 없습니다.")
    memo = session_memory.clean(ctx.memory)
    received, deductible = memo["received_this_year"], memo["deductible_this_year"]
    warning = None if memo["anniversary_month"] else POLICY_YEAR_WARNING
    if received is None and deductible is None:
        return {"items": [], "notes": [], "need": list(REMAINING_NEED), "warning": warning}
    result = calculator.remaining_limit(rules["profile"], rules["values"], received=received,
                                        prior_deductible=deductible,
                                        prior_visits=memo["outpatient_visits_this_year"])
    ctx.add_evidence(policy_rules.evidence(rules, result["rules_used"]))
    return {"items": result["items"], "notes": result["notes"], "need": None, "warning": warning}


# ---- check_exclusions ---------------------------------------------------------------------------

EXCLUSIONS_RULES = """[보상 여부 확인]
- 약관에서 보상하지 않을 수 있는 치료·항목(예: 임신·출산, 치과, 미용·성형, 임플란트, 한방 등)이 보상되는지 콕 집어 물을 때만
  check_exclusions를 부르세요. 금액 질문에는 쓰지 마세요.
- 어떤 병으로 병원에 가거나 입원·수술했는데 보장되는지처럼 일반적인 보장 질문은 search_terms를 부르세요.
- 결과가 conclusive=false면 보상된다/안 된다를 단정하지 말고, 확인한 쪽 조항 내용만 안내하세요.
- 결과가 found=false면 "약관에서 확인할 수 없다"고 말하세요.
"""

EXCLUSIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "check_exclusions",
        "description": ("면책일 수 있는 특정 치료·항목(임신·출산, 치과, 미용, 임플란트 등)이 보상되는지 물을 때만. "
                        "보장 조항과 면책 조항을 함께 확인한다. 일반 보장 질문은 search_terms, 금액 질문에는 쓰지 않는다."),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "앞 대화를 반영한, 혼자 읽어도 뜻이 통하는 완전한 질문"},
            },
            "required": ["question"],
        },
    },
}
EXCLUSIONS_COVERAGE_PER_SECTION = 2
EXCLUSIONS_EXCLUSION_PER_SECTION = 3


def _exclusions_available(ctx):
    from boheom_mate.core import articles
    inv = ctx.inventory or {}
    return inv.get(articles.COVERAGE, 0) > 0 and inv.get(articles.EXCLUSION, 0) > 0


def run_exclusions(args, ctx):
    """design.md 9.3: 보장(구역당 2)·면책(모든 구역, 구역당 3)을 따로 골라 rag.answer로 답한다."""
    from boheom_mate.core import articles
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question이 비어 있습니다.")
    coverage = articles.rank(question, articles.locate(ctx.source, articles.COVERAGE),
                             per_section=EXCLUSIONS_COVERAGE_PER_SECTION)
    exclusions = articles.rank(question, articles.locate(ctx.source, articles.EXCLUSION),
                               per_section=EXCLUSIONS_EXCLUSION_PER_SECTION)
    picked = coverage + exclusions
    result = rag.answer(question, ctx.source, articles=picked) if picked else {
        "found": False, "answer": rag.NOT_FOUND_MESSAGE, "evidence": [], "summary": None}
    found = bool(result["found"] and picked)
    ctx.turn["searches"].append(question)
    ctx.turn["summary"] = ctx.turn["summary"] or result.get("summary")
    evidence = result["evidence"] if found else []
    ctx.add_evidence(evidence)  # rag.answer가 번호·강조 문장을 검증한 조각만
    return {
        "found": found,
        "conclusive": bool(coverage) and bool(exclusions),
        "answer": result["answer"] if found else rag.NOT_FOUND_MESSAGE,
        "coverage": [evidence_label(c) for c in coverage],
        "exclusions": [evidence_label(c) for c in exclusions],
        "cited": [evidence_label(e) for e in evidence],
    }


# ---- identify_generation ------------------------------------------------------------------------

GENERATION_RULES = """[세대 확인]
- 사용자가 실손 가입 시기(연도·월)를 말하거나 몇 세대인지 물으면 identify_generation을 부르세요. 세대를 직접 정하지 마세요.
- 결과에 need가 있으면 그 값(joined_year: 가입 연도, joined_month: 가입 월)을 한 번 물어보세요.
- warning이 있으면 그대로 전하세요. latest_rules는 최신 약관(이 상품) 기준 값이며 가입 세대의 값이 아닙니다.
  1~3세대의 자기부담률·공제금액 숫자는 지어내지 마세요.
"""

GENERATION_TOOL = {
    "type": "function",
    "function": {
        "name": "identify_generation",
        "description": "사용자가 말한 실손 가입 연도·월로 가입 세대(1~4세대)를 확인한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "joined_year": {"type": "integer", "description": "사용자가 말한 실손 가입 연도"},
                "joined_month": {"type": "integer", "description": "사용자가 말한 실손 가입 월(1~12). 모르면 넣지 않는다"},
            },
            "required": ["joined_year"],
        },
    },
}

GENERATION_WARNING = ("현재 앱의 약관·계산은 최신 약관 기준 참고 정보이며 가입하신 세대의 약관과 "
                      "자기부담률·보장이 다를 수 있으니 보험증권과 가입 당시 약관을 확인하세요")
OLD_GENERATIONS = ("1세대", "2세대", "3세대")

# 최신 약관 대비 차이 안내에 쓰는 현재 상품 Verified_Value(자기부담·공제). 라벨에는 숫자를 넣지 않는다(Req 5.4)
LATEST_RULE_LABELS = {
    policy_rules.PROFILE_BASE: {
        "covered_inpatient_pay_rate": "급여 입원 보상 비율",
        "covered_outpatient_clinic_deduct": "급여 통원 공제금액(의원·병원)",
        "non_severe_inpatient_pay_rate": "비중증 비급여 입원 보상 비율",
    },
    policy_rules.PROFILE_SIMPLE: {
        "simple_inpatient_deduct": "입원 공제금액(정액)",
        "simple_inpatient_rate": "입원 공제 비율",
        "simple_outpatient_deduct": "통원 1회당 공제금액",
    },
}

# [금액 계산]의 세대 경계 안내 문장. identify_generation이 bound이면 GENERATION_HINT로 바꾼다
CALCULATE_GENERATION_LINE = ("- 가입 세대를 모르면 한 번 물어보세요(가입 시기: 2009.10 이전=1세대, ~2017.3=2세대, "
                             "~2021.6=3세대, 2021.7 이후=4세대).\n")
GENERATION_HINT = "- 가입 시기를 말하면 identify_generation으로 세대를 확인하세요.\n"


def _year_ok(year):
    return isinstance(year, int) and not isinstance(year, bool) and 1990 <= year <= 2100


def _latest_rules(rule_set):
    """현재 상품 Verified_Value 중 자기부담·공제 값과 그 인용문. rule_set이 ok가 아니면 빈 목록."""
    if not (rule_set or {}).get("ok"):
        return [], []
    labels = LATEST_RULE_LABELS.get(rule_set.get("profile"), {})
    values, quotes = rule_set.get("values") or {}, rule_set.get("quotes") or {}
    keys = [k for k in labels if values.get(k) is not None and quotes.get(k)]
    return [{"label": labels[k], "value": values[k], "quote": quotes[k]} for k in keys], keys


def run_generation(args, ctx):
    """design.md 9.4: memory.generation_of로 세대를 정하고, 모르면 need로 되묻게 한다."""
    year, month = args.get("joined_year"), args.get("joined_month")
    generation = session_memory.generation_of(year, month)
    need = None
    if generation is None:
        need = "joined_month" if _year_ok(year) else "joined_year"
    latest, keys = _latest_rules(ctx.rule_set)
    if generation is not None:
        month_ok = isinstance(month, int) and not isinstance(month, bool) and 1 <= month <= 12
        ctx.turn["memory_updates"].update({"joined_year": year, "joined_month": month if month_ok else None,
                                           "generation": generation})
    if keys:
        ctx.add_evidence(policy_rules.evidence(ctx.rule_set, keys))
    return {
        "generation": generation,
        "need": need,
        "warning": GENERATION_WARNING if generation in OLD_GENERATIONS else None,
        "latest_rules": latest,
    }


# ---- claim_guide --------------------------------------------------------------------------------

CLAIM_RULES = """[청구 서류·기한]
- 보험금 청구 서류나 청구 기한을 물으면 claim_guide를 부르세요. 서류 목록과 기한은 결과의 약관 원문으로만 말하세요.
- 사용자가 이번 또는 앞 대화에서 치료일·사고일·청구권이 생긴 날을 말했으면 YYYY-MM-DD로 바꿔 treatment_date에 꼭 넣으세요.
  예: "2026년 3월 2일" → "2026-03-02"
- 치료일을 말한 적이 없을 때만 treatment_date 없이 부르고, 결과에 need가 있으면 치료일을 한 번 물어보세요.
- deadline이 있으면 날짜와 함께 deadline_note를 그대로 전하세요. deadline이 null이면 날짜를 지어내지 말고 limitation_article 원문만 안내하세요.
"""

CLAIM_TOOL = {
    "type": "function",
    "function": {
        "name": "claim_guide",
        "description": "보험금 청구 서류나 청구 기한을 물을 때. 사용자가 사고일·청구권 발생일을 말해도 치료일로 넣는다.",
        "parameters": {
            "type": "object",
            "properties": {
                "treatment_date": {"type": "string", "description": ("치료일(YYYY-MM-DD). 사용자가 이번 또는 앞 대화에서 치료일·사고일을 말했으면 "
                                                   "이 형식으로 바꿔 반드시 넣는다(예: 2026년 3월 2일 → 2026-03-02)")},
            },
        },
    },
}
CLAIM_DOCUMENTS_MAX_CHARS = 1500
CLAIM_LIMITATION_MAX_CHARS = 800
CLAIM_DOCUMENTS_MARK = "영수증"
CLAIM_LIMITATION_MARK = "소멸시효"
DEADLINE_BASIS = "치료일 기준 참고 날짜"


def _claim_available(ctx):
    from boheom_mate.core import articles
    return (ctx.inventory or {}).get(articles.CLAIM, 0) > 0


def _squash(text):
    return "".join((text or "").split())


def _marked_evidence(chunks, mark):
    """조각마다 mark가 든 문장(원문에 있는 것만)을 highlights로 붙인 근거 항목."""
    out = []
    for c in chunks:
        text = c.get("text") or ""
        whole = _squash(text)
        highlights = [s for s in policy_rules._sentences(text) if mark in s and _squash(s) in whole]
        out.append({"section": c.get("section", ""), "article_no": c.get("article_no", ""),
                    "title": c.get("title", ""), "text": text, "highlights": highlights})
    return out


def _joined(chunks, limit):
    return "\n\n".join(c.get("text") or "" for c in chunks)[:limit]


def run_claim(args, ctx):
    """design.md 9.5: 청구·소멸시효 조항 원문을 근거로 넣고, 기간이 검증됐을 때만 치료일 + N년 참고 날짜를 계산한다."""
    from boheom_mate.core import articles, claim
    documents = articles.locate(ctx.source, articles.CLAIM)
    limitation = articles.locate(ctx.source, articles.LIMITATION)
    ctx.add_evidence(_marked_evidence(documents, CLAIM_DOCUMENTS_MARK))
    ctx.add_evidence(_marked_evidence(limitation, CLAIM_LIMITATION_MARK))
    years = ((ctx.rule_set or {}).get("limitation") or {}).get("years")
    treatment_date = args.get("treatment_date")
    if isinstance(treatment_date, str) and not treatment_date.strip():
        treatment_date = None
    deadline = basis = note = need = None
    if treatment_date is None:
        need = "treatment_date"
    elif years is not None:
        deadline = claim.reference_deadline(claim.parse_date(treatment_date), years).isoformat()
        basis, note = DEADLINE_BASIS, claim.DEADLINE_NOTE
    return {
        "documents_article": _joined(documents, CLAIM_DOCUMENTS_MAX_CHARS),
        "limitation_article": _joined(limitation, CLAIM_LIMITATION_MAX_CHARS),
        "limitation_years": years,
        "deadline": deadline,
        "deadline_basis": basis,
        "deadline_note": note,
        "need": need,
    }


# ---- explain_term -------------------------------------------------------------------------------

TERM_RULES = """[용어 풀이]
- "비급여가 뭐예요?"처럼 약관 용어의 뜻을 물으면 explain_term을 부르세요.
- 뜻은 결과 definitions의 sentences(약관 정의 원문)로만 쉽게 풀어 말하세요. 원문에 없는 뜻을 지어내지 마세요.
- 결과가 found=false면 "약관에서 확인할 수 없다"고 말하세요.
"""

TERM_TOOL = {
    "type": "function",
    "function": {
        "name": "explain_term",
        "description": "약관 용어의 뜻을 물을 때(예: 비급여가 뭐예요?).",
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "뜻을 물은 용어(예: 비급여)"},
            },
            "required": ["term"],
        },
    },
}
TERM_MAX_DEFINITIONS = 3
TERM_SENTENCE_MAX_CHARS = 300   # 표 형태라 문장이 이보다 길면 후보 말이 든 원문 줄만 고른다
_TERM_SPLIT = re.compile(r"[\s·,]+")
_PARENS = re.compile(r"\([^()]*\)")


def _term_available(ctx):
    from boheom_mate.core import articles
    return (ctx.inventory or {}).get(articles.DEFINITION, 0) > 0


def _term_candidates(term):
    """[term] + glossary.lookup(term) 용어에서 괄호 부분을 뺀 2자 이상 낱말(순서 유지, 중복 없음)."""
    from boheom_mate.core import glossary
    out = [term]
    for mapped in glossary.lookup(term):
        out.extend(w for w in _TERM_SPLIT.split(_PARENS.sub(" ", mapped)) if len(w) >= 2)
    seen, result = set(), []
    for w in out:
        key = policy_rules._norm(w)
        if key and key not in seen:
            seen.add(key)
            result.append(w)
    return result


def _definition_form(word, text):
    w = re.escape(word)
    return re.search(rf"{w}\s*[:：]|['‘]{w}['’](이)?란|[\"“]{w}[\"”]\s*(이)?라\s*함은", text) is not None


def _term_highlights(text, word):
    """word가 든 문장을 원문 그대로. 긴(표 형태) 문장은 word가 든 원문 줄로 바꾼다. 원문에 없는 것은 뺀다."""
    key, whole = policy_rules._norm(word), _squash(text)
    picked = []
    for s in policy_rules._sentences(text):
        if key not in policy_rules._norm(s):
            continue
        if len(s) > TERM_SENTENCE_MAX_CHARS:
            picked.extend(ln.strip() for ln in s.split("\n") if key in policy_rules._norm(ln))
        else:
            picked.append(s)
    return [h for h in dict.fromkeys(picked) if h and _squash(h) in whole]


def run_term(args, ctx):
    """design.md 9.6: 일상어를 약관 용어로 바꿔 Definition_Article에서 그 말이 든 정의 조각 상위 3개를 찾는다."""
    from boheom_mate.core import articles
    term = args.get("term")
    if not isinstance(term, str) or not term.strip():
        raise ValueError("term이 비어 있습니다.")
    term = term.strip()
    candidates = _term_candidates(term)
    scored, matched = [], []
    for order, chunk in enumerate(articles.locate(ctx.source, articles.DEFINITION)):
        norm = policy_rules._norm(chunk.get("text"))
        hits = [i for i, w in enumerate(candidates) if policy_rules._norm(w) in norm]
        if not hits:
            continue
        matched.extend(candidates[i] for i in hits if candidates[i] not in matched)
        word = candidates[hits[0]]
        defined = _definition_form(word, chunk.get("text") or "")
        scored.append(((hits[0], not defined, order), chunk, word))
    scored.sort(key=lambda x: x[0])
    evidence = []
    for _, chunk, word in scored[:TERM_MAX_DEFINITIONS]:
        text = chunk.get("text") or ""
        evidence.append({"section": chunk.get("section", ""), "article_no": chunk.get("article_no", ""),
                         "title": chunk.get("title", ""), "text": text,
                         "highlights": _term_highlights(text, word)})
    ctx.add_evidence(evidence)
    result = {
        "found": bool(evidence),
        "term": term,
        "matched_terms": [w for w in candidates if w in matched],
        "definitions": [{"label": evidence_label(e), "sentences": e["highlights"]} for e in evidence],
    }
    if not evidence:
        result["answer"] = rag.NOT_FOUND_MESSAGE
    return result


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
    Tool(name="remaining_limit", schema=REMAINING_TOOL, rules=REMAINING_RULES,
         available=lambda ctx: bool((ctx.rule_set or {}).get("ok")), run=run_remaining, disclaimer=True),
    Tool(name="check_exclusions", schema=EXCLUSIONS_TOOL, rules=EXCLUSIONS_RULES,
         available=_exclusions_available, run=run_exclusions, disclaimer=True),
    Tool(name="identify_generation", schema=GENERATION_TOOL, rules=GENERATION_RULES,
         available=lambda ctx: True, run=run_generation, disclaimer=True),
    Tool(name="claim_guide", schema=CLAIM_TOOL, rules=CLAIM_RULES,
         available=_claim_available, run=run_claim, disclaimer=True),
    Tool(name="explain_term", schema=TERM_TOOL, rules=TERM_RULES,
         available=_term_available, run=run_term, disclaimer=False),
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
