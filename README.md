# 쫑알쫑알 화분 · Talking Basil Pot

센서 이력을 조회하고 바질 관리 가이드를 검색하여 근거와 함께 답하는 전시용 웹 앱입니다.
원작: 이다영 / 쫑알쫑알. 이번 버전은 기존 Arduino → Google Sheets 수집 경로를 유지합니다.

## 이번 복구에서 달라진 점

- 오래된 LangChain/Google Generative AI 연결을 현재 Google Gen AI SDK의 명시적 함수 호출로 교체했습니다.
- 작은 관리 가이드는 로컬 BM25·한국어 문자 n-gram 검색으로 읽습니다. 별도 임베딩 API·FAISS 설치가 필요 없습니다.
- 시간 인덱스·누적 합계/유효 관측 수·블록별 최솟값/최댓값으로 기간 조회를 처리합니다.
- 마지막 기록 기준과 실제 오늘 기준을 선택합니다. 기본 UI는 마지막 기록 기준입니다.
- 수집 중단, 날짜 경계, 누락·중복·미래 시각, 범위 밖 센서값을 처리하고 실제 측정 최신성을 별도로 표시합니다.
- 비교 기간, 세 센서 추세, 수집 공백·급변 후보, 답변 근거와 세션 성능 기록을 제공합니다.
- 키나 네트워크가 없어도 제한된 기본 안내와 로컬 가이드 검색을 사용할 수 있습니다. Gemini 응답과 구분합니다.
- 생성된 Python 코드를 실행하지 않습니다. 등록된 읽기 전용 함수만 호출합니다.

연구 기여나 성능 개선을 입증했다는 뜻은 아닙니다. 실측 데이터·사용자 연구로 검증해야 합니다.

## 연구계획서에 맞춘 추가 확장 (2026-10-04)

- 식물 탐구: 직접 관찰 → 기록 카드 → 질문의 기본 안내 → 이해 확인. 세 고정 미션은 키가 있어도 외부 AI 없이 동작합니다.
- 자유 대화: 명확한 기간·비교·추세·공백 질문은 먼저 필요한 Python 조회를 실행하고 관련 도구만 Gemini에 제공합니다.
- 근거 확인: 질문 범위와 조회 인자에 맞는 성공·빈 기록·실패를 구별합니다. 이 검사가 모델의 모든 문장이 사실임을 보증하지는 않습니다.
- 불규칙 수집: 관측 평균과 공백을 제외한 시간 가중 평균을 구분합니다. 10분 초과 간격과 마지막 관측 이후는 채우지 않습니다.
- 그래프: 각 구간의 최솟값·최댓값과 실제 발생 시각을 보존하고, 긴 공백이나 결측이 있는 구간의 평균선을 끊습니다.
- 설명 선택: 바질 캐릭터/해설자, 일반/쉬운 설명.
- 운영자 리허설: 기본 비활성, 시작 후 제한된 버튼·성능 기록만 메모리에 수집합니다. 정식 방문자 연구와는 구분합니다.

[문헌 검토·설계 결정·비교 실험 계획](docs/research-roadmap.md)과 [검증 결과](docs/validation-2026-10-04.md)를 함께 확인하세요.

## 실행 (Python 3.12)

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
~~~

Linux/macOS에서는 .venv/bin/python을 사용합니다.
배포에는 requirements.txt, 개발·테스트에는 requirements-dev.txt를 사용합니다.

.streamlit/secrets.example.toml을 .streamlit/secrets.toml로 복사해 로컬 편집기에 입력합니다.

~~~toml
GOOGLE_API_KEY = "본인의 Gemini API 키"
GEMINI_MODEL = "gemini-3.8-flash"
SHEET_ID = "Google Sheets 문서 ID"
SHEET_GID = "0"
~~~

