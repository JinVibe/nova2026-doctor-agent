"""사실 커버리지 채점: 모범 기록 S의 사실 목록(증례별 루브릭, 1회 추출·캐시) 중 대화에서 실제로 물어 확인한 것을 센다.

  python dev.py bench/coverage.py v2.4b v2.4j ...     → 태그마다 증례별 커버리지(%)·친절도(1~10) 표 + <결과>/coverage.json
  python dev.py bench/coverage.py --rubric            → 루브릭만 (다시) 추출

6축 근사 채점(bench/grade.py)보다 잡음이 작은 정보수집 대리 지표. 공식 루브릭은 비공개라 모범 기록 S를 그 근사로 쓴다.
sol 호출: 루브릭 추출 증례당 1회(캐시), 채점 실행·증례당 1회(medium).
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nova_sdk import Client  # noqa: E402

from agent.llm import parse_json, with_retry  # noqa: E402

SAMPLES = ROOT / 'nova-devkit' / 'samples'
RUBRIC_DIR = ROOT / 'bench' / 'rubric'
GRADER = 'openai/gpt-6.1-sol'
CASES = ['C01', 'C03', 'C10']

RUBRIC_PROMPT = """아래는 가상 환자 증례의 '모범 초진 기록' S(주관적 정보)와 '환자 대본'입니다.
의사가 문진으로 확인해야 할 **개별 사실**을 빠짐없이 목록으로 뽑으세요. 한 항목 = 환자 답 하나로 확인되는 사실 하나.
- 현병력(시작·경과·양상·정도·악화/완화·동반 증상 각각), 이전 치료·반응, 약(이름·용량·횟수·기간·변화·금단 등 각각), 과거력, 가족력(구성원별), 사회력(흡연·음주·직업·카페인 등), 일상 영향, 환자의 걱정·기대, 그리고 모범 기록에 (-)로 적힌 음성 확인 항목도 각각 넣습니다.
- 첫 진술(start_information)에 이미 나온 사실은 넣지 않습니다.
- 소아면 출생·발달·영양·수면·부모 키·사춘기 징후 같은 소아 항목도 각각.
JSON 객체 하나만: {"facts": [{"id": 1, "section": "hpi|pmh|meds|fh|sh|ros|ice", "fact": "사실 한 문장(짧게)"}]}"""

GRADE_PROMPT = """당신은 의료 AI 경진대회의 정보수집 채점관입니다. '사실 목록'의 각 항목이 아래 '대화 기록'에서 **의사가 물어서 환자가 답해 확인되었는지** 판정합니다.
- covered: 의사의 질문과 환자의 답으로 그 사실(또는 그 음성)이 확인됨. 환자가 자발적으로 말한 것도 인정. 의사가 묻지 않았거나 환자가 답하지 않은 것은 제외.
- 부분 확인(예: 약 이름만 들었고 용량은 못 들음)은 해당 항목만 covered로.
또한 '친절도'를 1~10으로 매깁니다(인사·호명, 환자 말에 대한 반응, 공감, 쉬운 말, 설명의 구조, 질문 유도와 답변, 안심시키는 마무리). 기계적으로 반복되는 표현은 감점.
JSON 객체 하나만: {"covered": [id, ...], "kindness": 7, "kindness_note": "한 줄", "best_asked": ["대화에서 특히 좋았던 질문 1~2개"], "wasted": ["사실 목록과 무관하거나 중복인 질문 1~3개"]}"""


def text(reply):
    return (reply.get('choices') or [{}])[0].get('message', {}).get('content') or '' if isinstance(reply, dict) else str(reply or '')


def transcript(events):
    lines = []
    for e in events:
        tag = {'SAY': '의사', 'EXAM': '진찰'}.get(e['kind'], e['kind'])
        lines.append(f"[턴 {e['turn']}] {tag}: {e['sent']} → {e['reply']}")
    return '\n'.join(lines)


def extract_rubric(client, case):
    sample = SAMPLES / case
    cj = json.loads((sample / 'case.json').read_text(encoding='utf-8'))
    model = json.loads((sample / 'model_record.json').read_text(encoding='utf-8'))
    user = '\n\n'.join(['## 첫 진술\n' + json.dumps(cj.get('start_information'), ensure_ascii=False),
                        '## 모범 기록 S\n' + json.dumps(model.get('S', model), ensure_ascii=False),
                        '## 환자 대본\n' + json.dumps(cj.get('patient_script'), ensure_ascii=False)])
    reply = with_retry(client.chat, [{'role': 'system', 'content': RUBRIC_PROMPT}, {'role': 'user', 'content': user}],
                       max_tokens=12000, reasoning_effort='medium', model=GRADER)
    data = parse_json(text(reply)) or {}
    facts = [f for f in data.get('facts') or [] if isinstance(f, dict) and f.get('fact')]
    for i, f in enumerate(facts, 1):
        f['id'] = i
    RUBRIC_DIR.mkdir(parents=True, exist_ok=True)
    (RUBRIC_DIR / f'{case}.json').write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding='utf-8')
    return facts


def rubric(client, case):
    p = RUBRIC_DIR / f'{case}.json'
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    return extract_rubric(client, case)


def grade(client, case_dir):
    case = case_dir.name
    log_p = case_dir / 'agent_log.json'
    if not log_p.exists():
        return None
    log = json.loads(log_p.read_text(encoding='utf-8'))
    facts = rubric(client, case)
    user = '## 사실 목록\n' + '\n'.join(f"{f['id']}. [{f.get('section','')}] {f['fact']}" for f in facts) \
        + '\n\n## 대화 기록\n' + transcript([e for e in log['events'] if e['kind'] in ('SAY', 'EXAM')])
    reply = with_retry(client.chat, [{'role': 'system', 'content': GRADE_PROMPT}, {'role': 'user', 'content': user}],
                       max_tokens=8000, reasoning_effort='medium', model=GRADER)
    data = parse_json(text(reply)) or {}
    ids = {f['id'] for f in facts}
    covered = sorted({int(i) for i in data.get('covered') or [] if str(i).isdigit() and int(i) in ids})
    result = {'total': len(facts), 'covered': covered, 'n': len(covered), 'pct': round(100 * len(covered) / max(1, len(facts)), 1),
              'kindness': data.get('kindness'), 'kindness_note': data.get('kindness_note'),
              'best_asked': data.get('best_asked'), 'wasted': data.get('wasted'),
              'missed': [f['fact'] for f in facts if f['id'] not in covered]}
    (case_dir / 'coverage.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result


def main():
    import os
    client = Client(token=os.environ['NOVA_DEV_KEY'])
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--rubric' in sys.argv:
        for case in CASES:
            facts = extract_rubric(client, case)
            print(case, len(facts), '항목')
        if not args:
            return
    for case in CASES:
        rubric(client, case)
    jobs = [(tag, ROOT / 'bench' / 'results' / tag / case) for tag in args for case in CASES]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda j: (j[0], j[1].name, grade(client, j[1])), jobs))
    by_tag = {}
    for tag, case, r in results:
        by_tag.setdefault(tag, {})[case] = r
    print(f"{'태그':<16}" + ''.join(f"{c:>12}" for c in CASES) + f"{'평균%':>8}{'친절':>6}")
    for tag, rows in by_tag.items():
        cells, pcts, kind = [], [], []
        for c in CASES:
            r = rows.get(c)
            if r:
                cells.append(f"{r['n']}/{r['total']}({r['pct']:.0f})".rjust(12))
                pcts.append(r['pct'])
                if isinstance(r.get('kindness'), (int, float)):
                    kind.append(r['kindness'])
            else:
                cells.append('—'.rjust(12))
        avg = sum(pcts) / len(pcts) if pcts else 0
        k = sum(kind) / len(kind) if kind else 0
        print(f"{tag:<16}" + ''.join(cells) + f"{avg:>8.1f}{k:>6.1f}")


if __name__ == '__main__':
    main()
