"""Doctor 모델 호출: 재시도, JSON 파싱, 호출 통계.

temperature는 보내지 않는다(예선 Doctor가 gpt-6-luna로 바뀌어 미지원).
"""
import inspect
import json
import re
import time
import uuid

from nova_sdk import NovaError

from . import config

# 모델·네트워크 일시 오류 — 턴을 쓰지 않으므로 잠시 뒤 다시 보낸다.
RETRY_CODES = {'PROVIDER_UNAVAILABLE', 'PROVIDER_RESPONSE_INVALID', 'PATIENT_RESPONSE_INVALID',
               'EXAMINER_RESPONSE_INVALID', 'API_UNAVAILABLE', 'REQUEST_PENDING', 'LOCAL_GATEWAY_UNAVAILABLE',
               'INVALID_API_RESPONSE'}  # 게이트웨이가 JSON 아닌 응답(HTML 오류 페이지 등)을 준 경우
# 진료를 더 할 수 없는 상태.
STOP_CODES = {'TURN_LIMIT_REACHED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'RUN_NOT_ACTIVE'}


# 같은 request_id로 다시 보내야 하는 오류(문서: 처리 중이거나 제공자 장애 → 끝난 답을 그대로 받음).
SAME_REQUEST_CODES = {'PROVIDER_UNAVAILABLE', 'REQUEST_PENDING', 'API_UNAVAILABLE', 'LOCAL_GATEWAY_UNAVAILABLE',
                      'INVALID_API_RESPONSE'}


def with_retry(call, *args, waits=(2, 5, 10, 20, 30), **kwargs):
    """일시 오류면 기다렸다 다시 부른다. request_id를 받는 호출(say·exam·chat)은 같은 ID로 재시도한다."""
    takes_id = 'request_id' in inspect.signature(call).parameters
    request_id = str(uuid.uuid4())
    for wait in (*waits, None):
        try:
            if takes_id:
                kwargs['request_id'] = request_id
            return call(*args, **kwargs)
        except NovaError as exc:
            if exc.code not in RETRY_CODES or wait is None:
                raise
            if exc.code not in SAME_REQUEST_CODES:  # 빈 답 등은 새 요청으로
                request_id = str(uuid.uuid4())
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
    def __init__(self, client, effort=None):
        self.client = client
        self.effort = effort or config.DOCTOR_EFFORT
        self.calls = 0
        self.failures = 0
        self.usage = {}  # 서버 usage 필드 누적(prompt_tokens·completion_tokens…). 효율 점수의 절반이 Doctor 비용(10/16 운영진 메일)

    def ask(self, system, user, max_tokens=4096):
        self.calls += 1
        completion = with_retry(self.client.chat, [{'role': 'system', 'content': system},
                                                   {'role': 'user', 'content': user}],
                                max_tokens=max_tokens, reasoning_effort=self.effort)
        self.add_usage(completion.get('usage'))
        return completion['choices'][0]['message'].get('content') or ''

    def add_usage(self, usage, prefix=''):
        for key, value in (usage or {}).items():
            if isinstance(value, dict):
                self.add_usage(value, prefix + key + '.')
            elif isinstance(value, (int, float)):
                self.usage[prefix + key] = self.usage.get(prefix + key, 0) + value

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