실제 키 파일은 Git에서 제외합니다. 기존 GOOGLE_API_KEY와 SHEET_ID 설정을 그대로 지원합니다.
GEMINI_API_KEY도 허용합니다. 환경변수가 secrets보다 우선하며, 같은 출처에서는 GOOGLE_API_KEY가 우선합니다.
설정된 서버 키를 앱 입력칸에 노출하지 않습니다. 서버 키가 없을 때만 세션용 키 입력을 표시합니다.
모델 접근 여부와 무료 할당량은 본인 AI Studio 프로젝트에서 확인해야 합니다.
이 앱은 결제를 켜거나 모델을 임의로 유료 모델로 전환하지 않습니다.

### 데이터 연결

- Google Sheets: 기존 앱과 같은 CSV 내보내기 방식입니다. 인증 없이 CSV를 읽을 수 있는 문서만 연결됩니다.
- 비공개 시트를 공개로 바꿀 필요는 없습니다. CSV로 내보내 업로드하거나, 후속으로 인증된 읽기 연결을 추가할 수 있습니다.
- CSV: 25MB 이하, UTF-8/CP949. Timestamp, Temperature, Humidity, Soil Moisture 및 일반적인 한글/영문 별칭을 인식합니다.
- 합성 시연 데이터: 왼쪽에서 직접 선택해야 생성합니다. 연결 실패를 합성 데이터로 자동 대체하지 않습니다.
- Sheets 재조회는 열린 페이지에서 120초 간격입니다. 연결 실패 시 같은 세션의 마지막 성공 스냅샷을 명시적으로 표시합니다.
- 20만 행 전체 CSV 다운로드 자체는 여전히 필요합니다. 개선 대상은 로컬 반복 조회입니다. 저장소의 증분 동기화는 후속 과제입니다.

### 수집이 중단된 경우의 날짜 의미

마지막 기록이 9월 20일, 실제 날짜가 10월 4일이라고 가정합니다.

| 선택 | 오늘/기준일 | 이번 달 | 최신성 |
|---|---|---|---|
| 마지막 기록 기준 | 9월 20일 기록 | 9월 기록 | 실제 10월 4일 기준 오래된 측정 |
| 실제 오늘 기준 | 10월 4일 기록, 없으면 없음 | 10월 기록 | 같은 실제 시각 기준 |
| 명시적 2026-08-15 | 두 모드 모두 8월 15일 | 해당 없음 | 실제 시각 기준 |

화면과 답변 근거에는 분석 기준일과 실제 마지막 측정 시각을 따로 기록합니다.
주간은 월요일 시작이며 겨울 2025는 2025-12-01부터 2026-03-01 전까지입니다.
모든 내부 범위는 시작 포함·끝 제외입니다. UI의 마지막 포함일은 다음 날 0시로 변환합니다.
기존 mean은 유효 관측값의 산술평균(관측 수 가중)입니다.
추가 time_weighted_mean은 양 끝 센서값이 유효하고 10분 이하로 연결되는 구간에만 앞의 값을 적용한 시간 가중 근사입니다.
긴 공백은 전체를 제외하며 마지막 측정값을 미래로 연장하지 않습니다. 전체 요청 기간의 평균으로 해석하면 안 됩니다.
coverage는 요청 기간 대비 이런 짧은 관측 간격이 뒷받침하는 시간 비율입니다. 결측을 반영한 센서별 비율은 다를 수 있습니다.
각 기간의 실제 측정 행 수와 시간 지원 범위는 별개입니다. 예를 들어 두 인접 측정 사이의 작은 부분 범위에는 원본 행이 없어도 추정 지원시간은 있을 수 있습니다.
토양 센서값은 보정 정보가 없어 원시값으로만 표현합니다. 공기 습도 %와 혼동하거나 물주기를 자동 결정하지 않습니다.

## 구조

