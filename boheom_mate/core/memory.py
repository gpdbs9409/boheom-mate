"""세션 메모리(상담 메모): 한 대화에서 사용자가 말한 사실을 정해진 칸에 모아 둔다.

로그인이 없으므로 브라우저 세션 하나(한 대화 흐름) 안에서만 기억한다.
매 턴 에이전트를 부르기 전에 사용자 말로 메모를 먼저 갱신해 프롬프트에 넣는다.
그래서 같은 턴에 말한 가입 시기도 코드가 세대로 바꿔 계산에 쓴다. 계산이 끝나면 계산에 쓴 값을 더한다.
그래서 대화가 길어져도 가입 세대, 병원 종류 같은 정보를 다시 묻지 않는다.

- 사용자가 직접 말한 것만 적는다. 답변에서 추측한 것은 적지 않는다.
- 이름·주민번호·연락처 같은 개인정보는 적지 않는다.
- 계산기에 실제로 넣은 값(세대, 병원 종류 등)은 LLM 없이 그대로 옮긴다.
"""
import json

GENERATIONS = ("1세대", "2세대", "3세대", "4세대")
CARE = ("통원", "입원")
HOSPITALS = ("의원", "종합병원")
MAX_ITEMS = 5

EMPTY = {
    "joined_year": None,
    "joined_month": None,
    "generation": None,
    "has_other_insurance": None,
    "care": None,
    "hospital": None,
    "severe": None,
    "conditions": [],
    "facts": [],
}

PROMPT = """실손보험 상담 대화에서 사용자에 대해 알게 된 사실을 정해진 칸에 정리하는 담당자입니다.
[이전 메모]와 [이번 대화]를 보고 갱신된 메모를 JSON으로만 답하세요.

{"joined_year": 가입 연도(숫자)|null, "joined_month": 가입 월(1~12)|null,
 "generation": "1세대"|"2세대"|"3세대"|"4세대"|null, "has_other_insurance": true|false|null,
 "care": "통원"|"입원"|null, "hospital": "의원"|"종합병원"|null, "severe": true|false|null,
 "conditions": ["병명·증상·치료"], "facts": ["그 밖에 상담에 필요한 사실"]}

- 사용자가 직접 말한 것만 적으세요. 상담 도우미의 답변 내용이나 추측은 적지 마세요.
- 가입 시기를 말하면 joined_year, joined_month에 숫자로 적으세요(월을 말하지 않았으면 joined_month는 null).
  generation은 사용자가 "2세대"처럼 세대를 직접 말했을 때만 적으세요. 가입 시기에서 세대를 계산하지 마세요.
- hospital: 동네 의원·병원·치과·한의원·약국은 "의원", 종합병원·대학병원은 "종합병원".
- severe: 암 등 중증(산정특례 대상) 질환 치료라고 말했을 때만 true.
- 사용자가 앞에서 한 말을 바꾸면 새 말을 따르세요. 언급이 없으면 이전 값을 그대로 두세요.
- conditions, facts는 각각 최대 5개, 짧게. 금액·횟수 같은 숫자는 facts에 넣어도 됩니다(예: "올해 도수치료 10회 받음").
- 이름, 주민등록번호, 전화번호, 주소 같은 개인정보는 적지 마세요."""


def _str_list(value, limit):
    items = value if isinstance(value, list) else []
    out = []
    for x in items:
        s = str(x).strip()
        if s and s not in out:
            out.append(s[:limit])
    return out[-MAX_ITEMS:]


def generation_of(year, month=None):
    """가입 연·월로 실손 세대를 정한다. 경계 연도에 월을 모르면 None(사용자에게 다시 묻게 한다).
    1세대: ~2009년 9월 / 2세대: 2009년 10월~2017년 3월 / 3세대: 2017년 4월~2021년 6월 / 4세대: 2021년 7월~"""
    if not isinstance(year, int) or not 1990 <= year <= 2100:
        return None
    if month is not None and not (isinstance(month, int) and 1 <= month <= 12):
        month = None
    boundaries = [(2009, 10, "1세대", "2세대"), (2017, 4, "2세대", "3세대"), (2021, 7, "3세대", "4세대")]
    for b_year, b_month, before, after in boundaries:
        if year < b_year:
            return before
        if year == b_year:
            if month is None:
                return None
            return before if month < b_month else after
    return "4세대"


