"""S·O·A·P 초진 기록 작성.

S·A·P는 Doctor 모델이 쓰고, O.exam은 코드가 진찰 로그에서 턴 번호와 함께 직접 쓴다(지어낼 여지 없음).
"""
import re

from nova_sdk import RECORD_FIELDS, empty_record

from .prompts import RECORD_PROMPT


HEDGES = ('의심', '잠정', '가능성', '추정', '소견', '(r/o)', 'r/o')


def clean_diagnosis(text):
    """주진단은 진단명만: 괄호 설명과 '의심·잠정·가능성' 같은 군말을 뗀다."""
    text = re.sub(r'\s*[(（][^)）]*[)）]\s*$', '', ' '.join(str(text or '').split()))
    changed = True
    while changed and text:
        changed = False
        for h in HEDGES:
            if text.endswith(h):
                text = text[: -len(h)].rstrip(' ,.·의로') ; changed = True
    return text.strip()


def _text(value):
    if isinstance(value, list):
        return '\n'.join(map(str, value))
    return str(value or '').strip()


def write_record(doctor, visit, closing_lines, diagnosis):
    user = (visit.situation() + '\n\n진료 마무리 설명(실제로 환자에게 한 말)\n' + ('\n'.join(closing_lines) or '(없음)')
            + f'\n\n잠정 주진단: {diagnosis}')
    result = doctor.ask_json(RECORD_PROMPT, user, max_tokens=8000,
                             check=lambda v: isinstance(v.get('S'), dict)) or {}

    note = empty_record()
    for section, fields in RECORD_FIELDS.items():
        values = result.get(section) if isinstance(result.get(section), dict) else {}
        for field in fields:
            note[section][field] = _text(values.get(field))

    note['O']['exam'] = visit.exam_findings()
    note['O']['tests'] = ''  # 예선은 검사 결과 없음

    said = '\n'.join(f'- {line}' for line in closing_lines)
    if said:
        note['P']['education'] = (note['P']['education'] + '\n' if note['P']['education'] else '') + '[설명함]\n' + said
    if not any(note['S'].values()):  # 기록 작성 실패 시 대화 원문이라도 남긴다
        note['S']['hpi'] = visit.transcript()
    final = clean_diagnosis(result.get('diagnosis')) or clean_diagnosis(diagnosis) or '진단 미정'
    return note, final
