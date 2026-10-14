"""기능 스위치. 제출 ZIP마다 값을 바꿔 사이트 샘플 검증으로 A/B 비교할 수 있다(make_zip.py --set KEY=VALUE).

서버에는 환경 변수를 넘길 수 없으므로 파일 상수로 둔다.
"""
USE_GUIDELINES = True   # 지침 코퍼스 발췌를 기록 작성에 첨부(v1.0)
VERIFY_S = True         # S 항목 사실 검증 호출(v1.1)
CORE_EXAMS_FULL = False  # True면 의식·지남력과 복부 촉진도 공통 진찰에 포함(v0.9 방식)