def clean(raw):
    """LLM이 준 메모를 정해진 칸과 보기로만 남긴다. 가입 연·월이 있으면 세대는 코드가 계산한다."""
    raw = raw if isinstance(raw, dict) else {}
    pick = lambda v, allowed: v if v in allowed else None
    boolean = lambda v: v if isinstance(v, bool) else None
    integer = lambda v: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer() else None
    year, month = integer(raw.get("joined_year")), integer(raw.get("joined_month"))
    by_date = generation_of(year, month)
    return {
        "joined_year": year if by_date or year else None,
        "joined_month": month if year else None,
        "generation": by_date or pick(raw.get("generation"), GENERATIONS),
        "has_other_insurance": boolean(raw.get("has_other_insurance")),
        "care": pick(raw.get("care"), CARE),
        "hospital": pick(raw.get("hospital"), HOSPITALS),
        # 중증은 "그렇다"고 들었을 때만 적는다. 말하지 않은 것을 "아니요"로 적으면 다음 계산을 잘못 이끈다
        "severe": True if raw.get("severe") is True else None,
        "conditions": _str_list(raw.get("conditions"), 30),
        "facts": _str_list(raw.get("facts"), 60),
    }


def merge_calculation(memory, inputs):
    """계산기에 넣은 값은 사용자가 확인해 준 정보이므로 메모에 그대로 반영한다."""
    memory = dict(memory)
    for key in ("generation", "has_other_insurance", "care", "hospital", "severe"):
        if inputs and inputs.get(key) is not None:
            memory[key] = inputs[key]
    return clean(memory)


def update(memory, user_text, assistant_text="", calculation=None):
    """사용자 말(과 있으면 답변)로 메모를 갱신한다. 실패하면 이전 메모를 그대로 쓴다."""
    from boheom_mate.core import rag

    previous = clean(memory or EMPTY)
    try:
        raw = rag._ask_json(PROMPT, (
            f"[이전 메모]\n{json.dumps(previous, ensure_ascii=False)}\n\n"
            f"[이번 대화]\n사용자: {user_text}" + (f"\n상담 도우미: {assistant_text}" if assistant_text else "")))
        updated = clean(raw)
    except Exception:
        updated = previous
    if calculation:
        updated = merge_calculation(updated, calculation.get("inputs"))
    return updated


def describe(memory):
    """메모를 사람이 읽는 줄 목록으로. 비어 있으면 빈 목록."""
    m = clean(memory or EMPTY)
    yes_no = {True: "예", False: "아니요"}
    lines = []
    if m["joined_year"]:
        when = f"{m['joined_year']}년" + (f" {m['joined_month']}월" if m["joined_month"] else "")
        lines.append(f"가입 시기: {when}")
    if m["generation"]:
        lines.append(f"가입 세대: {m['generation']}")
    if m["has_other_insurance"] is not None:
        lines.append(f"다른 실손 가입: {yes_no[m['has_other_insurance']]}")
    if m["care"]:
        lines.append(f"치료 형태: {m['care']}")
    if m["hospital"]:
        lines.append(f"병원 종류: {'동네 의원·병원' if m['hospital'] == '의원' else '종합병원·대학병원'}")
    if m["severe"] is not None:
        lines.append(f"중증(산정특례) 치료: {yes_no[m['severe']]}")
    if m["conditions"]:
        lines.append(f"병명·증상: {', '.join(m['conditions'])}")
    lines += [f"참고: {f}" for f in m["facts"]]
    return lines


def prompt_block(memory):
    """채팅 에이전트 시스템 프롬프트에 붙일 글. 메모가 비어 있으면 빈 문자열."""
    lines = describe(memory)
    if not lines:
        return ""
    return ("\n\n[이 대화에서 기억하는 정보]\n사용자가 앞에서 직접 말한 내용입니다. 이미 아는 내용은 다시 묻지 말고 쓰세요. "
            "계산할 때도 이 값을 넣으세요. 사용자가 다르게 말하면 새 말을 따르세요.\n"
            + "\n".join(f"- {line}" for line in lines))
