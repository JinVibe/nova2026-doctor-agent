# 대회 운영 가이드 정리 (10.12 기준)

> 출처: 운영진 메일(10.12), `run_agent 실행 흐름과 agent 진료 실제 예시.pptx`(10.11), 개회식 슬라이드
> 시작 패키지·개발 키트·공개 샘플 3건은 플랫폼 **개발 가이드**에서 내려받기 (10.12 새벽 갱신본 사용)

---

## 1. 플랫폼 & 일정

| 항목 | 내용 |
|---|---|
| 로그인 | 신청 이메일 입력 → 로그인 링크(15분 유효, 1회용) |
| 개발 키 | '개발·API' 화면에서 발급, 잔여 크레딧 비율 확인 |
| 샘플 검증 | '예선 제출'에 ZIP 업로드 → 공개 샘플 3건 실행 + 참고 점수. **하루 10회**, 공식 횟수와 별개 |
| 공식 평가 | **10.13(화) 오픈 예정**, 하루 1회, 리더보드 5증례 |
| 모델 변경 가능성 | 예선 Doctor가 gpt-oss-20b → **gpt-6-luna**로 바뀔 수 있음. `client.chat()`은 동일하지만 **temperature는 gpt-oss만 지원** → 생략해도 동작하게 구현 |

## 2. 실행 구조

```
prepare()  ── 최대 10분, 증례 접근 불가 (예선: return None)
client.ready()  ── 이 시점부터 진료 30분
consult(client, state)  ── 최대 30분 · 50턴
client.diagnose(note, diagnosis)  ── 제출 → 채점
```

```python
from nova_sdk import RECORD_FIELDS, NovaError, empty_record, run_agent

def prepare():
    return None

def consult(client, model):
    session = client.session()   # 시작 정보 · 남은 턴 · 가능한 행동 · 마감
    ...                          # client.chat() → client.say() / client.exam()
    return client.diagnose(note=note, diagnosis=diagnosis)

if __name__ == '__main__':
    run_agent(prepare, consult)  # ← 이 줄 없으면 해당 증례 실패
```

- 증례마다 **새 컨테이너**에서 `python run.py` 실행 → 증례 간 메모리·파일 공유 없음
- 환경변수 `NOVA_API_BASE`, `NOVA_RUN_TOKEN`, `NOVA_SESSION_ID`를 SDK가 읽음
- 서버에서는 `session()`이 **지난 행동 기록(events)을 주지 않음** → 대화 기록은 우리 코드가 직접 보관
- 모든 요청에 `request_id` — 같은 ID로 재전송하면 처음 응답을 그대로 반환(턴 중복 차감 없음)

## 3. 행동별 규칙

| 함수 | 턴 | 핵심 | 주요 오류 |
|---|---|---|---|
| `session()` | 0 | 시작 정보(첫 진술·활력징후), 남은 턴, 가능한 행동, 마감 | 409 MODEL_NOT_READY (ready 전) |
| `chat()` | 0 | Doctor 모델 호출(OpenAI 형식) | 403 MODEL_NOT_ALLOWED, 400 INVALID_COMPLETION_FIELDS, **503 PROVIDER_RESPONSE_INVALID(빈 답)** |
| `say()` | 1 | 1~30자, 질문 하나 | 400 CONTENT_TOO_LONG/EMPTY (1턴), 503 PATIENT_RESPONSE_INVALID (0턴, 재시도) |
| `exam()` | 1 | 신체진찰 동작 하나 | **422 EXAM_NOT_SPECIFIC / EXAM_NOT_RECOGNIZED (1턴 차감!)**, 503 (0턴) |
| `diagnose()` | 0 | S·O·A·P **15칸** + 주진단 한 줄. 빈칸 가능, 전부 비면 안 됨 | 이후 409 SESSION_CLOSED |

- 30자 초과는 SDK가 전송 전에 `ValueError` → 턴 안 씀. 직접 HTTP로 보내면 1턴 차감
- 뭉뚱그린 질문("증상을 전부 말씀해 주세요")은 환자가 "한 번에 하나씩"이라고 답하고 **1턴 소모**

### `client.chat()` 제약

- 설정 가능한 것: **`messages`, `max_tokens`, `reasoning_effort`(low/medium/high), `temperature`(0~2)** 뿐
- `tools`, `stream`, `response_format` 등을 넣으면 400 → **JSON 강제 기능 없음, 직접 파싱·검증**
- completion_tokens에 **보이지 않는 추론 토큰 포함**
- gpt-oss-20b는 **빈 답을 자주 반환**(추론만 하고 끝남) → 503. 비용·토큰 한도·효율 점수에는 안 들어가지만 **호출 200회 한도에는 포함**. 재시도 처리 필수

### EXAM 판정 방식 (중요)

| 판정 | 결과 | 예시 |
|---|---|---|
| 진찰표 행에 연결 | 그 행의 **정해진 소견** 반환 (채점 근거로 기록됨) | "양손을 앞으로 뻗게 해 떨림을 보겠습니다" → 자세성 떨림(-/-) |
| 표에 없는 진찰 (unlisted) | 모델이 **정상 소견**을 지어서 답함(새 이상 소견 없음) | "무릎 반사를 보겠습니다" → 2+/2+, 대칭 |
| 뭉뚱그림 (too_broad) | 422 EXAM_NOT_SPECIFIC, **1턴** | "신체검진 다 해 주세요", "손 떨림 확인하겠습니다" |
| 진찰 아님 (not_exam) | 422 EXAM_NOT_RECOGNIZED, **1턴** | 문진·혈액·영상·처치·기록 작성 ("수면 일지를 작성하겠습니다") |

