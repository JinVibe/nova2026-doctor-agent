"""진료지침 코퍼스(NOVA_GUIDELINES_DIR) 검색: 제목 키워드 매칭 → 검사·치료·추적 절 발췌.

서버(CPU 2코어·8GB·인터넷 없음)에서 돌아야 하므로 임베딩 없이 catalog.csv 제목 검색만 쓴다.
코퍼스가 없으면(로컬 기본 환경 등) 빈 결과를 돌려주고 진료는 그대로 진행한다.
"""
import csv
import os
import re
from pathlib import Path

# catalog.csv 열 순서: 파일명, 제목, 발행기관, 연도, 언어, 문서 유형, 형식, 라이선스, 출처 URL (머리말 이름에 의존하지 않음)
COLS = ('file', 'title', 'org', 'year', 'lang', 'kind', 'fmt', 'license', 'url')

TYPE_WEIGHT = {'진료지침': 3.0, '합의문': 2.5, '권고문': 2.5, '근거 리뷰': 2.0, '질환 개요(교과서)': 2.0, '요약본': 1.5,
               '기타 (일반인용)': 1.2, '진료지침 (일반인용)': 1.2, '요약본 (일반인용)': 1.0, '기타': 0.5}
# 발췌할 절 제목(영/한). 평가·검사·치료·추적·권고.
SECTION_RE = re.compile(r'evaluation|diagnos|work-?up|treatment|management|therapy|follow|monitor|recommend|'
                        r'검사|진단|치료|추적|관리|권고|경과', re.I)
SKIP_TITLE_RE = re.compile(r'archived|pregnan|in children|pediatric|neonat|veterinar|orbit|surgery|surgical|anesthe', re.I)
MAX_DOC_CHARS = 2400
MAX_TOTAL_CHARS = 7500
DEFAULT_LIMIT = 4


def _root():
    env = os.getenv('NOVA_GUIDELINES_DIR')
    if env and Path(env).is_dir():
        return Path(env)
    local = Path(__file__).resolve().parent.parent / 'guidelines' / 'guidelines'  # 로컬 개발용 복사본
    return local if local.is_dir() else None


class Library:
    def __init__(self, root=None):
        self.root = Path(root) if root else _root()
        self.rows = []
        if self.root and (self.root / 'catalog.csv').exists():
            try:
                with open(self.root / 'catalog.csv', encoding='utf-8-sig', newline='') as f:
                    reader = csv.reader(f)
                    next(reader, None)
                    self.rows = [dict(zip(COLS, r)) for r in reader if len(r) >= 2]
            except (OSError, csv.Error):
                self.rows = []

    def available(self):
        return bool(self.rows)

    def search(self, keywords, limit=3, child=False):
        """키워드(영·한)를 제목에 대어 점수 매김. 종류별 가중치, 한국어 가산, 소아 여부에 따라 소아 문서 가감."""
        kws = [k.strip().lower() for k in keywords if isinstance(k, str) and len(k.strip()) >= 2]
        if not kws or not self.rows:
            return []
        scored = []
        for row in self.rows:
            title = row.get('title', '')
            low = title.lower()
            hits = [k for k in kws if k in low]
            if not hits:
                continue
            score = sum(len(k) for k in hits) / max(len(low), 1) * 6 + len(hits) * 2
            score += TYPE_WEIGHT.get(row.get('kind', ''), 1.0)
            score += 1.0 if row.get('lang') == '한국어' else 0
            if SKIP_TITLE_RE.search(title):
                score -= 4 if not (child and re.search(r'children|pediatric', title, re.I)) else -2
            if 'korea' in low:
                score += 0.5
            scored.append((score, row))
        scored.sort(key=lambda s: -s[0])
        picked, seen = [], set()
        for score, row in scored:
            key = row.get('title', '').lower()[:40]
            if key in seen:
                continue
            seen.add(key)
            picked.append(row)
            if len(picked) >= limit:
                break
        return picked

    def excerpt(self, row, max_chars=MAX_DOC_CHARS):
        """문서에서 검사·치료·추적 절만 골라 길이 제한 안에서 잇는다. 출처 줄을 앞에 붙인다."""
        path = self.root / row['file']
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return ''
        body = re.sub(r'^---.*?---\s*', '', text, count=1, flags=re.S)
        parts = re.split(r'(?m)^(#{1,5} .*)$', body)
        chunks = []  # (heading, content)
        for i in range(1, len(parts) - 1, 2):
            heading, content = parts[i].strip('# ').strip(), parts[i + 1].strip()
            if SECTION_RE.search(heading) and content:
                chunks.append((heading, content))
        if not chunks:  # 절 제목이 없으면 본문 앞부분
            chunks = [('본문', body.strip())]
        out, used = [], 0
        for heading, content in chunks:
            content = re.sub(r'\n{2,}', '\n', content)
            room = max_chars - used
            if room <= 200:
                break
            piece = content[:room]
            out.append(f'[{heading}]\n{piece}')
            used += len(piece) + len(heading) + 4
        src = f"출처: {row.get('title','')} — {row.get('org','')} {row.get('year','')} ({row.get('license','')}) {row.get('url','')}"
        return src + '\n' + '\n'.join(out)

    def lookup(self, keywords, child=False, limit=DEFAULT_LIMIT, total=MAX_TOTAL_CHARS):
        """키워드로 문서를 찾아 발췌문을 모아 돌려준다. 결과가 없으면 빈 문자열."""
        texts, used = [], 0
        for row in self.search(keywords, limit=limit, child=child):
            ex = self.excerpt(row, max_chars=min(MAX_DOC_CHARS, total - used))
            if ex:
                texts.append(ex)
                used += len(ex)
            if used >= total - 300:
                break
        return '\n\n'.join(texts)


_LIB = None


def library():
    """프로세스당 한 번만 catalog를 읽는다."""
    global _LIB
    if _LIB is None:
        _LIB = Library()
    return _LIB
