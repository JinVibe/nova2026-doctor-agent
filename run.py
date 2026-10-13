"""N.O.V.A. 2026 예선 제출 진입점. 진료 로직은 agent/ 패키지에 있다."""
from nova_sdk import run_agent

from agent.doctor import consult


def prepare():
    # 예선: 준비할 모델 없음(Doctor는 client.chat). 본선에서 NOVA_MODEL_DIR의 모델을 읽는다.
    return None


if __name__ == '__main__':
    run_agent(prepare, consult)