→ "심장 청진", "슬개건 반사"처럼 **구체적인 동작 하나**로 요청해야 함. unlisted 진찰은 정상 소견만 돌아오므로 정보 가치가 낮음.

## 4. 한도

| 구분 | 한도 |
|---|---|
| 진료 세션(증례당) | 50턴 · Doctor 호출 200회 · 입력 50만 토큰 · 출력 10만 토큰 · 30분 |
| 개발 키(팀당 하루, KST 자정 초기화) | gpt-oss-20b 3,000회 · gpt-6.1-sol 100회 · gpt-6-luna 500회 |

## 5. 서버 실행 환경

- 인터넷 차단(대회 API만), **CPU 2코어 · RAM 8GB · GPU 없음**
- **읽기 전용 파일 시스템**, 쓰기는 `/tmp`(512MB)와 `/output`만 → HuggingFace 등 홈 캐시 쓰기 실패
- `requirements.txt`는 **PyPI 휠로만** 설치, 이미지 빌드 최대 20분 (실패 시 DEPENDENCY_DOWNLOAD_FAILED)
- 실패 사유: `failed` · `prepare_timeout` · `timeout(30분)` · `oom`. 샘플 검증은 stderr 끝 30줄까지 보임, 리더보드는 사유만
- 시작 패키지의 **`local.Dockerfile`**(인터넷 없음 + 읽기 전용)로 먼저 확인할 것

## 6. 개발 방법 3가지

| | 개발 키트 `develop.py` | 개발 키 + `sample.py` | 서버 (샘플 검증 · 공식) |
|---|---|---|---|
| 코드 위치 | 내 PC | 내 PC | 서버 컨테이너 |
| 가상 환자·진찰 판정 | **내가 고른 모델** | gpt-6.1-sol low (공식과 동일) | 샘플: gpt-6-luna / 공식: gpt-6.1-sol |
| 비용 | 내 부담 | **팀 크레딧** | 운영진 |
| 채점 | 없음 | 없음 | 샘플: luna 참고 점수 / 공식: sol |
| session() | 시작 정보 | 시작 정보 + 지난 기록 | 시작 정보만 |
| 진료 시간 | 30분 (변경 가능) | 세션 생성부터 30분 | ready 후 30분 |

**C01 1증례 비용(sample.py 기준): 약 117원** (Doctor 6원 · 가상 환자 69원 · 진찰 판정 42원) → 크레딧 5만 원이면 약 400증례. 비용의 대부분은 환자·판정 모델.

## 7. 채점

| 실행 | 판정 | 채점 모델 |
|---|---|---|
| 샘플 검증 | 모든 증례 완주·기록 제출·Doctor 1회 이상 호출 | gpt-6-luna (환자 low · 판정 low · 채점 medium) |
| 공식 평가 | Doctor 미호출 증례 0점 | 임상 채점·진단 판정 **gpt-6.1-sol high**, 친절도 gpt-6.1-sol low |
| 예선 최종 | 같음 | GPT · Claude · Gemini 평균 |

- 채점 모델이 받는 것: **서버에 기록된 대화·진찰**, 제출한 기록 텍스트, **비공개 루브릭(문진·진찰·검사·설명·안전 항목)**. 우리 Doctor 프롬프트는 안 받음
- 판정 근거는 **기록 문장을 글자 그대로 인용** → 묻지 않고 적은 내용은 "수행"으로 인정 안 함
- 진단 판정은 **5점 척도** (Nori et al., *Sequential Diagnosis with Language Models*, Microsoft AI 2025)

## 8. 기본 에이전트의 C01 결과 (반면교사)

**증례:** 62세 여성, 불면 → 정답 **수면제 의존**(Sedative-Hypnotic Use Disorder). 기본 에이전트 진단은 "주요 불면증"으로 **오진**.

| 정보수집 | 진단추론 | 검사·추적 | 안전 | 의사소통 | 효율 | **총점** |
|---|---|---|---|---|---|---|
| 3.8 / 25 | 4.2 / 15 | **0 / 10** | 11.3 / 15 | **2.0 / 15** | **20 / 20** | **41.2** |

- 30턴 · 4분 20초 · 호출 69회(Doctor 37) → **효율은 만점.** 30턴과 Doctor 37회 호출로도 감점이 없었음
- **진찰 10번 중 6번 거절** = 6턴 낭비. 표에 연결된 건 심장·폐 청진 2번뿐
- 결정적 단서("졸피뎀 10mg, 밤 4알·새벽 2알")를 듣고도 **기록에서 누락**, 나이 오기(66세), FH·ROS 빈칸
- 설명 SAY가 30자를 넘어 재질문 반복 → **진단·계획 설명이 제대로 전달 안 됨**(의사소통 2점)
- 기본 에이전트 구조: 매 턴 Doctor에 "다음 행동" JSON 요청(medium) → 30자·형식 검사 → 전송. 빈 답은 2·5·10초 대기 후 재시도, 형식 오류는 최대 3번 재질문. MAX_TURNS 30에서 `write_record()`
