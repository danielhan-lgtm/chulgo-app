import streamlit as st
import datetime
from utils import *

bh_ok     = bool(st.session_state.get("api_token"))
slack_ok  = bool(st.session_state.get("slack_token"))
gmail_ok  = bool(st.session_state.get("gmail_token"))
master_ok_flag = bool(DEFAULT_MASTER) or bool(st.session_state.get("master_bytes"))
bh_locs  = len(st.session_state.get("api_locations", []))
master_status = "자동 연결" if bool(DEFAULT_MASTER) else ("업로드됨" if st.session_state.get("master_bytes") else "미연결")

# 알림 배너
if not bh_ok:
    st.markdown('<div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:10px;padding:11px 16px;margin-bottom:14px;font-size:0.84rem;color:#991b1b;font-weight:500;">⚠️ <b>박스히어로 API 미연결</b> — 사이드바 연동 섹션에서 연결해주세요.</div>', unsafe_allow_html=True)

# ── 파이프라인 상태 ──────────────────────────────────────
def _pipe_node(icon, label, ok, sub):
    dot_cls = "on" if ok else "off"
    return f"""<div class="pipe-node">
        <div style="font-size:1.5rem;margin-bottom:4px;">{icon}</div>
        <div class="pipe-name"><span class="pipe-dot {dot_cls}"></span>{label}</div>
        <div class="pipe-sub">{sub}</div>
    </div>"""

st.markdown(f"""
<div class="pipe-wrap">
    {_pipe_node("💬", "Slack", slack_ok, "채널 연결됨" if slack_ok else "미연결")}
    <div class="pipe-arrow">→</div>
    {_pipe_node("📧", "Gmail", gmail_ok, "연결됨" if gmail_ok else "미연결")}
    <div class="pipe-arrow">→</div>
    {_pipe_node("📂", "마스터 파일", master_ok_flag, master_status)}
    <div class="pipe-arrow">→</div>
    {_pipe_node("⚙️", "변환 엔진", True, "대기 중")}
    <div class="pipe-arrow">→</div>
    {_pipe_node("📦", "박스히어로", bh_ok, f"{bh_locs}개 위치" if bh_ok else "미연결")}
</div>
""", unsafe_allow_html=True)

# ── 지표 카드 4개 ────────────────────────────────────────
today      = datetime.date.today().isoformat()
cnt        = st.session_state.get("tx_counter", {}).get(today, {"total": 0, "success": 0, "error": 0})
warn_count = sum(1 for l in st.session_state.get("tx_logs", [])
                 if l["level"] == "warning" and l["ts"].startswith(datetime.date.today().strftime("%m-%d")))

m1, m2, m3, m4 = st.columns(4, gap="small")
for col, label, val, icon, icon_bg, badge_txt, badge_cls in [
    (m1, "오늘 총 처리",  cnt["total"],    "📋", "#eff6ff", "건", "badge-gray"),
    (m2, "전송 성공",     cnt["success"],  "✅", "#ecfdf5", "건", "badge-green"),
    (m3, "전송 실패",     cnt["error"],    "❌", "#fef2f2", "건", "badge-red"),
    (m4, "경고",          warn_count,      "⚠️", "#fffbeb", "건", "badge-yellow"),
]:
    col.markdown(f"""<div class="metric-card">
        <div class="metric-icon" style="background:{icon_bg};">{icon}</div>
        <div class="metric-label">{label}</div>
        <div class="metric-value">{val}</div>
        <div><span class="metric-badge {badge_cls}">오늘 {val}{badge_txt}</span></div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ── 로그 + Payload Inspector ────────────────────────────
logs = st.session_state.get("tx_logs", [])
log_col, inspect_col = st.columns([3, 2], gap="medium")

with log_col:
    st.markdown('<div class="section-header">📋 전송 이력 / 오류 로그</div>', unsafe_allow_html=True)
    lf1, lf2, lf3 = st.columns([3, 2, 1])
    with lf1:
        log_filter = st.selectbox("필터", ["전체", "성공", "오류", "경고"], label_visibility="collapsed", key="log_filter")
    with lf3:
        if st.button("초기화", key="clear_logs"):
            st.session_state["tx_logs"] = []
            st.session_state["tx_counter"] = {}
            st.rerun()

    filtered = [l for l in logs if log_filter == "전체" or
                (log_filter == "성공" and l["level"] == "success") or
                (log_filter == "오류" and l["level"] == "error") or
                (log_filter == "경고" and l["level"] == "warning")]

    if not filtered:
        st.markdown('<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:20px;text-align:center;color:#9ca3af;font-size:0.85rem;">전송 이력이 없습니다. 출고 전송 후 여기에 기록됩니다.</div>', unsafe_allow_html=True)
    else:
        level_cfg = {
            "success": ("✅", "log-success", "#065f46"),
            "error":   ("❌", "log-error",   "#991b1b"),
            "warning": ("⚠️", "log-warning", "#92400e"),
            "info":    ("ℹ️", "log-info",    "#1e40af"),
        }
        for i, log in enumerate(filtered):
            icon, css_cls, color = level_cfg.get(log["level"], ("•","","#374151"))
            cols = st.columns([7, 1])
            with cols[0]:
                st.markdown(f"""<div class="log-item {css_cls}">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                        <span style="font-weight:700;font-size:0.82rem;color:{color};">{icon} {log['message']}</span>
                        <span style="font-size:0.7rem;color:#9ca3af;white-space:nowrap;margin-left:8px;">{log['ts']}</span>
                    </div>
                    {f'<div style="font-size:0.74rem;color:#6b7280;margin-top:3px;">{log["detail"]}</div>' if log.get("detail") else ""}
                </div>""", unsafe_allow_html=True)
            with cols[1]:
                if log["level"] == "error" and log.get("payload") and not log.get("retried"):
                    if st.button("↺", key=f"retry_{i}", use_container_width=True, help="재시도"):
                        token = st.session_state.get("api_token")
                        if token:
                            try:
                                with st.spinner("재전송 중..."):
                                    result = post_transaction(token, log["payload"])
                                log["retried"] = True
                                add_log("success", f"재시도 성공 — 트랜잭션 {result.get('id','')}", source="retry")
                                st.rerun()
                            except Exception as e:
                                add_log("error", f"재시도 실패: {str(e)[:80]}", source="retry")
                                st.rerun()

with inspect_col:
    st.markdown('<div class="section-header">🔍 Payload Inspector</div>', unsafe_allow_html=True)
    payload_logs = [l for l in logs if l.get("payload")]
    if not payload_logs:
        st.markdown('<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:20px;text-align:center;color:#9ca3af;font-size:0.85rem;">전송된 payload가 없습니다.</div>', unsafe_allow_html=True)
    else:
        options = [f"{l['ts']}  {l['message'][:28]}" for l in payload_logs]
        sel = st.selectbox("기록 선택", range(len(options)), format_func=lambda i: options[i],
                           label_visibility="collapsed", key="payload_sel")
        sel_log = payload_logs[sel]
        lvl_badge = {"success":"✅","error":"❌","warning":"⚠️"}.get(sel_log["level"],"ℹ️")
        st.caption(f"{lvl_badge} {sel_log['ts']} · source: {sel_log.get('source','—')}")
        st.json(sel_log["payload"])
