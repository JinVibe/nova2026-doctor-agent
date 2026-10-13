"""공개 샘플 3건을 개발 키로 서버에서 실행하고 진료 로그를 남긴다.

  python dev.py bench/run_samples.py v0.6            → bench/results/v0.6/<C01|C03|C10>/agent_log.json
  python dev.py bench/run_samples.py v0.6 C01        → 한 건만
"""
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nova_sdk import Client  # noqa: E402

import agent.doctor as doctor  # noqa: E402

CASES = {
    'C01': '4fa07144-04e5-4116-a18e-88d8a90cc21a',
    'C03': 'ef3649a9-330b-4e12-ad2d-fab94d019330',
    'C10': '35dd8b69-2531-454e-bb8c-fb8030293447',
}


def run_case(tag, name):
    out = ROOT / 'bench' / 'results' / tag / name
    out.mkdir(parents=True, exist_ok=True)
    try:
        client = Client(token=os.environ['NOVA_DEV_KEY'], sample=True)
        client.create_sample(CASES[name])
        consult = doctor.Consult(client)
        consult.log_dir = out
        result = consult.run()
        return name, result.get('status')
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
