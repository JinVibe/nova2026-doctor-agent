"""공개 증례 3건(C01·C03·C10)을 버전마다 어떻게 진료했는지 보여주는 HTML(docs/evolution/*.html) 생성.

    .venv/Scripts/python.exe -X utf8 bench/build_evolution.py

bench/results/<버전>/<증례>/agent_log.json을 읽는다. 새 버전을 돌린 뒤 STAGES에 한 줄 추가하고 다시 실행하면 된다.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'evolution'

# 버전 단계(공통). changes는 에이전트에서 바꾼 것, official은 그 버전의 공식 점수(있을 때).
STAGES = [
    dict(v='baseline', label='기본 에이전트', date='대회 제공', official='', note='대회가 준 출발점',
         changes=['매 턴 모델에게 "다음 행동" JSON을 물어 그대로 전송(Doctor 30회 이상 호출).',
                  '진찰 거절이 잦아 턴을 낭비하고, 설명이 30자를 넘어 재질문이 반복됨.',
                  'C01에서는 결정적 단서(졸피뎀 밤 4알·새벽 2알)를 듣고도 기록에서 누락, "주요 불면증" 오진(샘플 채점 41.2).']),
    dict(v='v0.5', label='v0.5 첫 실행', date='10/12', official='', note='우리 구조의 첫 진료',
         changes=['"계획 → 문진 → 진찰 → 설명" 큐 구조. 질문은 30자·한 항목 규칙을 코드가 검사.',
                  '결정적 단서를 늦게 듣고도 진단을 다시 정하는 단계가 없어 증상 이름을 진단으로 적음.']),
    dict(v='v0.6', label='v0.6 재평가', date='10/13', official='', note='마무리 전에 진단을 다시 정함',
         changes=['REASSESS 단계 신설: 마무리 전에 진료 전체를 다시 읽고 주진단을 정함.',
                  '진단 원칙(증상명보다 원인, 약물·물질을 항상 의심)과 복용약 후속 규칙을 프롬프트에.',
                  '첫 질문 앞에 "많이 불편하셨겠어요." 공감 접두. 턴 상한 46.']),
    dict(v='v1.2', label='v1.2 첫 리더보드', date='10/14', official='68.55 (19위)', note='기록 정직성',
         changes=['공통 문진 8개(과거력·약·수술·알레르기·가족력·흡연·음주·직업; 소아는 출생·발달·예방접종)를 코드가 보장.',
                  'S 기록을 대화 원문과 대조해 근거 없는 문장은 지우는 검증 호출(+4.7).',
                  '마무리 뒤 환자가 되물으면 최대 2턴 답변.']),
    dict(v='v1.7', label='v1.7 마무리 10줄', date='10/15', official='효율 9.7/20', note='턴이 효율을 깎는다는 것을 배움',
         changes=['마무리 10줄: 공감 / 진단 / 근거 / 검사+목적 / 치료 / 피할 것 / 응급 증상 2 / 추적 시점 / 질문 유도.',
                  '턴 상한 50. 공식 채점에서 효율 9.7/20(기본 에이전트 30턴은 20/20) → 턴 수가 감점의 주범.',
                  '여기서부터 턴을 줄이는 쪽으로 방향 전환.']),
    dict(v='v2.0f', label='v2.0f 38턴', date='10/17', official='71.864', note='의존성 약 후속 질문을 코드가 보장',
         changes=['38턴. 수면제·안정제 복용이면 증량·처방 초과·금단 질문을 바로 붙임.',
                  '진찰 7개 상한, 진찰 턴 예약.', '공식 71.864로 당시 최고.']),
    dict(v='v2.1b', label='v2.1b 실패한 실험', date='10/17', official='61.326 (기각)', note='위험 신호 질문을 줄이면 안 된다',
         changes=['계획 질문을 6개로 줄이고 "아니요"로 답할 선별 질문을 2개 이하로 → 서사 심화 우선.',
                  '안전 −4.5, 진단 −3.2. 우울·자살 사고·수면무호흡·두통·시야 같은 음성 확인이 안전·진단 점수였다.',
                  '규칙으로 남김: 계획 단계의 위험 신호·감별 음성 질문은 줄이지 않는다.']),
    dict(v='v2.3d', label='v2.3d 34턴', date='10/18', official='73.994', note='효율 곡선의 적정점',
         changes=['34턴 · 마무리 7줄 · 진찰 5개. 30턴 실험(g)은 효율 +1.6에 안전·소통 −5.3이라 기각.',
                  '30자를 넘어 빠지던 마무리 줄을 모델에게 줄여 달라고 해 살림.',
                  'C03에서 "특발성 저신장"으로 한 번 뒤집힘 → 가족도 비슷했다면 가족의 경과를 묻는 규칙을 추가(다음 버전).']),
    dict(v='v2.4b', label='v2.4b 현재 최고', date='10/18', official='74.599 (20위, 1위 77.559)', note='말투와 환자 관점',
         changes=['환자가 걱정·불안을 말하면 다음 질문 앞에 "걱정되시죠."(진료당 1회).',
                  '마무리 중 환자가 되물으면 그 자리에서 바로 답함.',
                  'ICE 2문항(일상 지장·가장 걱정되는 점)을 코드가 보장. 소통 +0.55, 진단 5증례 모두 5/5.']),
    dict(v='v2.4j', label='v2.4j 기각된 소통 실험', date='10/19', official='69.310 (기각)', note='수용 표현의 기계적 반복은 친절도를 깎는다',
         changes=['첫 턴 "안녕하세요, ○○ 님. 많이 힘드셨겠어요."(이름을 첫 진술에서 추출, 소아는 어머님께 "걱정 많으셨겠어요").',
                  '질문마다 수용 표현("그렇군요. / 네, 알겠습니다. / 네.") — 친절도 채점은 대화 전체를 읽는다.',
                  '마무리는 공감 줄을 빼고 6줄 → 턴 합계 34 그대로. 계획 질문 구성 V4(1위 진단의 특징적 양성 증상), 기록 P 규칙 V2.',
                  '공식 69.310: 소통 −2.05(수용 표현이 기계적 반복으로 보임), 진단 −2.67(계획 질문 규칙 V4가 선별 질문을 바꿔 숨은 증례 진단이 흔들림). 둘 다 폐기.']),
]

OFFICIAL = [('10/13', 'v0.9', 67.5), ('10/14', 'v1.2', 68.55), ('10/15', 'v1.6', 66.5), ('10/17', 'v2.0f', 71.864),
            ('10/17', 'v2.0h', 68.38), ('10/17', 'v2.1b', 61.326), ('10/18', 'v2.3d', 73.994), ('10/18', 'v2.3g', 70.299),
            ('10/18', 'v2.4b', 74.599), ('10/19', 'v2.4f', 73.204), ('10/19', 'v2.4j', 69.31), ('10/19', 'v2.4n', 73.125)]
TOP = 77.559

CASES = {
    'C01': dict(title='박선희 님 진료의 변천', name='박선희', who='62세 여성', complaint='"잠을 잘 못 자서 왔다, 수면제를 처방해 달라"',
                answer='수면제(졸피뎀) 의존', correct=('졸피뎀', '수면제 의존', '진정제', '수면제 사용'), baseline_dx='주요 불면증',
                baseline_note='대회 안내 문서(GUIDE §8)의 C01 결과: 30턴, 4분 20초, Doctor 37회 호출, 진찰 10번 중 6번 거절, 결정적 단서를 듣고도 기록에서 누락, 설명 30자 초과로 재질문 반복. 6축 3.8 · 4.2 · 0 · 11.3 · 2.0 · 20 = 41.2.'),
    'C03': dict(title='김도현 어린이 진료의 변천', name='김도현', who='6세 남아, 어머니 동반', complaint='"또래보다 키가 많이 작은 것 같다" (1년에 5cm, 100→105cm)',
                answer='체질성 성장 지연', correct=('체질성',), baseline_dx='',
                baseline_note='기본 에이전트의 C03 결과는 서버 로그에 대화가 없고 턴·호출 수만 남아 있습니다.'),
    'C10': dict(title='최윤정 님 진료의 변천', name='최윤정', who='32세 여성', complaint='"석 달 동안 다이어트 없이 7kg이 빠졌다" (56→49kg)',
                answer='그레이브스병(갑상선기능항진증)', correct=('그레이브스',), baseline_dx='',
                baseline_note='기본 에이전트의 C10 결과는 서버 로그에 대화가 없고 턴·호출 수만 남아 있습니다.'),
}

ACK_RE = r'^(안녕하세요, [^.]+\.|안녕하세요\.|많이 불편하셨겠어요\.|많이 힘드셨겠어요\.|걱정되시죠\.|그렇군요\.|네, 알겠습니다\.|알겠습니다\.|네\.)\s*'


def load(v, case):
    p = ROOT / 'bench' / 'results' / v / case / 'agent_log.json'
    if not p.exists():
        return None
    log = json.loads(p.read_text(encoding='utf-8'))
    ev = [{'t': e.get('turn'), 'k': e.get('kind'), 's': e.get('sent') or '', 'r': e.get('reply') or ''}
          for e in log.get('events') or [] if e.get('kind') in ('SAY', 'EXAM')]
    return dict(turns=log.get('turns'), calls=log.get('doctor_calls'), tokens=(log.get('doctor_usage') or {}).get('total_tokens'),
                dx=log.get('diagnosis') or '', events=ev, opening=log.get('opening') or '', vitals=log.get('vitals') or '')


def stats(ev):
    exams = [i for i, e in enumerate(ev) if e['k'] == 'EXAM']
    last_exam = exams[-1] if exams else -1
    closing_start = len(ev)
    for i in range(last_exam + 1, len(ev)):
        if ev[i]['k'] == 'SAY' and not ev[i]['s'].rstrip().endswith('?'):
            closing_start = i
            break
    return dict(exams=len(exams), questions=sum(1 for e in ev[:closing_start] if e['k'] == 'SAY'),
                closing=len(ev) - closing_start, closing_start=closing_start)


TEMPLATE = (Path(__file__).parent / 'evolution_template.html').read_text(encoding='utf-8')


def build(case):
    c = CASES[case]
    stages, opening, vitals = [], '', ''
    for s in STAGES:
        d = load(s['v'], case)
        if d is None:
            continue
        ev = d['events']
        st = stats(ev) if ev else dict(exams=0, questions=0, closing=0, closing_start=0)
        dx = d['dx'] or (c['baseline_dx'] if s['v'] == 'baseline' else '')
        if d['opening']:
            opening, vitals = d['opening'], d['vitals']
        stages.append({**s, 'turns': d['turns'], 'calls': d['calls'], 'tokens': d['tokens'], 'dx': dx,
                       'correct': any(k in dx for k in c['correct']) if dx else None, 'events': ev, **st})
    page = (TEMPLATE.replace('__TITLE__', c['title']).replace('__CASE__', case).replace('__NAME__', c['name'])
            .replace('__WHO__', c['who']).replace('__COMPLAINT__', c['complaint']).replace('__ANSWER__', c['answer'])
            .replace('__VITALS__', vitals.replace('\n', ' · ').replace(' : ', ' ').replace(': ', ' '))
            .replace('__BASELINE_NOTE__', c['baseline_note'])
            .replace('__STAGES__', json.dumps(stages, ensure_ascii=False)).replace('__OFFICIAL__', json.dumps(OFFICIAL, ensure_ascii=False))
            .replace('__TOP__', str(TOP)).replace('__ACK__', json.dumps(ACK_RE, ensure_ascii=False)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{case}.html').write_text(page, encoding='utf-8')
    return stages


if __name__ == '__main__':
    for case in CASES:
        st = build(case)
        print(case, ' '.join(f"{s['v']}:{s['turns']}:{'O' if s['correct'] else '-' if s['correct'] is None else 'X'}" for s in st))
    index = ['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>진료의 변천</title>',
             '<style>body{font-family:"IBM Plex Sans KR","Malgun Gothic",sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6;color:#2a2b30;background:#f6f3ec}a{color:#2e7d84}</style></head><body>',
             '<h1>N.O.V.A. 2026 Doctor Agent · 진료의 변천</h1><p>공개 증례 3건을 버전마다 어떻게 진료했는지, 서버 실행 로그의 대화 그대로입니다.</p><ul>']
    for case, c in CASES.items():
        index.append(f'<li><a href="{case}.html">{case} · {c["title"]}</a> — {c["who"]}, {c["complaint"]}</li>')
    index.append('</ul><p>생성: <code>bench/build_evolution.py</code></p></body></html>')
    (OUT / 'index.html').write_text('\n'.join(index), encoding='utf-8')
    print('written', OUT)
