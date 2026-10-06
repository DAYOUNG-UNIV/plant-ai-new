# 검증 기록 · 2026-10-04

이 기록은 이번 연구 확장 후 로컬 Windows / Python 3.12.10 환경에서 수행한 결과다.

## 실행 결과

| 항목 | 결과 | 범위 |
|---|---|---|
| 전체 pytest | 261 passed, 3 warnings / 13.21초 | 계산·데이터·RAG·모의 Gemini·Streamlit AppTest |
| 의존성 검사 | No broken requirements found | 설치된 requirements |
| 고정 질문 평가 | 30/30, 모델 호출 0회 | 직접 정답을 확인한 합성 기록의 조회 계획·근거·일부 수치 |
| Streamlit 상태 | HTTP 200, ok | 로컬 127.0.0.1:8501 |
| 실제 연결 설정 | Gemini 키 없음, Sheets ID 없음 | 비밀값 출력 없이 설정 여부만 확인 |
| 실제 Gemini / Sheets / 공개 배포 | 미검증 | 실제 계정 설정 후 확인 필요 |
| 시각 검토 | Streamlit AppTest로 컴포넌트와 흐름 검증 | 브라우저 자동화 런타임 오류로 실제 화면의 이미지 검토는 미완료 |

3개 경고는 기존 혼합 시간대 파싱의 Pandas FutureWarning 1건과 테스트의 NumPy timedelta DeprecationWarning 2건이다. 고정된 현재 버전에서는 테스트가 통과한다.

## 의미 있는 검증 사례

- 불규칙 수집에서 관측 평균과 시간 가중 평균이 달라짐, 긴 공백 제외, 마지막 값 외삽 방지, 반열린 범위 경계.
- 구간 최소·최대의 실제 발생 시각 보존, 빈 구간·결측·긴 공백에서 평균선 연결 방지.
- 질문에 명시된 기간과 실제 도구 인자의 일치, 잘못된 도구 거부, 빈 기록과 실패 구분, 모델에게 전송하지 못한 근거 제외.
- 오래된 이력의 분석 기준일과 실제 측정 최신성 분리, 실제 오늘을 명시한 질문의 빈 결과.
- 고정 미션 3개는 API 키가 있어도 외부 AI 요청 없음.
- 리허설 기본 비활성, 시작·완료·중단·종료·삭제, 1,000개 이벤트 상한과 초과 집계, 원질문·답변·음성·개인정보 필드 제외.
- 에이전트 평가기가 잘못된 수치 정답, 다른 기간에서 가져온 수치, 실행 없이 붙인 성공 표지를 실패로 판정하는지 확인.

평가 질문은 개발과 함께 만든 회귀 세트다. 독립적인 비공개 평가 세트가 아니며, Gemini의 자연어 사실성이나 사용자의 학습 효과를 평가한 점수가 아니다.
[질문 세트](../evals/scenarios.json)와 [결과 JSON](../evals/results-2026-10-04.json)을 함께 보관한다.

## 합성 20만 행 조회 비교

기존 방식은 매 질의마다 날짜 배열로 전체 데이터를 선택한 뒤 통계를 계산한다. 새 방식은 정렬된 시간 인덱스와 누적 합계·개수·블록 극값을 사용한다.

| 항목 | 측정 |
|---|---:|
| 질의 세트 | 24개 범위 × 3회 |
| 인덱스 생성 | 72.35 ms |
| 전체 스캔 세트 중앙값 | 815.54 ms |
| 인덱스 조회 세트 중앙값 | 6.02 ms |
| 해당 환경의 비율 | 약 135.46배 |
| 동등성 확인 | 행 수 + 세 센서의 유효 개수·평균·최소·최대 |

새 조회에는 공백·시간 지원 정보의 계산도 포함된다. 비교 정답은 두 방식 모두 제공하는 관측 통계로 한정한다. 시간 가중 통계 자체의 정답은 별도 단위 테스트로 검증했다.

이 측정에는 CSV 다운로드, 네트워크, Gemini 응답, 실제 사용자 동시 접속이 포함되지 않는다. 깨끗하고 균일한 합성 자료의 한 PC 측정값이며 실서비스 전체가 같은 비율로 빨라졌다는 뜻은 아니다.

## 재실행

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/evaluate_agent.py --output artifacts/evaluation.json
.\.venv\Scripts\python.exe scripts/benchmark_queries.py --output artifacts/benchmark.json
.\.venv\Scripts\python.exe scripts/check_connection.py
~~~

실제 API 키·시트를 설정한 이후의 live 점검, 안경 연동, 사용자 비교 실험은 별도 단계다. 이번 커밋에는 원본 연구계획서 PDF, 실제 센서 CSV, API 키가 포함되지 않는다.
