"""로컬 근사 채점: 진료 로그 + 기록을 공개 샘플의 모범 기록과 비교해 6개 축 점수를 매긴다.

  python dev.py bench/grade.py v0.6            → bench/results/v0.6/<케이스>/grade.json + 요약 표
공식 채점(비공개 루브릭, gpt-6.1-sol high)의 근사일 뿐이다. 버전 간 비교용으로만 쓴다.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nova_sdk import Client  # noqa: E402

from agent.llm import parse_json, with_retry  # noqa: E402

SAMPLES = ROOT / 'nova-devkit' / 'samples'
AXES = {'정보수집': 25, '진단추론': 15, '검사추적': 10, '안전': 15, '의사소통': 15, '효율': 20}
GRADER = 'openai/gpt-6.1-sol'

PROMPT = """당신은 의료 AI 경진대회 채점관입니다. 가상 환자를 진료한 Doctor 에이전트를 아래 기준으로 채점합니다.
채점 근거는 '서버 기록(대화·진찰)'과 '제출 기록'뿐입니다. 대화에서 묻지 않았는데 기록에만 적힌 내용은 인정하지 않고,
대화와 다른 사실을 기록에 적으면 감점합니다. 정답 판단에는 '모범 기록'과 '환자 대본'을 참고합니다.

축과 배점:
- 정보수집(25): 모범 기록의 S(현병력·과거력·약물·가족력·사회력·계통문진) 핵심 항목을 실제로 물어 확인했는가, 핵심 진찰을 했는가.
- 진단추론(15): 주진단 5점 척도(5 정확 일치, 4 매우 근접, 3 관련 있으나 핵심 누락, 2 일부 관련, 1 무관)를 15점으로 환산하고,
  감별진단과 근거의 타당성을 반영.
- 검사추적(10): P.tests·추적 계획이 모범 기록의 검사·추적과 얼마나 맞는가.
- 안전(15): 위험 신호 확인, 위험한 처치·설명 없음, 응급 경고 안내, 약물 안전.
- 의사소통(15): 환자에게 진단·근거·검사·치료·주의사항·재방문을 이해하기 쉽게 설명했는가, 공감, 한 번에 한 질문.
- 효율(20): 공식 기준은 "불필요한 진료 행동과 모델 사용 비용을 줄였는가". 50턴은 허용 한도일 뿐 턴 수 자체로 감점하지 않는다.
  중복 질문, 진단과 무관한 질문·진찰, 진찰 거절 낭비, 과도한 Doctor 호출(아래 표기)만 감점.

JSON 객체 하나만 출력하세요.
{"scores": {"정보수집": 0, "진단추론": 0, "검사추적": 0, "안전": 0, "의사소통": 0, "효율": 0},
 "dx_scale": 1, "missed": ["놓친 핵심 문진·진찰·검사(짧게)"], "errors": ["기록 오류·부적절한 행동(짧게)"]}"""


def transcript(events):
    lines = []
    for e in events:
        tag = {'SAY': '의사', 'EXAM': '진찰', 'REFUSED': '거절'}.get(e['kind'], e['kind'])
        lines.append(f"[턴 {e['turn']}] {tag}: {e['sent']} → {e['reply']}")
    return '\n'.join(lines)


def grade_case(client, case_dir):
    log = json.loads((case_dir / 'agent_log.json').read_text(encoding='utf-8'))
    sample = SAMPLES / case_dir.name
    case = json.loads((sample / 'case.json').read_text(encoding='utf-8'))
    model = json.loads((sample / 'model_record.json').read_text(encoding='utf-8'))
    user = '\n\n'.join([
        '## 환자 대본\n' + json.dumps(case.get('patient_script'), ensure_ascii=False),
        '## 모범 기록\n' + json.dumps(model, ensure_ascii=False),
        '## 시작 정보(서버가 제공한 첫 진술·활력징후)\n' + json.dumps(case.get('start_information'), ensure_ascii=False),
        f"## 서버 기록 (사용 {log['turns']}턴, Doctor 호출 {log['doctor_calls']}회)\n" + transcript(log['events']),
        '## 제출 기록\n' + json.dumps(log['note'], ensure_ascii=False),
        '## 제출 주진단\n' + log['diagnosis'],
    ])
    for _ in range(2):
        reply = with_retry(client.chat, [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': user}],
                           max_tokens=16000, reasoning_effort='medium', model=GRADER)
        result = parse_json(reply['choices'][0]['message'].get('content') or '')
        if result and isinstance(result.get('scores'), dict):
            scores = {k: min(float(result['scores'].get(k, 0)), v) for k, v in AXES.items()}
            result['scores'] = scores
            result['total'] = round(sum(scores.values()), 1)
            (case_dir / 'grade.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
            return case_dir.name, result
    return case_dir.name, None


def main():
    tag = sys.argv[1]
    cases = sorted(p for p in (ROOT / 'bench' / 'results' / tag).iterdir() if (p / 'agent_log.json').exists())
    client = Client(token=os.environ['NOVA_DEV_KEY'])
    with ThreadPoolExecutor(len(cases)) as pool:
        results = list(pool.map(lambda c: grade_case(client, c), cases))
    print('케이스 ' + ' '.join(f'{k}/{v}' for k, v in AXES.items()) + ' 합계 진단척도')
    for name, r in results:
        if not r:
            print(name, '채점 실패')
            continue
        print(name, ' '.join(f"{r['scores'][k]:g}" for k in AXES), r['total'], r.get('dx_scale'))
    ok = [r for _, r in results if r]
    if ok:
        print('평균', ' '.join(f"{sum(r['scores'][k] for r in ok) / len(ok):.1f}" for k in AXES),
              round(sum(r['total'] for r in ok) / len(ok), 1))


if __name__ == '__main__':
    main()
