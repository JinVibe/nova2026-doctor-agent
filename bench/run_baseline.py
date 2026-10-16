"""기초 제출 코드(nova-devkit/run.py)를 개발 키로 공개 샘플에 실행하고 Doctor 토큰 사용량을 기록한다.

  python dev.py bench/run_baseline.py baseline [C01 ...]   → bench/results/baseline/<case>/agent_log.json
효율 점수의 비용 절반을 비교할 기준(기초 코드는 공식 평가에서 효율 20/20).
"""
import json
import os
import runpy
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'nova-devkit'))

import nova_sdk  # noqa: E402

CASES = {'C01': '4fa07144-04e5-4116-a18e-88d8a90cc21a', 'C03': 'ef3649a9-330b-4e12-ad2d-fab94d019330',
         'C10': '35dd8b69-2531-454e-bb8c-fb8030293447'}


class Counting(nova_sdk.Client):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls, self.usage, self.turns = 0, {}, 0

    def chat(self, *a, **k):
        out = super().chat(*a, **k)
        self.calls += 1
        for key, value in (out.get('usage') or {}).items():
            if isinstance(value, (int, float)):
                self.usage[key] = self.usage.get(key, 0) + value
            elif isinstance(value, dict):
                for k2, v2 in value.items():
                    if isinstance(v2, (int, float)):
                        self.usage[key + '.' + k2] = self.usage.get(key + '.' + k2, 0) + v2
        return out

    def action(self, kind, **k):
        out = super().action(kind, **k)
        if kind in ('SAY', 'EXAM') and isinstance(out, dict) and out.get('turns_remaining') is not None:
            self.turns = 50 - out['turns_remaining']
        return out


def run_case(tag, name):
    out = ROOT / 'bench' / 'results' / tag / name
    out.mkdir(parents=True, exist_ok=True)
    try:
        mod = runpy.run_path(str(ROOT / 'nova-devkit' / 'run.py'), run_name='baseline')
        client = Counting(token=os.environ['NOVA_DEV_KEY'], sample=True)
        client.create_sample(CASES[name])
        started = time.time()
        state = mod['prepare']()
        client.ready()
        result = mod['consult'](client, state)
        log = {'turns': client.turns, 'doctor_calls': client.calls, 'doctor_usage': client.usage,
               'seconds': round(time.time() - started), 'result': result}
        (out / 'agent_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
        return name, (result or {}).get('status')
    except Exception:
        (out / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        return name, 'error'


def main():
    tag = sys.argv[1]
    names = sys.argv[2:] or list(CASES)
    with ThreadPoolExecutor(len(names)) as pool:
        for name, status in pool.map(lambda n: run_case(tag, n), names):
            print(name, status)


if __name__ == '__main__':
    main()
