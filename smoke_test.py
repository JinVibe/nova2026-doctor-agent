"""gpt-oss-20b 동작/속도 확인용 스모크 테스트.

로컬(Ollama)과 엘리스 API를 환경변수로 바꿔 끼운다.
  LLM_BASE_URL  기본값 http://localhost:11434/v1 (Ollama)
  LLM_API_KEY   Ollama는 아무 값이나 OK
  LLM_MODEL     기본값 gpt-oss:20b (엘리스에서는 openai/gpt-oss-20b 등으로 변경)
"""
import os
import sys
import time

from openai import OpenAI

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
API_KEY = os.environ.get("LLM_API_KEY", "ollama")
MODEL = os.environ.get("LLM_MODEL", "gpt-oss:20b")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

SYSTEM = (
    "당신은 외래 초진 의사입니다. 환자의 첫마디와 활력징후를 보고 "
    "감별진단 3개와, 이를 구별하기 위해 가장 먼저 할 질문 3개(각 30자 이하)를 "
    "JSON으로만 답하세요. 형식: {\"ddx\": [...], \"questions\": [...]}"
)
CASE = (
    "환자: 김OO, 58세 남성. '어제 저녁부터 가슴이 답답하고 식은땀이 나요.'\n"
    "활력징후: BP 150/95, HR 104, RR 22, BT 36.8, SpO2 95%"
)


def run(effort: str) -> None:
    t0 = time.time()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": CASE}],
        reasoning_effort=effort,
        temperature=0.2,
    )
    dt = time.time() - t0
    u = resp.usage
    print(f"\n=== reasoning_effort={effort} | {dt:.1f}s | "
          f"in {u.prompt_tokens} / out {u.completion_tokens} tok "
          f"({u.completion_tokens / dt:.1f} tok/s) ===")
    print(resp.choices[0].message.content)


if __name__ == "__main__":
    print(f"endpoint={BASE_URL} model={MODEL}")
    for effort in sys.argv[1:] or ["low", "medium"]:
        run(effort)
