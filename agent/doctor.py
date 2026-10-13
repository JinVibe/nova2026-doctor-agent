"""진료 흐름: PLAN → EXECUTE → REPLAN → CLOSING → RECORD.

LLM은 계획·상태 갱신·글쓰기만 하고, 무엇을 보낼지(30자·중복·진찰 문구·턴·시간)는 코드가 통제한다.
"""
import json
import os
import sys
import time
from pathlib import Path

from nova_sdk import NovaError

from .knowledge import CORE_EXAMS, CORE_HISTORY, EXAMS, exam_menu
from .llm import STOP_CODES, Doctor, with_retry
from .prompts import CLOSING_PROMPT, PLAN_PROMPT, QUESTION_RULES, REPLAN_PROMPT
from .record import write_record
from .visit import MAX_CHARS, Visit, nfc

TURN_BUDGET = int(os.getenv('NOVA_TURN_BUDGET', 40))  # 마무리 설명 포함 상한
CLOSING_TURNS = 6
MAX_REPLANS = 3
TIME_MARGIN = 240   # 마감 4분 전부터 마무리·기록으로
EMPATHY = '많이 불편하셨겠어요. '
REFUSALS = {'EXAM_NOT_SPECIFIC', 'EXAM_NOT_RECOGNIZED', 'CONTENT_TOO_LONG', 'EMPTY_CONTENT'}


def valid_question(text):
    text = nfc(text)
    return 0 < len(text) <= MAX_CHARS and text.count('?') <= 1


def covered(visit, keywords):
    return any(k in q for q in visit.asked for k in keywords)