~~~text
app.py                    Streamlit 화면, 재조회, 대화 및 관측 기준 선택
plant_ai/data.py           설정, 제한된 CSV 로딩, 명시적 합성 자료
plant_ai/analytics.py      정제, 시간 인덱스, 기간 통계, 비교, 이벤트 후보
plant_ai/knowledge.py      로컬 가이드 검색과 JSON 출처
plant_ai/agent.py          Gemini 함수 호출, 응답 근거, 예산 및 오류 처리
plant_ai/routing.py        명확한 질문의 계획과 도구 범위 선택
plant_ai/verification.py   조회 범위·실행 결과 감사와 구조화된 근거
plant_ai/inquiry.py        로컬 탐구 미션과 선택형 리허설 기록
tests/                    계산·검색·입출력·에이전트·화면 회귀 테스트
scripts/benchmark_queries.py  합성 20만 행 반복 조회 성능 비교
scripts/check_connection.py  설정 및 선택적 실제 연결 점검
scripts/evaluate_agent.py  고정 합성 기록의 오프라인 질문 평가
evals/scenarios.json       기간·공백·응답 보류 등 30개 평가 사례
~~~

가이드 출처는 기존 사용자 작성 basil_knowledge.json입니다. 출판물로 검증된 지식이라고 표시하지 않습니다.
RAG와 함수 호출은 환각을 없애지 않습니다. 도구의 정확한 선택, 기간 해석, 답변의 근거 일치도 검증이 필요합니다.

## 검증

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/benchmark_queries.py --output artifacts/benchmark.json
.\.venv\Scripts\python.exe scripts/check_connection.py
.\.venv\Scripts\python.exe scripts/evaluate_agent.py --output artifacts/evaluation.json
~~~

실제 키와 Sheet 설정을 저장한 뒤 아래 명령으로 소량의 실제 API 요청을 보낼 수 있습니다.

~~~powershell
.\.venv\Scripts\python.exe scripts/check_connection.py --live
~~~

기본 테스트는 외부 API를 모의 처리합니다. 통과했다고 실제 Gemini/Google Sheets/공개 배포가 복구된 것은 아닙니다.
벤치마크는 고정된 합성 데이터에서 같은 24개 범위에 대해 행 수 및 세 센서의 개수·평균·최소·최대가 일치하는지 확인합니다.
데이터 생성과 인덱스 구축 시간은 조회 시간에서 분리하며, 네트워크와 LLM 응답 시간은 측정하지 않습니다.

## Streamlit Community Cloud 반영

1. 기존 비공개 GitHub 저장소를 권한 있는 계정으로 복제합니다.
2. 이 작업의 변경 커밋을 원래 저장소 이력 위로 옮겨 별도 브랜치에서 검토합니다.
3. app.py, requirements.txt, plant_ai 디렉터리, basil_knowledge.json을 함께 반영합니다.
4. 앱 설정의 Python을 3.12로 선택하고 기존 Secrets의 GOOGLE_API_KEY/SHEET_ID를 유지합니다.
5. 실제 연결 점검 후 대화·수집 중단·한도 초과·재시작을 확인하고 전시 버전을 고정합니다.

이 ZIP 작업 폴더의 첫 커밋은 원본 ZIP 스냅샷입니다. 원격 저장소의 기존 이력을 덮어쓰거나 강제 push하지 않습니다.
로컬 커밋과 공개 서비스 배포는 별개입니다.

## 근거 문서

