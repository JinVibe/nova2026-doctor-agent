"""S·O·A·P 초진 기록 작성.

S·A·P는 Doctor 모델이 쓰고, O.exam은 코드가 진찰 로그에서 턴 번호와 함께 직접 쓴다(지어낼 여지 없음).
"""
import json
import os
import re

from nova_sdk import RECORD_FIELDS, empty_record

from . import config
from .prompts import RECORD_PROMPT, VERIFY_AP_PROMPT, VERIFY_PROMPT

VERIFY = config.VERIFY_S and os.getenv('NOVA_VERIFY_S', '1') != '0'  # S 항목 사실 검증(Doctor 호출 1회 추가)
VERIFY_AP = config.VERIFY_AP and os.getenv('NOVA_VERIFY_AP', '1') != '0'  # A·P 검증(Doctor 호출 1회 추가)


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


def verify_subjective(doctor, visit, note):
    """S 초안을 대화 원문과 대조해 근거 없는 내용은 지우고 빠진 (-) 소견은 넣는다. 실패하면 초안 유지."""
    user = visit.situation() + '\n\nS 초안\n' + json.dumps(note['S'], ensure_ascii=False, indent=1)
    result = doctor.ask_json(VERIFY_PROMPT, user, max_tokens=8000,
                             check=lambda v: isinstance(v.get('S'), dict) and any(_text(x) for x in v['S'].values()))
    if not result:
        return {'status': 'failed'}
    fixed = {f: _text(result['S'].get(f)) for f in RECORD_FIELDS['S']}
    # 감수가 내용을 절반 넘게 날렸으면(과잉 삭제 의심) 초안을 지킨다.
    if sum(map(len, fixed.values())) < 0.5 * sum(map(len, note['S'].values())):
        return {'status': 'rejected_overcut'}
    note['S'].update(fixed)  # note에는 S/O/A/P 외 키를 넣지 않는다(SDK가 거절함)
    return {'status': 'applied', 'removed': result.get('removed') or [], 'added': result.get('added') or []}


def verify_assessment_plan(doctor, visit, note, closing_lines):
    """A·P 초안을 대화 원문·실제 마무리 설명과 대조: 근거 없는 사실 삭제, 하지 않은 안내는 '안내 예정'으로. 실패하면 초안 유지."""
    user = (visit.situation() + '\n\n진료 마무리 설명(실제로 환자에게 한 말)\n' + ('\n'.join(closing_lines) or '(없음)')
            + '\n\nA 초안\n' + json.dumps(note['A'], ensure_ascii=False, indent=1)
            + '\n\nP 초안\n' + json.dumps(note['P'], ensure_ascii=False, indent=1))
    result = doctor.ask_json(VERIFY_AP_PROMPT, user, max_tokens=8000,
                             check=lambda v: isinstance(v.get('A'), dict) and isinstance(v.get('P'), dict))
    if not result:
        return {'status': 'failed'}
    fixed = {s: {f: _text(result[s].get(f)) for f in RECORD_FIELDS[s]} for s in ('A', 'P')}
    before = sum(len(v) for s in ('A', 'P') for v in note[s].values())
    after = sum(len(v) for s in fixed for v in fixed[s].values())
    if after < 0.6 * before:  # 과잉 삭제 의심
        return {'status': 'rejected_overcut'}
    for s in ('A', 'P'):
        note[s].update(fixed[s])
    return {'status': 'applied', 'fixed': result.get('fixed') or []}


def write_record(doctor, visit, closing_lines, diagnosis, references=''):
    user = (visit.situation() + '\n\n진료 마무리 설명(실제로 환자에게 한 말)\n' + ('\n'.join(closing_lines) or '(없음)')
            + f'\n\n잠정 주진단: {diagnosis}')
    if references:
        user += '\n\n참고 지침(발췌)\n' + references
    result = doctor.ask_json(RECORD_PROMPT, user, max_tokens=8000,
                             check=lambda v: isinstance(v.get('S'), dict)) or {}

    note = empty_record()
    for section, fields in RECORD_FIELDS.items():
        values = result.get(section) if isinstance(result.get(section), dict) else {}
        for field in fields:
            note[section][field] = _text(values.get(field))

    note['O']['exam'] = visit.exam_findings()
    note['O']['tests'] = ''  # 예선은 검사 결과 없음
    visit.verify_log = verify_subjective(doctor, visit, note) if VERIFY and any(note['S'].values()) else {'status': 'skipped'}

    said = '\n'.join(f'- {line}' for line in closing_lines)
    if said:
        note['P']['education'] = (note['P']['education'] + '\n' if note['P']['education'] else '') + '[설명함]\n' + said
    if VERIFY_AP and any(note['A'].values()):
        visit.verify_log['ap'] = verify_assessment_plan(doctor, visit, note, closing_lines)
    if not any(note['S'].values()):  # 기록 작성 실패 시 대화 원문이라도 남긴다
        note['S']['hpi'] = visit.transcript()
    final = clean_diagnosis(result.get('diagnosis')) or clean_diagnosis(diagnosis) or '진단 미정'
    return note, final
