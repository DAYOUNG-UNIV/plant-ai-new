"""Static preview entry point. Only stdlib and Streamlit are runtime dependencies.

The generated page is read from disk; this entry point does not import the AI
agent, load secrets, query Sheets, or generate new answers.
"""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


DEMO_HTML = Path(__file__).resolve().parent / "demo" / "index.html"


def main():
    st.set_page_config(page_title="쫑알쫑알 화분 · 미리보기", page_icon="🌿", layout="wide")
    st.title("🌿 쫑알쫑알 화분 · 미리보기")
    st.info(
        "저장된 기록과 관찰 미션을 둘러보는 미리보기입니다. "
        "실시간 센서에 연결하거나 AI가 새 답변을 생성하지 않습니다. "
        "질문 버튼은 미리 준비한 안내를 보여 줍니다."
    )
    st.link_button("실제 AI 대화 사이트 열기", "https://chatty-plant.streamlit.app/")
    try:
        html = DEMO_HTML.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        st.warning("미리보기 자료를 아직 준비하고 있어요. 준비가 끝난 뒤 다시 열어 주세요.")
        return
    if not html.strip():
        st.warning("미리보기 자료가 비어 있어요. 자료를 다시 준비한 뒤 이용해 주세요.")
        return

    # Streamlit makes the iframe fill the container width. The page has its own
    # tablet/phone breakpoints; scrolling keeps every section reachable.
    components.html(html, height=1000, scrolling=True, tab_index=0)
    st.caption("기록 출처와 마지막 측정 시각은 미리보기 상단에서 확인할 수 있습니다.")
    st.download_button(
        "저장된 미리보기 내려받기", data=html,
        file_name="talking-basil-preview.html", mime="text/html", key="download_demo",
    )


if __name__ == "__main__":
    main()