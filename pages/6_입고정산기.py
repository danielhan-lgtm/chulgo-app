import streamlit as st
import streamlit.components.v1 as components
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import load_config

st.set_page_config(page_title="입고정산기", page_icon="📦", layout="wide")

PORT = 3001

# 백그라운드 FastAPI 서버 - 앱 수명 동안 1번만 시작
@st.cache_resource
def _start_api_server():
    import receiving_api
    receiving_api.start_server(PORT)
    import time
    time.sleep(2)  # 서버 기동 대기
    return True

_start_api_server()

cfg = load_config()
if not cfg.get("ourbox_id") or not cfg.get("ourbox_pw"):
    st.warning("⚠️ 아워박스 계정이 설정되지 않았습니다.")
    with st.expander("설정하기"):
        with st.form("ourbox_creds"):
            oid = st.text_input("아워박스 아이디", value=cfg.get("ourbox_id", ""))
            opw = st.text_input("아워박스 비밀번호", value=cfg.get("ourbox_pw", ""), type="password")
            if st.form_submit_button("저장"):
                from utils import save_config
                save_config({"ourbox_id": oid, "ourbox_pw": opw})
                st.success("저장됨. 새로고침하세요.")

components.iframe(f"http://127.0.0.1:{PORT}", height=900, scrolling=True)
