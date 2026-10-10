# nova2026-doctor-agent

[N.O.V.A. 2026](https://nova.snubhai.org/) (분당서울대병원 의료인공지능센터 × 엘리스) 참가용 Doctor Agent.

가상 환자를 문진·진찰한 뒤 **S·O·A·P 초진 기록 + 주진단 1개**를 제출하는 에이전트를 만든다.

## 대회 요약

| 단계 | 기간 | 모델 | 행동 |
|---|---|---|---|
| 예선 | 10.12 – 10.22 | `openai/gpt-oss-20b` 고정 (엘리스 API) | SAY · EXAM · DIAGNOSE |
| 본선 | 10.27 – 11.10 | 자유 (파인튜닝 가능) | SAY · EXAM · TEST · DIAGNOSE |

- 증례당 최대 50턴 · 20분, 증례마다 대회 모델 1회 이상 호출 필수
- SAY는 30자 이하, 한 번에 질문 하나 / EXAM·TEST는 한 번에 하나
- 예선 채점(100점): 정보수집 25 · 효율 20 · 진단추론 15 · 안전 15 · 의사소통 15 · 검사·추적 10
- 제출: 루트에 `run.py` + `requirements.txt`가 있는 ZIP(50MB 이하), Python 3.12, 인터넷 차단 환경

## 설계 방향 (예선)

Plan → Execute → Replan 구조로 LLM 호출을 증례당 3~5회로 제한한다.

1. **PLAN** (LLM): 첫마디·활력징후로 감별진단과 구별용 질문/진찰 계획 생성 — DDO, MedKGI
2. **EXECUTE** (코드): 계획을 한 턴씩 실행, `[턴, 행동, 응답]`을 로그로 저장
3. **REPLAN** (LLM, 필요 시): 감별진단 갱신 및 확신도 기반 종료 판단 — MediQ
4. **설명·교육** (SAY 1~2턴): Calgary-Cambridge 마무리 단계 — MedExAgent
5. **SOAP 생성** (LLM): 로그에 있는 사실만 사용, O에는 턴 번호 표기

## 로컬 개발

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

ollama pull gpt-oss:20b
.\.venv\Scripts\python.exe smoke_test.py          # low, medium 비교
```

엔드포인트는 환경변수로 바꾼다 (기본값은 로컬 Ollama).

| 변수 | 기본값 |
|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` |
| `LLM_API_KEY` | `ollama` |
| `LLM_MODEL` | `gpt-oss:20b` |

> API 키·비공개 증례·채점 정보는 커밋하지 않는다 (`.gitignore` 참고).
