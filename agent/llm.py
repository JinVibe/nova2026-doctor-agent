"""Doctor 모델 호출: 재시도, JSON 파싱, 호출 통계.

temperature는 보내지 않는다(예선 Doctor가 gpt-6-luna로 바뀌어 미지원).
"""
import json
import re
import time

from nova_sdk import NovaError

# 모델·네트워크 일시 오류 — 턴을 쓰지 않으므로 잠시 뒤 다시 보낸다.
RETRY_CODES = {'PROVIDER_UNAVAILABLE', 'PROVIDER_RESPONSE_INVALID', 'PATIENT_RESPONSE_INVALID',
               'EXAMINER_RESPONSE_INVALID', 'API_UNAVAILABLE', 'REQUEST_PENDING', 'LOCAL_GATEWAY_UNAVAILABLE'}
# 진료를 더 할 수 없는 상태.
STOP_CODES = {'TURN_LIMIT_REACHED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'RUN_NOT_ACTIVE'}


def with_retry(call, *args, waits=(2, 5, 10), **kwargs):
    for wait in (*waits, None):
        try:
            return call(*args, **kwargs)
        except NovaError as exc:
            if exc.code not in RETRY_CODES or wait is None:
                raise
            time.sleep(wait)


def parse_json(text):
    """모델 출력에서 첫 JSON 객체를 꺼낸다(코드 블록이나 앞뒤 설명이 붙어도 됨)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', text or ''):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class Doctor:
    def __init__(self, client, effort='medium'):
        self.client = client
        self.effort = effort
        self.calls = 0
        self.failures = 0

    def ask(self, system, user, max_tokens=4096):
        self.calls += 1
        completion = with_retry(self.client.chat, [{'role': 'system', 'content': system},
                                                   {'role': 'user', 'content': user}],
                                max_tokens=max_tokens, reasoning_effort=self.effort)
        return completion['choices'][0]['message'].get('content') or ''

    def ask_json(self, system, user, *, max_tokens=4096, check=None, tries=2):
        """JSON 객체 하나를 받는다. 형식이 틀리면 한 번 더 묻고, 호출이 실패하면 None."""
        for _ in range(tries):
            try:
                value = parse_json(self.ask(system, user, max_tokens))
            except NovaError:
                self.failures += 1
                return None
            if value is not None and (check is None or check(value)):
                return value
            user += '\n(직전 출력이 형식에 맞지 않았습니다. 지시한 JSON 객체 하나만 출력하세요.)'
        return None
