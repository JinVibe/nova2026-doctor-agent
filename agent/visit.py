"""한 증례의 진료 상태: 대화·진찰 기록, 남은 턴, 마감 시각.

서버의 session()은 지난 기록을 주지 않으므로 모든 기록은 여기서 들고 있는다.
"""
import time
import unicodedata
from datetime import datetime

MAX_CHARS = 30


def nfc(text):
    return unicodedata.normalize('NFC', str(text or '')).strip()


def format_vitals(vitals):
    if isinstance(vitals, dict):
        return ', '.join(f'{k} {v}' for k, v in vitals.items())
    return str(vitals or '')


class Visit:
    def __init__(self, session):
        initial = session['initial']
        self.opening = initial.get('opening', '')
        self.vitals = format_vitals(initial.get('vital_signs'))
        self.total_turns = session['turns_remaining']
        self.turns_left = session['turns_remaining']
        self.deadline = session.get('deadline')
        self.events = []        # {'turn', 'kind', 'sent', 'reply'}
        self.asked = set()      # 보낸 SAY (중복 방지)
        self.exams_done = set() # 보낸 진찰 ID
        self.ddx = []
        self.verify_log = {}    # S 검증 결과(기록용)

    @property
    def turn(self):
        return self.total_turns - self.turns_left

    def seconds_left(self):
        if not self.deadline:
            return float('inf')
        return datetime.fromisoformat(self.deadline).timestamp() - time.time()

    def record(self, kind, sent, reply, turns_remaining=None):
        self.turns_left = turns_remaining if turns_remaining is not None else self.turns_left - 1
        self.events.append({'turn': self.turn, 'kind': kind, 'sent': sent, 'reply': reply})

    def transcript(self):
        lines = []
        for e in self.events:
            t = f"[턴 {e['turn']}]"
            if e['kind'] == 'SAY':
                lines += [f"{t} 의사: {e['sent']}", f"{t} 환자: {e['reply']}"]
            elif e['kind'] == 'EXAM':
                lines.append(f"{t} 진찰({e['sent']}): {e['reply']}")
            else:
                lines.append(f"{t} 진찰 거절({e['sent']}): {e['reply']}")
        return '\n'.join(lines)

    def situation(self):
        return (f'첫 진술: {self.opening}\n활력징후: {self.vitals}\n\n'
                f"진료 기록\n{self.transcript() or '(아직 없음)'}")

    def exam_findings(self):
        """O.exam 칸: 활력징후 + 진찰 결과를 턴 번호와 함께(코드가 직접 작성)."""
        lines = [f'V/S : {self.vitals}'] if self.vitals else []
        lines += [f"[턴 {e['turn']}] {e['sent']} : {e['reply']}" for e in self.events if e['kind'] == 'EXAM']
        return '\n'.join(lines)

    def ddx_text(self):
        return ', '.join(f"{d.get('dx')}({d.get('p')})" for d in self.ddx if isinstance(d, dict)) or '(없음)'