- [Gemini 요금과 무료 등급](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini 모델 종료 일정](https://ai.google.dev/gemini-api/docs/deprecations)
- [Google Gen AI Python SDK](https://github.com/googleapis/python-genai)
- [후속 연구·평가 계획](docs/research-roadmap.md)


## 저장 기록 기반 전시 패키지와 오프라인 열람

앱의 **데이터 상태 → 이 기록으로 오프라인 열람본 만들기**에서 현재 읽은 자료를 단일 HTML과 JSON으로 내려받을 수 있습니다. 합성 자료는 합성으로 표시하며 실제 자료로 자동 대체하지 않습니다. 마지막 측정 시각·마지막 기록일 기준 분석·제작 시각을 각각 표시합니다.

CSV로 독립형 전시 폴더와 ZIP을 만들려면 다음 순서로 실행합니다. 원본 시트의 비공개 열은 제외하고 측정 시각·온도·공기 습도·토양 원시값만 정제 CSV에 포함합니다. `capa`, `grd` 같은 의미가 확인되지 않은 열을 토양 센서로 추정하지 않습니다.

~~~powershell
.\.venv\Scripts\python.exe scripts/build_exhibition.py --csv '실제-센서-기록.csv' --source-name '사용자가 확인한 센서 기록'
# PDF 제작 환경에 ReportLab과 한글 폰트가 설치된 경우:
python scripts/render_exhibit_pdf.py
.\.venv\Scripts\python.exe scripts/build_exhibition.py --finalize
.\.venv\Scripts\python.exe scripts/verify_exhibition.py artifacts/exhibition
~~~

- 실제 자료가 아직 없으면 `--csv ...` 대신 `--empty`를 사용합니다. 수치·그래프를 비운 **실제 기록 연결 전** 준비본입니다.
- PDF 제작만 ReportLab 및 한글 폰트가 필요합니다. 기본 폰트 경로는 Windows의 `C:/Windows/Fonts`이며 `--font-dir`로 바꿀 수 있습니다. 서버와 완성된 HTML/PDF 열람에는 해당 라이브러리가 필요하지 않습니다.
- 출력: `artifacts/exhibition/`, `artifacts/plant-exhibition.zip`, `output/pdf/plant-offline-guide.pdf`. 새 자료로 다시 만들면 이전 PDF를 제거하므로 PDF도 다시 제작한 뒤 `--finalize`합니다.
- 정제 CSV의 SHA-256과 각 패키지 파일의 SHA-256을 저장합니다. 검사는 파일 변경·누락을 찾으며 원자료의 진위나 센서 보정 정확성을 인증하지 않습니다.
- 생성·질문 안내·탐구 미션에는 외부 AI 호출이 없습니다. 새로운 자유 대화는 온라인 앱을 사용합니다.
- 기록 요약은 직접 관측한 사실을 보여 주며 식물 상태의 원인을 진단하지 않습니다. 측정이 중단된 구간은 그래프를 끊어서 표시합니다.
- 실행·태블릿 연결·문제 해결은 [전시 사용 안내](docs/exhibition-guide.md)를 참고하세요. PDF는 태블릿 단독 열람용이며 웹 인터랙션은 실제 사용할 기기에서 리허설해야 합니다.

선택적 화면 검사는 Node.js, Playwright, Microsoft Edge가 설치된 환경에서 `node scripts/test_offline_browser.cjs artifacts/exhibition/tablet.html artifacts/offline-ui-qa`로 실행합니다. 네트워크를 차단한 별도 브라우저에서 검사하며 테스트용 합성 자료는 전시 ZIP에 포함하지 않습니다.


## 웹사이트 두 개 운영

- `app.py`: Gemini 자유 대화와 센서 기록 탐색. `GOOGLE_API_KEY`, `GEMINI_MODEL`, `SHEET_ID`, `SHEET_GID`를 본 사이트의 Streamlit Secrets에 설정합니다.
- `demo_app.py`: `demo/index.html`의 저장된 기록만 보여 줍니다. API 키·Sheets 연결이 필요하지 않으며, 키가 설정돼 있어도 읽지 않습니다.
- 같은 GitHub 저장소에서 각 실행 파일을 선택해 두 개의 Streamlit 앱으로 배포합니다. Python은 3.12를 선택합니다.
- 데모 배포 뒤 본 사이트 Secrets에 `DEMO_SITE_URL`을 설정하면 데모로 이동하는 버튼이 표시됩니다. 실제 배포가 끝나기 전에는 주소를 추측해서 넣지 않습니다.

계정 찾기, 기존 앱 업데이트, Gemini 키 발급·연결과 점검은 [두 사이트 배포 안내](docs/web-deployment.md)에 정리했습니다.
