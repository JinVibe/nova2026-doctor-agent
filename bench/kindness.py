"""의사소통(친절도) 전용 로컬 채점: 대화 기록만 보고 sol low가 0~15점을 매긴다(공식 "친절도 gpt-6.1-sol low"를 흉내).

  python dev.py bench/kindness.py v2.4b v2.4j ... [--repeat 2]   → 태그별 증례 점수·평균, <결과>/kindness.json
공식 소통 점수가 있는 버전들로 방향이 맞는지 먼저 검증한 뒤 소통 실험의 사전 필터로 쓴다.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nova_sdk import Client  # noqa: E402

from agent.llm import parse_json, with_retry  # noqa: E402

GRADER = 'openai/gpt-6.1-sol'
CASES = ['C01', 'C03', 'C10']

PROMPT = """당신은 의료 AI 경진대회에서 '의사소통(친절도)' 영역만 채점하는 평가자입니다. 아래 외래 초진 대화(의사 발화 → 환자 답)를 읽고 0~15점을 매깁니다.
기준(각 0~3점, 합 15):
① 공감·존중: 환자의 걱정·고생에 진심으로 반응하는가. 형식적이거나 기계적으로 반복되는 표현은 오히려 감점.
② 질문 방식: 한 번에 한 질문, 쉬운 말, 환자가 답하기 편한 흐름, 환자의 답을 이어받는 자연스러움.
③ 설명의 명확성: 진단(가능성)·근거·검사·치료·주의사항·재방문을 환자가 알아듣는 말로 빠짐없이 설명했는가.
④ 환자 참여: 환자의 질문을 유도하고 구체적으로 답했는가, 환자의 걱정에 직접 답했는가, 안심시키는 마무리.
⑤ 전반적 태도: 인사·호명 등 예의, 일관된 존댓말, 환자를 사람으로 대하는 느낌.
JSON 객체 하나만: {"scores": {"공감": 0, "질문": 0, "설명": 0, "참여": 0, "태도": 0}, "total": 0, "note": "한 줄 이유(감점 요인 중심)"}"""


def text(reply):
    return (reply.get('choices') or [{}])[0].get('message', {}).get('content') or '' if isinstance(reply, dict) else str(reply or '')


def transcript(events):
    return '\n'.join(f"[턴 {e['turn']}] {'의사' if e['kind'] == 'SAY' else '진찰'}: {e['sent']} → {e['reply']}"
                     for e in events if e['kind'] in ('SAY', 'EXAM'))


def grade(client, case_dir, repeat):
    p = case_dir / 'agent_log.json'
    if not p.exists():
        return None
    log = json.loads(p.read_text(encoding='utf-8'))
    user = '## 대화 기록\n' + transcript(log['events'])
    runs = []
    for _ in range(repeat):
        reply = with_retry(client.chat, [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': user}],
                           max_tokens=3000, reasoning_effort='low', model=GRADER)
        data = parse_json(text(reply)) or {}
        sc = data.get('scores') or {}
        total = sum(float(v) for v in sc.values() if isinstance(v, (int, float))) if sc else float(data.get('total') or 0)
        runs.append({'total': min(total, 15), 'scores': sc, 'note': data.get('note')})
    result = {'mean': round(sum(r['total'] for r in runs) / len(runs), 2), 'runs': runs}
    (case_dir / 'kindness.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result


def main():
    import os
    client = Client(token=os.environ['NOVA_DEV_KEY'])
    repeat = int(sys.argv[sys.argv.index('--repeat') + 1]) if '--repeat' in sys.argv else 1
    args = [a for a in sys.argv[1:] if not a.startswith('--') and not a.isdigit()]
    jobs = [(tag, ROOT / 'bench' / 'results' / tag / case) for tag in args for case in CASES]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda j: (j[0], j[1].name, grade(client, j[1], repeat)), jobs))
    by_tag = {}
    for tag, case, r in results:
        by_tag.setdefault(tag, {})[case] = r
    print(f"{'태그':<16}" + ''.join(f"{c:>8}" for c in CASES) + f"{'평균':>8}")
    for tag, rows in by_tag.items():
        vals = [rows[c]['mean'] for c in CASES if rows.get(c)]
        print(f"{tag:<16}" + ''.join(f"{rows[c]['mean']:>8.1f}" if rows.get(c) else f"{'—':>8}" for c in CASES) + f"{sum(vals)/len(vals):>8.2f}")
    for tag, rows in by_tag.items():
        for c in CASES:
            if rows.get(c):
                print(f"  {tag} {c}: {rows[c]['runs'][0].get('note')}")


if __name__ == '__main__':
    main()
