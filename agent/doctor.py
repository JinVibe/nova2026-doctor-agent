"""진료 흐름: PLAN → 문진 → REPLAN → 진찰 → REPLAN → CHALLENGE → REASSESS → CLOSING → RECORD.

LLM은 계획·상태 갱신·글쓰기만 하고, 무엇을 보낼지(30자·중복·진찰 문구·턴·시간)는 코드가 통제한다.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from nova_sdk import NovaError

from . import config
from .guidelines import library
from .knowledge import (CORE_EXAMS, EXAMS, ICE_HISTORY, OPEN_PROBES, MED_FOLLOWUPS, MED_FOLLOWUPS_V3, MED_NEGATIVE, MED_RISK_FOLLOWUPS, MED_RISK_WORDS,
                        exam_menu, history_items, is_child)
from .llm import STOP_CODES, Doctor, with_retry
from .prompts import (DX_RULES_V2, ANSWER_PROMPT, ANSWER_PROMPT_V2, CHALLENGER_PROMPT, CLOSING_PROMPT, CLOSING_PROMPT_V2, CLOSING_PROMPT_V3, CLOSING_PROMPT_V4T, CLOSING_PROMPT_V6, CLOSING_PROMPT_V7, CLOSING_PROMPT_V4, CLOSING_PROMPT_V5,
                      COVERAGE_RULE, DX_RULES, MED_RULES, PLAN_DEDUP_RULE, PLAN_PROMPT, PLAN_QRULE_V1, PLAN_QRULE_V2,
                      PLAN_FEATURES_FIELD, PLAN_FEATURES_RULE, PLAN_STRUCT_FIELDS, PLAN_STRUCT_RULE, OPEN_START_RULE, SUMMARY_PROMPT, PLAN_QRULE_V3, PLAN_QRULE_V4, QUESTION_RULES, REASSESS_PROMPT, REASSESS_PROMPT_V1, REPEAT_RULES, REPLAN_PROMPT, SHORTEN_PROMPT, THREAD_RULE)
from .record import write_record
from .visit import MAX_CHARS, Visit, nfc

TURN_BUDGET = int(os.getenv('NOVA_TURN_BUDGET', config.TURN_BUDGET or (50 if config.CLOSING_V3 else 48)))  # 마무리 설명 포함 상한
CLOSING_TURNS = 6 if (config.CLOSING_V5 or config.WARM) else 7 if config.CLOSING_V4 else 10 if config.CLOSING_V3 else 8
ANSWER_TURNS = 1 if config.TURN_BUDGET and config.TURN_BUDGET <= 32 else 2    # 마무리 설명 뒤 환자 질문에 답하는 턴
MAX_REPLANS = 4 if config.EARLY_REPLAN else 3
TIME_MARGIN = 240   # 마감 4분 전부터 마무리·기록으로
EMPATHY = '많이 불편하셨겠어요. '
REFUSALS = {'EXAM_NOT_SPECIFIC', 'EXAM_NOT_RECOGNIZED', 'CONTENT_TOO_LONG', 'EMPTY_CONTENT'}
DX_RULES = DX_RULES_V2 if config.DX_RULES_V2 else DX_RULES  # noqa: F811  (플래그로 진단 원칙 v2)
MAX_EXAMS = (7 if config.EXAM_RICH else 4 if config.TURN_BUDGET and config.TURN_BUDGET <= 32 else 5 if config.TURN_BUDGET and config.TURN_BUDGET <= 34 else 6 if config.TURN_BUDGET and config.TURN_BUDGET <= 40 else 7) if config.EXAM_TRIM else 99   # 공통 3 + 계획 ≤4(짧은 진료는 ≤3)


SYMPTOM_NAMES = ('저신장', '불면', '불면증', '체중 감소', '체중감소', '두통', '복통', '발열', '어지럼', '어지러움', '피로', '기침', '설사', '구토', '부종', '호흡곤란', '흉통')
WORRY_WORDS = ('걱정', '불안', '무서', '두려', '힘들어', '힘드', '겁이')
WORRY_ACK = '걱정되시죠. '


def valid_question(text):
    text = nfc(text)
    return 0 < len(text) <= MAX_CHARS and text.count('?') <= 1


COMPOUND_RE = re.compile(r'·|,|이나 |거나 |또는 |및 |와 |과 |랑 ')


def is_compound(text):
    """여러 항목을 한 번에 묻는 질문(환자가 하나만 답하거나 '하나씩 물어 달라'고 거절함)."""
    return '?' in text and len(COMPOUND_RE.findall(text)) >= 1


def covered(visit, keywords):
    return any(k in q for q in visit.asked for k in keywords)


class Consult:
    def __init__(self, client):
        self.client = client
        self.visit = Visit(with_retry(client.session))
        self.doctor = Doctor(client)
        self.queue = []      # [('SAY', 문장) | ('EXAM', ID) | ('CORE', 항목) | ('REPLAN', None)]
        self.started = time.time()
        self.review = {}
        self.keywords = []   # 지침 검색어(REASSESS가 냄)
        self.references = ''  # 기록에 넣은 지침 발췌
        self.rejected = []    # 복합 질문이라 보내지 않은 질문(다음 재계획에 알려 하나씩 나눠 쓰게 함)
        self.summarized = False  # 진찰 전 요약 확인을 한 번만(COVERAGE_PLAN)
        self.dx_votes = []       # DX_VOTE 재평가 결과들(로그용)
        self.worry_acked = False  # 걱정 반응 접두는 한 번만(TALK_V3)
        self.log_dir = os.getenv('NOVA_OUTPUT_DIR')  # 로컬 로그 위치(서버에선 미설정)

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
            else:
                print(f'[agent] {kind} skipped: {exc.code}', file=sys.stderr)  # 조용히 건너뛰던 오류를 로그에 남김(로컬 시뮬레이터에서 sol 한도 초과를 못 봤음)
            return True  # 재시도까지 실패한 일시 오류 등: 이 항목만 건너뛴다
        reply_text = reply.get('content') if kind == 'SAY' else reply.get('result')
        self.visit.record(kind, content, nfc(reply_text), reply.get('turns_remaining'))
        return True

    def resync(self):
        try:
            self.visit.turns_left = with_retry(self.client.session)['turns_remaining']
        except (NovaError, KeyError):
            pass

    def say(self, text, check_compound=True):
        text = nfc(text)
        if not valid_question(text) or text in self.visit.asked:
            return True
        if check_compound and is_compound(text):
            self.rejected.append(text)
            return True
        if not self.visit.asked and len(EMPATHY + text) <= MAX_CHARS:
            text = EMPATHY + text
        elif text.endswith('?'):
            last = self.visit.events[-1] if self.visit.events else {}
            reply = last.get('reply') or ''
            acked = False
            if config.TALK_V3 and last.get('kind') == 'SAY' and any(w in reply for w in WORRY_WORDS) and len(WORRY_ACK + text) <= MAX_CHARS \
                    and not self.worry_acked:
                self.worry_acked = True  # 한 진료에 한 번만(연속 "걱정되시죠"는 기계적으로 보임)
                text = WORRY_ACK + text
                acked = True
            if config.ACK and not acked and last.get('kind') == 'SAY' and reply:  # v2.4j: TALK_V3와 함께 쓸 수 있게(전에는 elif라 ACK가 죽어 있었음)
                ack = (('네. ', '알겠습니다. ') if any(n in reply for n in ('아니요', '아뇨', '없어요', '없습니다')) else ('그렇군요. ', '네, 알겠습니다. '))[len(self.visit.asked) % 2]  # 부정 답에도 두 가지를 번갈아(4j C03에서 "네." 6연속)
                if len(ack + text) <= MAX_CHARS:
                    text = ack + text
        self.visit.asked.add(nfc(text))
        return self.act('SAY', text)

    def exam(self, eid):
        if eid not in EXAMS or eid in self.visit.exams_done or len(self.visit.exams_done) >= MAX_EXAMS:
            return True
        self.visit.exams_done.add(eid)
        return self.act('EXAM', EXAMS[eid][1])

    # ---------- 계획 ----------
    def enqueue(self, plan, front=False, max_exams=None):
        questions = [q for q in plan.get('questions') or [] if isinstance(q, str)]
        exams = [e for e in plan.get('exams') or [] if e in EXAMS]
        if max_exams is not None:
            exams = exams[:max_exams]
        items = [('SAY', q) for q in questions] + [('EXAM', e) for e in exams]
        self.queue = items + self.queue if front else self.queue + items
        return len(items)

    def plan(self):
        prompt = PLAN_PROMPT.format(dx=DX_RULES, rules=QUESTION_RULES, menu=exam_menu(),
                                    qrule=PLAN_QRULE_V4 if config.PLAN_Q_V4 else PLAN_QRULE_V3 if config.THREAD
                                    else PLAN_QRULE_V2 if config.HISTORY_V2 else PLAN_QRULE_V1,
                                    dedup=PLAN_DEDUP_RULE if config.REPEAT_GUARD else '')
        if config.EXAM_RICH:
            prompt = prompt.replace('진찰 ID 5~10개', '진찰 ID 3~4개(감별진단마다 양성·음성을 가르는 표적 진찰: 피부·갑상선·눈·신경·정신상태·기립성 혈압·맥박·반사·떨림·복부 등; 외관·심음·폐는 따로 하므로 제외)')
        elif config.EXAM_TRIM:
            prompt = prompt.replace('진찰 ID 5~10개', '진찰 ID 3~4개(감별을 직접 가르는 것만)')
        if config.PLAN_STRUCT:
            prompt = prompt.replace(' "questions": ["환자에게 할 질문"],\n', ' ' + PLAN_STRUCT_FIELDS)
            prompt = prompt.replace('- exams: 아래 목록에서', PLAN_STRUCT_RULE + '\n- exams: 아래 목록에서')
            assert PLAN_STRUCT_FIELDS in prompt and PLAN_STRUCT_RULE in prompt
        if config.OPEN_START:
            prompt = prompt.replace('- exams: 아래 목록에서', OPEN_START_RULE + chr(10) + '- exams: 아래 목록에서')
        if config.PLAN_FEATURES:
            prompt = prompt.replace(' "questions": ["환자에게 할 질문"]', ' ' + PLAN_FEATURES_FIELD + '"questions": ["환자에게 할 질문"]')
            prompt = prompt.replace('- exams: 아래 목록에서', PLAN_FEATURES_RULE + '\n- exams: 아래 목록에서')
            assert PLAN_FEATURES_FIELD in prompt and PLAN_FEATURES_RULE in prompt
        plan = self.doctor.ask_json(prompt,
                                    self.visit.situation(), max_tokens=6000,
                                    check=lambda v: isinstance(v.get('questions'), list) or (
                                        config.PLAN_STRUCT and any(isinstance(v.get(k), list) for k in ('hpi', 'red_flags', 'discriminators', 'ros')))) or {}
        self.visit.ddx = plan.get('ddx') or []
        if config.PLAN_STRUCT and not plan.get('questions'):
            # OSCE 순서: 현병력 → 위험 신호 → 감별 변별 → 계통 음성 확인. 칸별 상한으로 구성을 보장하고 합계 10.
            caps = tuple(zip(('hpi', 'red_flags', 'discriminators', 'ros'), config.PLAN_STRUCT_CAPS))
            qs = []
            for key, cap in caps:
                qs += [q for q in (plan.get(key) or []) if isinstance(q, str)][:cap]
            plan['questions'] = qs[:10]
            print(f"[agent] plan struct: " + ' '.join(f"{k}={len([q for q in (plan.get(k) or []) if isinstance(q, str)][:c])}" for k, c in caps), file=sys.stderr)
        if config.THREAD:
            plan['questions'] = (plan.get('questions') or [])[:6 if TURN_BUDGET <= 40 else 7]  # 선별 질문이 공통 문진·재계획 턴을 먹지 않게
        if config.ICE_V2 or config.OPEN_PROBE:
            cap = (10 if config.ICE_V2 else 12) - config.OPEN_PROBE
            plan['questions'] = (plan.get('questions') or [])[:cap]  # 모델이 우선순위로 적은 목록의 꼬리만 양보(11이면 재계획 질문 자리가 0이 됨)
        self.enqueue({'questions': plan.get('questions')})
        if config.OPEN_PROBE:
            self.queue += [('CORE', item) for item in OPEN_PROBES[:config.OPEN_PROBE]]
        if config.EARLY_REPLAN:
            self.queue.append(('REPLAN', None))  # 계획 질문의 답(단서)을 보고 바로 뒤쫓는 질문을 앞에 끼움
        if config.ICE_V2 and not config.PLAN_STRUCT:
            self.queue += [('CORE', item) for item in ICE_HISTORY[:2]]  # 일상 지장·가장 걱정되는 점(정보 범주 2개를 코드가 보장)
        if config.ICE:
            self.queue += [('CORE', item) for item in ICE_HISTORY]  # 환자 관점·기능 영향(공통 문진처럼 코드가 보장)
        self.queue += [('CORE', item) for item in history_items(self.visit.opening)]
        if config.ICE_V2 and config.PLAN_STRUCT:
            self.queue += [('CORE', item) for item in ICE_HISTORY[:2]]  # v2.5a: OSCE 순서대로 사회력 뒤에 ICE
        self.queue.append(('REPLAN', None))  # 병력 답(약 용량 등)을 보고 진찰 전에 한 번 보완
        core = CORE_EXAMS + (['MENTAL', 'ABD_PALP'] if config.CORE_EXAMS_FULL else ['ABD_PALP'] if config.CORE_ABD else [])
        if config.EXAM_RICH:
            core = list(CORE_EXAMS)  # 외관·심음·폐 공통(기침 증례에서 계획이 폐 청진을 빠뜨림, 10/19 로컬 S02) + 표적 4
        elif MAX_EXAMS <= 4:
            core = CORE_EXAMS[:1]  # 30턴: 외관만 공통, 나머지 3개는 감별을 가르는 계획 진찰(C10 안징후·C03 성장속도가 심음·폐에 밀렸음)
        self.enqueue({'exams': core + list(plan.get('exams') or [])}, max_exams=(3 + 4) if config.EXAM_RICH else (3 + 4) if config.EXAM_TRIM else None)

    def replan(self, front=False):
        done = ', '.join(sorted(self.visit.exams_done)) or '(없음)'
        guard = config.REPEAT_GUARD
        cov = config.COVERAGE_PLAN
        prompt = REPLAN_PROMPT.format(med=MED_RULES, dx=DX_RULES, rules=QUESTION_RULES, done=done, menu=exam_menu(),
                                      known='"known": ["이미 확인된 사실"], ' if guard else '', repeat=REPEAT_RULES if guard else '',
                                      coverage=COVERAGE_RULE if cov else THREAD_RULE if config.THREAD else '',
                                      coverage_fields=' "coverage": {{"covered": ["차원 번호"], "missing": ["차원 번호"]}}, "summary": "확인 요약 한 문장",' if cov
                                      else ' "summary": "지금까지 들은 핵심 사실 1~2개를 환자에게 확인하는 한 문장(28자 이내, \\"~로 이해했는데 맞나요?\\"로 끝남; 이미 확인했으면 빈 문자열)",'
                                      if (config.SUMMARY_CONFIRM and not self.summarized) else '')
        user = self.visit.situation() + f'\n\n현재 감별진단: {self.visit.ddx_text()}'
        if self.rejected:
            user += ('\n\n보내지 않은 질문(한 번에 여러 항목을 물어서 제외됨 — 필요하면 항목 하나씩 따로 쓰세요): '
                     + ' / '.join(self.rejected[-6:]))
            self.rejected = []
        update = self.doctor.ask_json(prompt, user, max_tokens=6000, check=lambda v: 'ddx' in v or 'questions' in v)
        if not update:
            return False
        self.visit.ddx = update.get('ddx') or self.visit.ddx
        added = self.enqueue(update, front, max_exams=2 if config.EXAM_TRIM else None)
        summary = nfc(update.get('summary') or '') if (config.COVERAGE_PLAN or config.SUMMARY_CONFIRM) else ''
        if summary and valid_question(summary) and not self.summarized:  # 요약문은 질문이 아니라 복합 검사를 거치지 않음
            self.summarized = True
            if config.SUMMARY_CONFIRM:
                # 큐 앞에 넣으면 진찰 턴 예약이 진찰을 먼저 빼내 끝까지 안 나감(10/19 로컬 L5d 0/5) → 재계획 직후 바로 말한다
                print(f'[agent] summary confirm: {summary}', file=sys.stderr)
                self.say(summary, check_compound=False)
            else:
                self.queue.insert(0, ('SAY', summary))  # 진찰로 넘어가기 전 요약 확인(의사소통: 이해 확인)
            added += 1
        return added > 0  # 추가할 것이 없으면(enough 포함) 마무리로

    # ---------- 단계 ----------
    def gather(self, allow_replan=True):
        limit = min(TURN_BUDGET, self.visit.total_turns) - CLOSING_TURNS - ANSWER_TURNS
        replans = 0
        while self.visit.turn < limit and self.has_time():
            if not self.queue:
                if not allow_replan or replans >= MAX_REPLANS or not self.replan():
                    return
                replans += 1
                continue
            # 진찰 턴 예약: 남은 턴이 대기 중인 진찰 수 이하이면 질문을 미루고 진찰부터 한다(v1.6에서 문진이 늘어 진찰이 1회만 된 적 있음)
            pending_exams = [i for i, (k, e) in enumerate(self.queue) if k == 'EXAM' and e not in self.visit.exams_done]
            if (config.THREAD or config.MEDCORE) and self.queue[0][0] == 'SAY':
                # 공통 문진 턴 예약: 남은 턴이 (대기 진찰 + 아직 안 물은 공통 문진) 이하면 계획·재계획 질문을 버린다(가족력·음주·직업이 통째로 빠진 38턴 C01)
                compound_check = not (config.HISTORY_V2 or config.CORE_FIX or config.THREAD)
                pending_core = [i for i, (k, it) in enumerate(self.queue) if k == 'CORE' and not covered(self.visit, it[2])
                                and not (compound_check and is_compound(it[1]))]  # 복합 문구라 결국 안 보낼 항목은 예약에서 빼서 계획 질문을 과하게 버리지 않게
                if limit - self.visit.turn <= len(pending_exams) + len(pending_core):
                    self.queue.pop(0)
                    continue
            reserve = len(pending_exams) + (1 if config.SUMMARY_CONFIRM and not self.summarized else 0)  # 요약·확인 1턴도 예약
            if (config.HISTORY_V2 or config.EXAM_RESERVE) and pending_exams and limit - self.visit.turn <= reserve and self.queue[0][0] != 'EXAM':
                if config.SUMMARY_CONFIRM and not self.summarized:
                    self.summarize_confirm()  # 진찰로 넘어가는 경계에서 1회(EXAM_RICH에서는 재계획이 이 지점에 못 오므로 여기서 직접)
                    continue
                kind, item = self.queue.pop(pending_exams[0])
            else:
                kind, item = self.queue.pop(0)
            if kind == 'REPLAN':
                if allow_replan and replans < MAX_REPLANS:
                    replans += 1
                    self.replan(front=True)
                continue
            if kind == 'CORE':
                topic, question, keywords = item
                # v1.6 문구는 단일 항목이라 차단 없이 보냄. v1.2 문구는 v1.2와 똑같이 차단을 거침(복합 문구 4개는 보내지지 않음)
                ok = True if covered(self.visit, keywords) else self.say(question, check_compound=not (config.HISTORY_V2 or config.CORE_FIX))
                if topic.startswith('open') and ok:
                    last = self.visit.events[-1] if self.visit.events else {}
                    if last.get('kind') == 'SAY' and any(n in (last.get('reply') or '') for n in ('없어요', '없습니다', '그게 다', '아니요', '아뇨')):
                        self.queue = [(k, it) for k, it in self.queue if not (k == 'CORE' and it[0].startswith('open'))]  # "없어요"면 나머지 열린 질문은 버림
                if topic == 'meds' and ok and self.took_medication():
                    # 약이 있다고 하면 이름·용량·횟수·기간을 하나씩 바로 묻는다(복합 질문은 환자가 거절함)
                    followups = list(MED_FOLLOWUPS_V3 if (config.THREAD or config.MEDCORE) else MED_FOLLOWUPS)
                    if config.MED_RISK and self.risky_medication():
                        head = 3 if (config.THREAD or config.MEDCORE) else 2  # 이름·(mg)·알 수 다음에 증량·초과·금단을 먼저
                        followups = followups[:head] + MED_RISK_FOLLOWUPS + followups[head:]
                    pending = [(q, kws) for q, kws in followups if not covered(self.visit, kws)]
                    if config.THREAD or config.MEDCORE:  # 약 후속은 공통 문진처럼 보호(턴 예약에서 계획 질문보다 먼저 지켜짐)
                        self.queue = [('CORE', ('medfu', q, kws)) for q, kws in pending] + self.queue
                    else:
                        self.queue = [('SAY', q) for q, kws in pending] + self.queue
            elif kind == 'SAY':
                ok = self.say(item, check_compound=not ((config.COVERAGE_PLAN or config.SUMMARY_CONFIRM) and item.endswith('맞나요?')))
            else:
                ok = self.exam(item)
            if not ok:
                return

    def summarize_confirm(self):
        """문진 → 진찰 경계에서 핵심 사실 1~2개를 환자에게 확인하는 한 문장(SUMMARY_CONFIRM, 1턴)."""
        self.summarized = True
        result = self.doctor.ask_json(SUMMARY_PROMPT, self.visit.situation(), max_tokens=1500,
                                      check=lambda v: isinstance(v.get('line'), str)) or {}
        line = nfc(result.get('line') or '')
        if line and valid_question(line):
            print(f'[agent] summary confirm: {line}', file=sys.stderr)
            self.say(line, check_compound=False)

    def risky_medication(self):
        """첫 진술이나 직전 답에 의존성 위험 약이 언급됐는지."""
        last = self.visit.events[-1] if self.visit.events else {}
        text = (last.get('reply') or '') + ' ' + self.visit.opening
        return any(w in text for w in MED_RISK_WORDS)

    def took_medication(self):
        """직전 SAY(복용 약 질문)의 답이 '약이 있다'인지."""
        last = self.visit.events[-1] if self.visit.events else {}
        reply = last.get('reply') or ''
        return last.get('kind') == 'SAY' and bool(reply) and not any(n in reply for n in MED_NEGATIVE)

    def challenge(self):
        """반대 의견(악마의 변호인): 1위 진단의 모순·미배제 위험 진단을 점검하고, 판가름 질문이 있으면 남은 턴에 묻는다."""
        review = self.doctor.ask_json(CHALLENGER_PROMPT.format(dx=DX_RULES, menu=exam_menu()),
                                      self.visit.situation() + f'\n\n현재 감별진단: {self.visit.ddx_text()}',
                                      max_tokens=6000, check=lambda v: 'alternative' in v or 'questions' in v) or {}
        self.queue = []
        self.enqueue({'questions': (review.get('questions') or [])[:3], 'exams': (review.get('exams') or [])[:2]})
        self.gather(allow_replan=False)
        return review

    def reassess_once(self, review):
        top = next((d.get('dx') for d in self.visit.ddx if isinstance(d, dict) and d.get('dx')), '')
        opinion = json.dumps({k: v for k, v in review.items() if k not in ('questions', 'exams')}, ensure_ascii=False)
        return self.doctor.ask_json((REASSESS_PROMPT if config.DX_CANDIDATES else REASSESS_PROMPT_V1).format(dx=DX_RULES),
                                    self.visit.situation() + f'\n\n진료 중 감별진단: {self.visit.ddx_text()}'
                                    f'\n\n검토 의견(반대 의견): {opinion}',
                                    max_tokens=6000, check=lambda v: v.get('diagnosis')) or {}

    @staticmethod
    def same_dx(a, b):
        a, b = (re.sub(r'[\s·,()（）]', '', nfc(x)) for x in (a, b))
        return bool(a) and bool(b) and (a in b or b in a)

    def reassess_vote(self, review):
        """재평가를 N회 돌려 주진단 다수결(DX_VOTE). 같은 진단을 포함 관계로 묶고, 동률이면 첫 결과를 따른다."""
        # 비용 파트가 호출 수에 민감(공식 ① 효율 −0.62) → 앞 두 결과가 같으면 세 번째는 생략(기대 호출 ≈ 2.1)
        results = []
        for i in range(max(1, config.DX_VOTE)):
            r = self.reassess_once(review)
            if r.get('diagnosis'):
                results.append(r)
            if i >= 1 and len(results) >= 2 and self.same_dx(results[0]['diagnosis'], results[1]['diagnosis']):
                break
        if not results:
            return {}
        groups = []  # [(대표 결과, 표 수)]
        for r in results:
            for g in groups:
                if self.same_dx(g[0]['diagnosis'], r['diagnosis']):
                    g[1] += 1
                    break
            else:
                groups.append([r, 1])
        best = max(groups, key=lambda g: g[1])  # max는 첫 최대값을 고르므로 동률이면 먼저 나온 진단
        self.dx_votes = [nfc(r['diagnosis']) for r in results]
        print(f'[agent] dx vote {self.dx_votes} -> {best[0]["diagnosis"]!r} ({best[1]}/{len(results)})', file=sys.stderr)
        return best[0]

    def reassess(self, review):
        """진료 전체를 다시 읽고 최종 감별진단·주진단을 정한다(후반에 나온 단서·반대 의견 반영)."""
        top = next((d.get('dx') for d in self.visit.ddx if isinstance(d, dict) and d.get('dx')), '')
        result = self.reassess_vote(review) if config.DX_VOTE else self.reassess_once(review)
        if isinstance(result.get('ddx'), list) and result['ddx']:
            self.visit.ddx = result['ddx']
        self.keywords = [k for k in result.get('keywords') or [] if isinstance(k, str)][:6]
        diagnosis = nfc(result.get('diagnosis')) or top
        if diagnosis.strip() in SYMPTOM_NAMES:  # 증상명이 주진단으로 나오면(C03 "저신장") 감별진단에서 첫 원인 진단으로 바꾼다
            cause = next((nfc(d['dx']) for d in self.visit.ddx if isinstance(d, dict) and d.get('dx')
                          and nfc(d['dx']).strip() not in SYMPTOM_NAMES), '')
            if cause:
                print(f'[agent] diagnosis {diagnosis!r} is a symptom name -> {cause!r}', file=sys.stderr)
                diagnosis = cause
        return diagnosis

    def consult_guidelines(self, diagnosis):
        """지침 코퍼스에서 주진단 관련 검사·치료·추적 절을 찾아 기록 작성에 넘긴다(없으면 빈 문자열)."""
        if not config.USE_GUIDELINES:
            return ''
        try:
            lib = library()
            if not lib.available():
                return ''
            return lib.lookup(self.keywords or [diagnosis], child=is_child(self.visit.opening))
        except Exception as exc:  # 지침 검색 실패가 진료를 멈추면 안 된다
            print(f'[agent] guidelines lookup failed: {exc!r}', file=sys.stderr)
            return ''

    def closing(self, diagnosis):
        prompt = CLOSING_PROMPT_V7 if (config.WARM and config.TESTS_SAY) else CLOSING_PROMPT_V6 if config.WARM else CLOSING_PROMPT_V5 if config.CLOSING_V5 else (CLOSING_PROMPT_V4T if config.TESTS_SAY else CLOSING_PROMPT_V4) if config.CLOSING_V4 else CLOSING_PROMPT_V3 if config.CLOSING_V3 else CLOSING_PROMPT_V2 if config.TALK_V2 else CLOSING_PROMPT
        result = self.doctor.ask_json(prompt, self.visit.situation()
                                      + f'\n\n감별진단: {self.visit.ddx_text()}\n주진단: {diagnosis}',
                                      max_tokens=4000, check=lambda v: isinstance(v.get('lines'), list)) or {}
        raw = [nfc(l) for l in result.get('lines') or [] if isinstance(l, str) and l.strip()]
        lines = [l for l in raw if valid_question(l)]
        if (config.CLOSING_V4 or config.CLOSING_V5 or config.WARM) and len(lines) < len(raw):
            lines = self.shorten(raw)  # 30자를 넘어 빠질 줄(응급 경고·추적 시점)을 줄여서 살린다(C03에서 7줄 중 3줄이 빠짐)
        if (config.CLOSING_V3 or config.CLOSING_V4 or config.CLOSING_V5) and lines and not any(k in l for l in lines for k in ('뒤', '후에', '다시 오', '뵙', '재진', '내일', '오늘', '마다', '다음 주', '매주', '매달')):
            # 결과 확인 시점 줄이 30자를 넘어 빠진 경우(C03에서 발생) 기본 문구를 마지막 질문 줄 앞에 넣는다
            lines.insert(len(lines) - 1 if lines[-1].endswith('?') else len(lines), '1~2주 뒤 검사 결과 보러 다시 오세요.')
        if not lines and diagnosis:
            lines = [l for l in (f'{diagnosis} 가능성이 높아 보여요.', '정확히 보려고 몇 가지 검사를 할게요.',
                                 '증상이 심해지면 바로 병원에 오세요.', '1~2주 뒤에 다시 뵐게요.') if len(l) <= MAX_CHARS]
        if config.TALK_V3:
            return self.speak_and_answer(lines, diagnosis)
        said = self.speak(lines)
        for _ in range(ANSWER_TURNS):  # 환자가 되물으면 짧게 답한다
            last = self.visit.events[-1] if self.visit.events else {}
            if last.get('kind') != 'SAY' or '?' not in (last.get('reply') or ''):
                break
            said += self.answer(diagnosis)
        return said

    def shorten(self, raw):
        """마무리 줄 중 30자를 넘는 것만 모델에 줄여 달라고 한 번 요청해 제자리에 넣는다."""
        long = [l for l in raw if not valid_question(l)]
        fix = self.doctor.ask_json(SHORTEN_PROMPT, '\n'.join(long), max_tokens=1500,
                                   check=lambda v: isinstance(v.get('lines'), list)) or {}
        fixed = iter([nfc(l) for l in fix.get('lines') or [] if isinstance(l, str)])
        out = []
        for l in raw:
            if valid_question(l):
                out.append(l)
                continue
            short = next(fixed, '')
            if valid_question(short):
                out.append(short)
        return out

    def speak_and_answer(self, lines, diagnosis):
        """마무리 설명을 한 줄씩 말하되, 환자가 되물으면 그 자리에서 바로 답한다(최대 ANSWER_TURNS회)."""
        said, used = [], 0  # used = 되묻기에 쓴 턴 수(합계 ANSWER_TURNS 안에서, 중간 되묻기는 한 줄로)
        for i, line in enumerate(lines):
            got = self.speak([line])
            if not got and (self.visit.turns_left <= 0 or self.visit.seconds_left() < 90):
                break
            said += got
            last = self.visit.events[-1] if self.visit.events else {}
            if got and used < ANSWER_TURNS and last.get('kind') == 'SAY' and '?' in (last.get('reply') or ''):
                got = self.answer(diagnosis, max_lines=1 if i < len(lines) - 1 else ANSWER_TURNS - used)
                said += got
                used += max(1, len(got))
        return said

    def answer(self, diagnosis, max_lines=2):
        """환자의 되물음에 1~2문장으로 답한다(v2). 답이 없거나 길면 기본 답."""
        user = self.visit.situation() + f'\n\n주진단: {diagnosis}'
        if config.TALK_V2 or config.CLOSING_V3 or config.CLOSING_V4 or config.CLOSING_V5:
            result = self.doctor.ask_json(ANSWER_PROMPT_V2, user, max_tokens=3000,
                                          check=lambda v: isinstance(v.get('lines'), list) and any(valid_question(l) for l in v['lines'])) or {}
            lines = [nfc(l) for l in result.get('lines') or [] if valid_question(l)][:max_lines]
        else:
            result = self.doctor.ask_json(ANSWER_PROMPT, user, max_tokens=3000, check=lambda v: valid_question(v.get('line'))) or {}
            lines = [nfc(result.get('line'))] if result.get('line') else []
        fallback = '지금 말씀드린 계획대로 하면 좋아질 수 있어요.' if config.CLOSING_V3 or config.CLOSING_V4 or config.CLOSING_V5 else '검사 결과를 보고 자세히 설명드릴게요.'
        return self.speak(lines or [fallback])

    def speak(self, lines):
        """설명 문장들을 차례로 말하고 실제로 전달된 문장을 돌려준다."""
        said = []
        for line in lines:
            if self.visit.turns_left <= 0 or self.visit.seconds_left() < 90:
                break
            before = len(self.visit.events)
            if not valid_question(line) or line in self.visit.asked:
                continue
            self.visit.asked.add(line)
            if not self.act('SAY', line):  # 설명문은 복합 검사 없이 보낸다
                break
            if len(self.visit.events) > before and self.visit.events[-1]['kind'] == 'SAY':
                said.append(line)
        return said

    def greet(self):
        """첫 턴 인사(WARM): 이름(또는 보호자 호칭)을 부르고 공감 한 마디. 마무리의 공감 줄을 여기로 옮겨 턴 합계는 같다."""
        opening = self.visit.opening
        if is_child(opening):
            who = '어머님' if any(w in opening for w in ('엄마', '어머니')) else '아버님' if any(w in opening for w in ('아빠', '아버지')) else '보호자님'
            text = f'안녕하세요, {who}. 걱정 많으셨겠어요.'
        else:
            m = re.search(r'(?:저는|이름은)\s*([가-힣]{2,4})(?=이고|이라고|입니다|이다|예요|이에요|이며|,)', opening)
            text = f'안녕하세요, {m.group(1)} 님. 많이 힘드셨겠어요.' if m else '안녕하세요. 많이 힘드셨겠어요.'
        if valid_question(text):
            self.visit.asked.add(nfc(text))  # say()의 첫 턴 공감 접두·복합 문장 차단(쉼표)을 거치지 않고 보낸다
            self.act('SAY', text)

    def run(self):
        if config.WARM:
            self.greet()
        self.plan()
        self.gather()
        self.review = self.challenge()
        diagnosis = self.reassess(self.review)
        said = self.closing(diagnosis)
        self.references = self.consult_guidelines(diagnosis)
        note, final = write_record(self.doctor, self.visit, said, diagnosis, self.references)
        self.save_log(note, final)
        return self.client.diagnose(note=note, diagnosis=final)

    def save_log(self, note, diagnosis):
        summary = {'turns': self.visit.turn, 'doctor_calls': self.doctor.calls, 'doctor_failures': self.doctor.failures,
                   'doctor_usage': self.doctor.usage,
                   'seconds': round(time.time() - self.started), 'ddx': self.visit.ddx, 'diagnosis': diagnosis,
                   'keywords': self.keywords, 'reference_chars': len(self.references), 'dx_votes': self.dx_votes}
        print('[agent] ' + json.dumps(summary, ensure_ascii=False), file=sys.stderr)
        out = self.log_dir
        if out:
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / 'agent_log.json').write_text(json.dumps(
                    {**summary, 'opening': self.visit.opening, 'vitals': self.visit.vitals,
                     'review': self.review, 'verify': self.visit.verify_log, 'references': self.references,
                     'events': self.visit.events, 'note': note},
                    ensure_ascii=False, indent=1), encoding='utf-8')
            except OSError:
                pass


def consult(client, state):
    return Consult(client).run()
