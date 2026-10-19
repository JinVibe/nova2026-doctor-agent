"""가상 환자(로컬 시뮬레이터, luna) 반응 탐침: 요약·확인 발화 등 특정 문장에 환자가 어떻게 답하는지 본다.

  python dev.py bench/probe_patient.py C01 "지금까지 7년간 약 없이는 잠을 못 자신다고 이해했는데 맞나요?" "..."
공개 샘플은 nova-devkit/samples/<C>, 새 증례는 bench/cases/<S>.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'nova-devkit'))

from nova_local import LocalClient, Simulator, load_case  # noqa: E402

from bench.run_local import DOCTOR_MODEL, PATIENT_MODEL, DevEndpoint  # noqa: E402


def main():
    name, *utterances = sys.argv[1:]
    folder = (ROOT / 'nova-devkit' / 'samples' / name) if name.startswith('C') else (ROOT / 'bench' / 'cases' / name)
    case = load_case(folder)
    sim = Simulator(case, doctor=DevEndpoint(DOCTOR_MODEL), sim=DevEndpoint(PATIENT_MODEL, effort='low'), minutes=30)
    client = LocalClient(sim, sample=True)
    for u in utterances:
        try:
            r = client.say(u)
            print(f'의사: {u}\n환자: {r.get("content")}\n')
        except Exception as exc:
            print(f'의사: {u}\n오류: {exc!r}\n')


if __name__ == '__main__':
    main()