class Consult:
    def __init__(self, client):
        self.client = client
        self.visit = Visit(client.session())
        self.doctor = Doctor(client)
        self.queue = []      # [('SAY', 문장) | ('EXAM', ID)]
        self.started = time.time()

    # ---------- 행동 ----------
    def has_time(self):
        return self.visit.seconds_left() > TIME_MARGIN

    def act(self, kind, content):
        """행동 하나. 계속 진료할 수 있으면 True."""
        try:
            call = self.client.say if kind == 'SAY' else self.client.exam
            reply = with_retry(call, content)
        except ValueError:
            return True  # SDK가 전송 전에 막음(턴 안 씀)
        except NovaError as exc:
            if exc.code in STOP_CODES:
                return False
            if exc.code in REFUSALS:  # 잘못 보낸 요청: 답은 없지만 1턴 사용
                self.visit.record('REFUSED', content, exc.code)
                self.resync()
            return True  # 재시도까지 실패한 일시 오류 등: 이 항목만 건너뛴다
        reply_text = reply.get('content') if kind == 'SAY' else reply.get('result')
        self.visit.record(kind, content, nfc(reply_text), reply.get('turns_remaining'))
        return True

    def resync(self):
        try:
            self.visit.turns_left = self.client.session()['turns_remaining']
        except (NovaError, KeyError):
            pass

    def say(self, text):
        text = nfc(text)
        if not valid_question(text) or text in self.visit.asked:
            return True
        if not self.visit.asked and len(EMPATHY + text) <= MAX_CHARS:
            text = EMPATHY + text
        self.visit.asked.add(nfc(text))
        return self.act('SAY', text)

    def exam(self, eid):
        if eid not in EXAMS or eid in self.visit.exams_done:
            return True
        self.visit.exams_done.add(eid)
        return self.act('EXAM', EXAMS[eid][1])

    # ---------- 계획 ----------
    def enqueue(self, plan):
        questions = [q for q in plan.get('questions') or [] if isinstance(q, str)]
        exams = [e for e in plan.get('exams') or [] if e in EXAMS]
        self.queue += [('SAY', q) for q in questions] + [('EXAM', e) for e in exams]

    def plan(self):
        plan = self.doctor.ask_json(PLAN_PROMPT.format(rules=QUESTION_RULES, menu=exam_menu()),
                                    self.visit.situation(), max_tokens=6000,
                                    check=lambda v: isinstance(v.get('questions'), list)) or {}
        self.visit.ddx = plan.get('ddx') or []
        self.enqueue({'questions': plan.get('questions')})
        self.queue += [('CORE', item) for item in CORE_HISTORY]
        self.enqueue({'exams': CORE_EXAMS + list(plan.get('exams') or [])})

    def replan(self):
        done = ', '.join(sorted(self.visit.exams_done)) or '(없음)'
        update = self.doctor.ask_json(REPLAN_PROMPT.format(rules=QUESTION_RULES, done=done, menu=exam_menu()),
                                      self.visit.situation() + f'\n\n현재 감별진단: {self.visit.ddx_text()}',
                                      max_tokens=6000, check=lambda v: 'ddx' in v or 'questions' in v)
        if not update:
            return False
        self.visit.ddx = update.get('ddx') or self.visit.ddx
        before = len(self.queue)
        self.enqueue(update)
        return len(self.queue) > before  # 추가할 것이 없으면(enough 포함) 마무리로

    # ---------- 단계 ----------
    def gather(self):
        limit = min(TURN_BUDGET, self.visit.total_turns) - CLOSING_TURNS
        replans = 0
        while self.visit.turn < limit and self.has_time():
            if not self.queue:
                if replans >= MAX_REPLANS or not self.replan():
                    return
                replans += 1
                continue
            kind, item = self.queue.pop(0)
            if kind == 'CORE':
                topic, question, keywords = item
                ok = True if covered(self.visit, keywords) else self.say(question)
            elif kind == 'SAY':
                ok = self.say(item)
            else:
                ok = self.exam(item)
            if not ok:
                return

    def closing(self):
        top = next((d.get('dx') for d in self.visit.ddx if isinstance(d, dict) and d.get('dx')), '')
        result = self.doctor.ask_json(CLOSING_PROMPT, self.visit.situation() + f'\n\n현재 감별진단: {self.visit.ddx_text()}',
                                      max_tokens=4000, check=lambda v: isinstance(v.get('lines'), list)) or {}
        diagnosis = nfc(result.get('diagnosis')) or top
        lines = [nfc(l) for l in result.get('lines') or [] if valid_question(l)]
        if not lines and diagnosis:
            lines = [l for l in (f'{diagnosis} 가능성이 높아 보여요.', '정확히 보려고 몇 가지 검사를 할게요.',
                                 '증상이 심해지면 바로 병원에 오세요.', '1~2주 뒤에 다시 뵐게요.') if len(l) <= MAX_CHARS]
        said = []
        for line in lines:
            if self.visit.turns_left <= 0 or self.visit.seconds_left() < 90:
                break
            before = len(self.visit.events)
            if not self.act('SAY', line):
                break
            if len(self.visit.events) > before and self.visit.events[-1]['kind'] == 'SAY':
                said.append(line)
        return said, diagnosis

    def run(self):
        self.plan()
        self.gather()
        said, diagnosis = self.closing()
        note, final = write_record(self.doctor, self.visit, said, diagnosis)
        self.save_log(note, final)
        return self.client.diagnose(note=note, diagnosis=final)

    def save_log(self, note, diagnosis):
        summary = {'turns': self.visit.turn, 'doctor_calls': self.doctor.calls, 'doctor_failures': self.doctor.failures,
                   'seconds': round(time.time() - self.started), 'ddx': self.visit.ddx, 'diagnosis': diagnosis}
        print('[agent] ' + json.dumps(summary, ensure_ascii=False), file=sys.stderr)
        out = os.getenv('NOVA_OUTPUT_DIR')
        if out:
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / 'agent_log.json').write_text(json.dumps(
                    {**summary, 'events': self.visit.events, 'note': note}, ensure_ascii=False, indent=1), encoding='utf-8')
            except OSError:
                pass


def consult(client, state):
    return Consult(client).run()
