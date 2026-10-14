# nova2026-doctor-agent

[N.O.V.A. 2026](https://nova.snubhai.org/) (분당서울대병원 의료인공지능센터 × 엘리스) 참가용 Doctor Agent.

가상 환자를 문진·진찰한 뒤 **S·O·A·P 초진 기록 + 주진단 1개**를 제출하는 에이전트를 만든다.

## 대회 요약

| 단계 | 기간 | 모델 | 행동 |
|---|---|---|---|
| 예선 | 10.12 – 10.22 | `openai/gpt-6-luna` 고정 (엘리스 API, 10/13부터; 문서의 `gpt-oss-20b`는 옛 표기) | SAY · EXAM · DIAGNOSE |
| 본선 | 10.27 – 11.10 | 자유 (파인튜닝 가능) | SAY · EXAM · TEST · DIAGNOSE |

- 증례당 최대 50턴 · 30분, 증례마다 대회 모델 1회 이상 호출 필수
- SAY는 30자 이하, 한 번에 질문 하나 / EXAM·TEST는 한 번에 하나
- 예선 채점(100점): 정보수집 25 · 효율 20 · 진단추론 15 · 안전 15 · 의사소통 15 · 검사·추적 10
- 제출: 루트에 `run.py` + `requirements.txt`가 있는 ZIP(50MB 이하), Python 3.12, 인터넷 차단 환경(외부 API 호출 금지)

## 진료 흐름 (`agent/`)

LLM(대회 모델)은 계획·판단·글쓰기만 하고, 무엇을 보낼지(30자·중복·복합 질문·턴·시간)는 코드가 통제한다.

1. **PLAN**: 첫 진술·활력징후로 감별진단 3~5개와 질문·표적 진찰 계획
2. **문진·진찰**(코드): 계획 질문 → 공통 병력(소아는 발달·접종) → 재계획 → 진찰 → 재계획. 약이 있으면 이름·용량·횟수·기간을 하나씩 묻는다
3. **CHALLENGE**(반대 의견 검토) → **REASSESS**(최종 주진단)
4. **마무리 설명** 8줄(공감·진단·근거·검사·치료·금기·응급 경고·재진) + 환자 되물음 답변
5. **기록**: S·A·P 작성 → S를 대화 원문과 대조하는 검증 호출 → O.exam은 코드가 진찰 로그에서 직접 작성

기능 스위치는 `agent/config.py`. 버전별 변경과 점수는 `docs/CHANGELOG.md`, 제출 기록은 `submissions/README.md`.

## 로컬 개발

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env     # NOVA_DEV_KEY=... 채우기 (커밋 금지)

.\.venv\Scripts\python.exe dev.py bench\run_samples.py v1.3        # 공개 샘플 3건을 서버에서 실행 → bench/results/v1.3/
.\.venv\Scripts\python.exe dev.py bench\grade.py v1.3              # 근사 채점(sol). 버전 비교용, 공식보다 약 11점 후함
.\.venv\Scripts\python.exe make_zip.py --save v1.3                 # 제출 ZIP(키 패턴 검사 포함) → submissions/
```

> API 키·비공개 증례·채점 정보는 커밋하지 않는다 (`.gitignore` 참고). `make_zip.py`가 ZIP 안의 키 패턴을 검사한다.

## 참고 자료·출처 (대회 FAQ: 외부 자료는 출처 명시)

제출 코드는 외부 API를 호출하지 않는다. 개발 중 참고·사용한 자료:

| 자료 | 용도 | 라이선스·출처 |
|---|---|---|
| N.O.V.A. 2026 지침 자료실 (`/opt/nova/guidelines`, 15,904건: StatPearls 질환 개요, 진료지침, 질병관리청 국가건강정보) | `agent/guidelines.py`가 주진단 관련 검사·치료 절을 발췌(현재 스위치 OFF). 문서별 라이선스는 `catalog.csv` 라이선스 열, 발췌 시 "(출처: 기관 연도)" 표기 | 대회 제공, 각 문서의 라이선스 따름 |
| N.O.V.A. 2026 공개 샘플 증례 3건(devkit) | 로컬 실행·근사 채점의 모범 기록 비교 | 대회 제공, 레포 미포함 |
| Nori et al. 2025, *Sequential Diagnosis with Language Models* (arXiv:2506.22405) | CHALLENGE(반대 의견)·가설 갱신 설계 | 논문 |
| Li et al. 2024, *MediQ* (arXiv:2406.00922) | 확신도 기반 질문 중단 | 논문 |
| Schmidgall et al. 2024, *AgentClinic* (arXiv:2405.07960); Johri et al. 2025, *CRAFT-MD* (Nat Med) | 턴 예산·질문 가치 설계 근거 | 논문 |
| 그 밖의 논문 23편 | `docs/RESEARCH.md`에 기법별 출처·효과 정리 | 논문 |

벤치마크 데이터(DDXPlus, ACI-Bench 등)는 내려받아 살펴봤으나 코드·프롬프트에 사용하지 않았다.
