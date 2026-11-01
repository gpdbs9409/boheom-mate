"""HIRA 종별 이름(Hospital_Class) → 계산기 병원 구분(Calculator_Hospital) 매핑.
순수 함수만 둔다(네트워크·파일 I/O 없음). API 호출은 integrations/hira_hospital.py가 맡는다.
"""
import re

GENERAL_CLASSES = ("상급종합병원", "종합병원")


def to_calculator_hospital(class_name):
    """공백 제거 후 GENERAL_CLASSES 중 하나와 같거나 '종합병원'으로 끝나면 "종합병원",
    그 밖(빈 문자열·None 포함)은 "의원"."""
    name = re.sub(r"\s+", "", class_name) if isinstance(class_name, str) else ""
    if name in GENERAL_CLASSES or name.endswith("종합병원"):
        return "종합병원"
    return "의원"
