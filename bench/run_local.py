"""새 증례(bench/cases/<ID>/case.json)를 개발 키트 로컬 시뮬레이터로 진료한다. 환자·진찰 판정은 sol low(공식과 같은 모델), Doctor는 luna.

  python dev.py bench/run_local.py <태그> S01 S02 ...      → bench/results/<태그>/<증례>/agent_log.json (+ sim.json)
(래퍼 스크립트에서 agent.config 플래그를 먼저 세팅한 뒤 runpy로 실행하면 같은 구성으로 돈다.)
"""
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'nova-devkit'))

from nova_sdk import Client  # noqa: E402
from nova_local import Endpoint, LocalClient, Simulator, load_case  # noqa: E402

from agent import doctor  # noqa: E402
from agent.llm import with_retry  # noqa: E402

CASES_DIR = ROOT / 'bench' / 'cases'
PATIENT_MODEL = os.getenv('NOVA_LOCAL_PATIENT', 'openai/gpt-6-luna')  # 공식 환자는 sol low지만 sol은 하루 100회 한도(채점기와 공유) → 기본은 luna(샘플 검증과 같음)
DOCTOR_MODEL = os.getenv('NOVA_LOCAL_DOCTOR', 'openai/gpt-6-luna')


class DevEndpoint(Endpoint):
    """개발 키로 대회 모델을 부르는 Endpoint(request_id 포함). 시뮬레이터가 (내용, 종료 이유, 입력 토큰, 출력 토큰)을 기대한다."""

    def __init__(self, model, effort=None):
        super().__init__('https://dev.local/v1', model)
        self.client = Client(token=os.environ['NOVA_DEV_KEY'])
        self.default_effort = effort

    def complete(self, messages, max_tokens=2048, json_mode=False, reasoning_effort=None, temperature=None):
        effort = reasoning_effort or self.default_effort
        reply = with_retry(self.client.chat, messages, max_tokens=max_tokens, model=self.model,
                           **({'reasoning_effort': effort} if effort else {}))
        choice = (reply.get('choices') or [{}])[0]
        usage = reply.get('usage') or {}
        return ((choice.get('message') or {}).get('content') or '', choice.get('finish_reason') or 'stop',
                int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0))


def run_case(tag, name):
    out = ROOT / 'bench' / 'results' / tag / name
    out.mkdir(parents=True, exist_ok=True)
    try:
        case = load_case(CASES_DIR / name)
        sim = Simulator(case, doctor=DevEndpoint(DOCTOR_MODEL), sim=DevEndpoint(PATIENT_MODEL, effort='low'), minutes=30)
        client = LocalClient(sim, sample=True)
        consult = doctor.Consult(client)
        consult.log_dir = out
        result = consult.run()
        (out / 'sim.json').write_text(json.dumps({'answer': case.get('answer'), 'diagnosis': sim.diagnosis, 'turns': sim.turns,
                                                  'doctor_calls': sim.doctor_calls, 'sim_calls': sim.sim_calls,
                                                  'events': sim.events}, ensure_ascii=False, indent=1), encoding='utf-8')
        return name, result.get('status'), sim.diagnosis, case.get('answer')
    except Exception:
        (out / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        return name, 'error', None, None


def main():
    tag = sys.argv[1]
    names = sys.argv[2:] or sorted(p.name for p in CASES_DIR.iterdir() if (p / 'case.json').exists())
    with ThreadPoolExecutor(min(6, len(names))) as pool:
        for name, status, dx, answer in pool.map(lambda n: run_case(tag, n), names):
            print(f'{name} {status} dx={dx!r} answer={answer!r}')


if __name__ == '__main__':
    main()
