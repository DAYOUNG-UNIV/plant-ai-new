# 웹 채팅과 API 없는 데모, 두 사이트 배포

이 저장소에는 서로 다른 실행 파일이 있습니다. 두 개의 Streamlit 앱이 같은 GitHub 저장소를 사용해도 됩니다.

| 용도 | 실행 파일 | Gemini 키 | 센서 기록 |
|---|---|---|---|
| 본 사이트 | app.py | 자유 AI 대화에 필요 | Google Sheets 또는 업로드한 CSV |
| 데모 사이트 | demo_app.py | 불필요, 키를 읽지 않음 | demo/index.html에 저장된 집계 |

2026-10-07 기준 배포 대상은 공개 저장소 **`DAYOUNG-UNIV/plant-ai-new`의 `exhibition-recovery` 브랜치**입니다. 이 브랜치에는 복구 코드와 실제 기록 데모가 포함되어 있습니다. **새 본 사이트와 새 데모 사이트의 Streamlit 실행은 아직 확인되지 않았습니다.** 아래 절차로 새 주소 두 개를 생성하고 동작을 확인합니다. 기존 앱 [chatty-plant.streamlit.app](https://chatty-plant.streamlit.app/)의 주소·연결 브랜치·설정은 그대로 보존합니다.

기존 앱은 2026-10-07 00:01 KST 확인에서 `Error running app` 화면이 보였습니다. 이 화면만으로 Gemini 키 문제라고 판단할 수 없습니다. 정확한 원인은 기존 앱의 Manage app에서 실행 로그를 확인해야 하며, 새 앱을 만들기 위해 기존 앱을 삭제하지 않습니다.

## 1. 새 공개 저장소의 코드 반영 확인

사용자가 복구 코드 공개에 동의하여 [DAYOUNG-UNIV/plant-ai-new](https://github.com/DAYOUNG-UNIV/plant-ai-new)를 새 배포 저장소로 선택했습니다. 기존 `index.html`을 보존하고, **`exhibition-recovery` 브랜치에 배포 파일 51개**를 추가했습니다. 아래 진입점 파일을 사용해 Streamlit 배포를 진행합니다. `index.html`은 Python 앱의 실행 파일이 아닙니다.

이전 비공개 `DAYOUNG-UNIV/plant-ai`에는 복구 코드 [97a4cb4](https://github.com/DAYOUNG-UNIV/plant-ai/commit/97a4cb4d9b1cde5f087e2fc15d1a39d177b2fe9b)와 후속 안내 [71bcf25](https://github.com/DAYOUNG-UNIV/plant-ai/commit/71bcf2529a3c85d7581163e5924152483a8a1836)가 게시되어 있습니다. 이는 **이전 저장소의 이력**입니다. 현재 배포에는 새 공개 저장소의 브랜치를 사용합니다. 원본 비공개 저장소와 `main`의 `798a653`은 그대로 보존합니다.

새 저장소에서 확인할 필수 구조:

```text
app.py
demo_app.py
requirements.txt
basil_knowledge.json
plant_ai/                       Python 모듈과 offline_template.html
demo/index.html                 미리 계산된 데모
.streamlit/config.toml          화면·업로드 설정
.streamlit/secrets.example.toml  키 없는 설정 예시
```

공개할 코드에 `.streamlit/secrets.toml`, `.env`, 실제 API 키, 가상환경, 원본 센서 CSV는 포함하지 않습니다. 데모에는 `demo/index.html`의 미리 계산한 집계가 포함됩니다. `artifacts/`는 Git에서 제외되며, 데모 실행은 이 폴더에 의존하지 않습니다.

## 2. 새 Streamlit 앱 두 개 만들기

1절의 브랜치·파일 반영을 확인한 뒤 [Streamlit 대시보드](https://share.streamlit.io/)에서 로그인하고 `DAYOUNG-UNIV` 작업공간을 선택합니다. **Create app → Yup, I have an app → Paste GitHub URL**로 아래 소스 주소를 각각 입력해 두 앱을 만듭니다. GitHub 소스 주소는 완성된 사이트 주소가 아닙니다. [공식 배포 절차](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)

- 새 본 사이트 소스: [exhibition-recovery/app.py](https://github.com/DAYOUNG-UNIV/plant-ai-new/blob/exhibition-recovery/app.py)
- 새 데모 사이트 소스: [exhibition-recovery/demo_app.py](https://github.com/DAYOUNG-UNIV/plant-ai-new/blob/exhibition-recovery/demo_app.py)

| 설정 | 새 본 사이트 | 새 데모 사이트 |
|---|---|---|
| Repository | `DAYOUNG-UNIV/plant-ai-new` | `DAYOUNG-UNIV/plant-ai-new` |
| Branch | `exhibition-recovery` | `exhibition-recovery` |
| Main file path | `app.py` | `demo_app.py` |
| Advanced settings → Python version | `3.12` | `3.12` |
| Secrets | 다음 절의 Gemini·센서 설정 | 입력하지 않음 |
| App URL | 사용 가능한 새 이름 선택 | 본 사이트와 다른 새 이름 선택 |

저장소나 브랜치가 목록에 없으면 값을 직접 입력할 수 있지만, 실제 GitHub에 그 브랜치와 실행 파일이 있어야 합니다. 코드 반영 전에는 새 저장소에 실행 파일이 없어 경로 검증 오류가 표시되었습니다. 이전 배포 화면이 열려 있다면 새로고침한 뒤 위 값을 다시 선택합니다. **Deploy 버튼이 회색이면 먼저 선택한 저장소·브랜치에서 `app.py` 또는 `demo_app.py`가 열리는지 확인**합니다. 원본 비공개 저장소의 접근 권한 문제와 새 저장소의 파일 누락은 별개의 확인 항목입니다. Python은 이 코드로 검증한 `3.12`를 명시적으로 선택합니다. `numpy==2.5.3`은 Python 3.12 이상이 필요합니다.

본 사이트의 Secrets를 설정하고 **Save → Deploy**를 진행합니다. 데모도 같은 순서로 만들되 Secrets는 비웁니다. 새 공개 저장소에서 배포한 앱의 공유 범위는 아래 4절에서 확인합니다. 두 앱의 실행 화면과 발급된 주소를 확인하기 전에는 배포 완료로 보지 않습니다.

## 3. Gemini 키를 본 사이트에 연결

1. https://aistudio.google.com/api-keys 에 로그인합니다.
2. 기존 키가 사용 가능하면 유지하고, Blocked 등으로 사용할 수 없으면 새 키를 만듭니다. Google 공식 문서에 따르면 장기간 사용하지 않은 제한 없는 기존 키는 차단될 수 있습니다.
3. 무료로 운영하려면 연결 프로젝트의 Free Tier 여부와 사용 가능한 모델 할당량을 AI Studio에서 확인합니다. 이 앱이 결제를 켜지는 않습니다.
4. 새 본 사이트를 만들 때 Advanced settings → Secrets에 아래 값을 입력합니다. 이미 새 앱을 만들었다면 해당 앱의 Settings → Secrets에서 저장합니다. 기존 앱의 Secrets는 그대로 두고, 실제 키를 채팅·GitHub에 붙여 넣지 않습니다.

```toml
GOOGLE_API_KEY = "본인의 Gemini API 키"
GEMINI_MODEL = "gemini-3.8-flash"
SHEET_ID = "확인된 기존 센서 시트 ID"
SHEET_GID = "0"
# 데모 사이트 배포 후 실제 URL 입력. 아직 없으면 비워 두기.
DEMO_SITE_URL = ""
```

2026-10-06 확인한 공식 요금표에는 gemini-3.8-flash 표준 입력/출력의 무료 등급이 있습니다. 실제 할당량과 접근 가능 여부는 계정·프로젝트 기준으로 확인합니다. 무료 한도를 무제한 이용으로 해석하지 않습니다.

서버 키를 설정한 본 사이트의 모델은 운영자가 정한 값으로 고정됩니다. 키가 없는 로컬 시험에서는 화면의 세션용 키 입력을 사용할 수 있습니다.

## 4. 전시 공개 범위와 두 사이트 연결

새 배포 저장소 `plant-ai-new`는 사용자 동의에 따라 **공개**이며, 공개 저장소에서 만든 Streamlit 앱도 기본적으로 공개됩니다. 각 새 앱의 **Settings → Sharing → Who can view this app**에서 전시 관람객이 열 수 있는 **This app is public and searchable** 상태를 확인합니다. [공식 공유 설정](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)

원본 `plant-ai` 저장소는 비공개로 유지합니다. Community Cloud의 비공개 앱 한 개 제한은 앱을 비공개로 운영할 때의 제약이며, 이번 두 전시 앱은 공개로 배포합니다. 기존 앱을 삭제하거나 원본 저장소의 공개 범위를 바꾸지 않습니다.

새 주소 두 개가 발급되면 다음 연결을 마무리합니다.

- 본 사이트 Secrets의 `DEMO_SITE_URL`에 **새 데모 주소**를 입력하면 본 사이트 상단의 이동 버튼이 나타납니다.
- `demo_app.py`의 **실제 AI 대화 사이트 열기** 버튼도 **새 본 사이트 주소**로 갱신해 `plant-ai-new`의 `exhibition-recovery`에 반영합니다. 현재 코드의 링크는 기존 `chatty-plant.streamlit.app`이므로 새 주소가 생기기 전에는 두 새 사이트 사이의 연결이 완료된 것으로 보지 않습니다.

데모의 기록 수·마지막 측정일·미션·질문 버튼을 확인합니다. 이 데모는 Gemini·Sheets가 중단되어도 열람할 수 있지만, Streamlit 사이트 접속에는 인터넷과 서버가 필요합니다. 인터넷까지 없는 태블릿에서는 내려받은 HTML 또는 별도로 만든 PDF를 사용합니다.

## 5. 실제 연결 검증

새 본 사이트에서 Google Sheets 기록을 선택하고 마지막 기록 시각을 확인한 뒤 자유 채팅에 `마지막 기록일의 평균 온도를 알려줘`라고 입력합니다. 답변 아래 `Gemini 응답` 표시를 확인합니다. `기본 안내 · AI 생성 응답 아님`은 로컬 안내이므로 API 성공으로 세지 않습니다.

데모 사이트는 자유 생성형 대화를 제공하지 않으며, 모든 질문 안내는 저장된 결과입니다. 운영 키가 잘못되거나 무료 한도가 끝나도 데모는 같은 화면을 제공합니다.

- 키가 설정됐다는 표시만 있음: 아직 API 요청 성공을 확인한 것이 아닙니다.
- 키 오류·권한 오류: AI Studio 키 상태와 해당 프로젝트 권한을 확인합니다.
- 모델 없음·지원하지 않음: GEMINI_MODEL과 계정에서 사용할 수 있는 모델을 확인합니다.
- 429: 프로젝트 사용량 한도나 순간 요청 제한을 확인하고 데모로 이동합니다. 키를 여러 개 만드는 것으로 프로젝트 한도가 늘지는 않습니다.
- 시트 읽기 실패: ID·탭 번호와 읽기 권한을 확인합니다. 현재 앱은 로그인 없는 CSV 내보내기 방식입니다.

## 데모 기록 갱신

기록을 갱신할 때만 운영자 PC에서 아래와 같이 다시 제작하고 demo/index.html을 커밋합니다. 데모 방문자가 열 때 API나 Sheets에서 새 자료를 가져오지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts/build_web_demo.py --csv '센서-기록.csv' --source-name '확인된 센서 기록'
```

사용자가 명시적으로 합성 예시를 만들려면 `--synthetic`, 기록 없는 준비본은 `--empty`입니다. 실제 기록 대신 합성 자료가 자동 삽입되지 않습니다.

## 공식 근거

- 배포: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- Secrets: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings
- Python 변경: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/upgrade-python
- 공개·비공개 앱: https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
- Gemini 키: https://ai.google.dev/gemini-api/docs/api-key
- Gemini 요금: https://ai.google.dev/gemini-api/docs/pricing
- 사용량 제한: https://ai.google.dev/gemini-api/docs/rate-limits
