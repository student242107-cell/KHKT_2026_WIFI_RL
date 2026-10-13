import streamlit as st
import pandas as pd
import time
import os
import plotly.express as px

# ==========================================
# 1. CẤU HÌNH GIAO DIỆN (UI CONFIG)
# ==========================================
st.set_page_config(
    page_title="Cognitive Wi-Fi | KHKT 2026", 
    page_icon="📡", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Thêm CSS custom để giao diện chuyên nghiệp hơn
st.markdown("""
    <style>
    .metric-card {background-color: #f8f9fa; padding: 15px; border-radius: 10px; border-left: 5px solid #007bff; box-shadow: 2px 2px 5px rgba(0,0,0,0.05);}
    .st-emotion-cache-1v0mbdj {margin-top: -50px;}
    </style>
""", unsafe_allow_html=True)

st.title("📡 WiFiSON_RL: TRẠM ĐIỀU HÀNH")
st.markdown("*Hệ thống Tối ưu hóa Tài nguyên Vô tuyến Thời gian thực (Reinforcement Learning)*")
st.markdown("---")

# ==========================================
# 2. HÀM TẢI DỮ LIỆU TELEMETRY (CSV)
# ==========================================
CSV_PATH = "data/processed/telemetry_history.csv"

@st.cache_data(ttl=5) # Cache tự động hết hạn sau 5 giây để ép reload
def load_data():
    if not os.path.exists(CSV_PATH):
        return pd.DataFrame()
    try:
        # Đọc 200 dòng mới nhất để biểu đồ chạy nhanh
        df = pd.read_csv(CSV_PATH)
        df = df.tail(200) 
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
        return df
    except Exception as e:
        st.error(f"Lỗi đọc dữ liệu: {e}")
        return pd.DataFrame()

df = load_data()

# ==========================================
# 3. LUỒNG HIỂN THỊ CHÍNH (MAIN DASHBOARD)
# ==========================================
st.sidebar.header("⚙️ CẤU HÌNH GIÁM SÁT")
auto_refresh = st.sidebar.checkbox("Bật Auto-Refresh (5s)", value=True)

if df.empty:
    st.warning("⏳ Đang chờ dữ liệu từ Cảm biến Telemetry... Vui lòng bật `telemetry.py`")
else:
    # --- BỘ LỌC SIDEBAR ---
    list_aps = df['AP_MAC'].unique()
    selected_ap = st.sidebar.selectbox("Lựa chọn Trạm phát sóng (AP):", list_aps)
    
    st.sidebar.markdown("---")
    st.sidebar.info("🧠 AI Status: **HIL Agent Active**\n\n🛡️ Fail-Safe: **Armed**")

    # Lọc data theo AP đã chọn
    df_ap = df[df['AP_MAC'] == selected_ap].copy()
    latest_data = df_ap.iloc[-1] # Dòng dữ liệu mới nhất (hiện tại)

    # --- HÀNG 1: THẺ CHỈ SỐ NHANH (METRIC CARDS) ---
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(label="👥 Thiết bị (Clients)", value=int(latest_data['sta_count']), 
                  delta=f"Tại {latest_data['Hour']}h{latest_data['Minute']}p")
    with col2:
        pc_percent = float(latest_data['p_c']) * 100
        st.metric(label="⚠️ Tỷ lệ Va chạm (p_c)", value=f"{pc_percent:.1f}%", 
                  delta="- Cảnh báo" if pc_percent > 15 else "An toàn", 
                  delta_color="inverse")
    with col3:
        airtime = float(latest_data['airtime_util']) * 100
        st.metric(label="⏱️ Chiếm dụng Airtime", value=f"{airtime:.1f}%", 
                  delta="Nghẽn" if airtime > 80 else "Ổn định", delta_color="inverse")
    with col4:
        st.metric(label="📶 Tín hiệu trung bình (RSSI)", value=f"{latest_data['avg_rssi']} dBm")

    st.markdown("---")

    # --- HÀNG 2: BIỂU ĐỒ REAL-TIME THEO DÕI AI (PLOTLY) ---
    st.subheader("📈 Giám sát Chất lượng Mạng Thời gian thực")
    
    fig_col1, fig_col2 = st.columns(2)
    
    with fig_col1:
        # Biểu đồ 1: Sự thay đổi của p_c và Retry Rate
        fig_pc = px.line(df_ap, x='Timestamp', y=['p_c', 'retry_rate'], 
                         title=f"Độ trễ và Đụng độ Tầng MAC ({selected_ap})",
                         labels={'value': 'Xác suất (0 - 1.0)', 'variable': 'Metric'},
                         color_discrete_map={'p_c': 'red', 'retry_rate': 'orange'})
        fig_pc.update_traces(line=dict(width=3))
        # Kẻ vạch đỏ nguy hiểm
        fig_pc.add_hline(y=0.15, line_dash="dot", annotation_text="Ngưỡng AI can thiệp (15%)", annotation_position="top right")
        st.plotly_chart(fig_pc, use_container_width=True)
        
    with fig_col2:
        # Biểu đồ 2: Airtime và Số lượng User
        fig_traffic = px.area(df_ap, x='Timestamp', y='sta_count', 
                              title=f"Lưu lượng Thiết bị kết nối ({selected_ap})",
                              labels={'sta_count': 'Số lượng thiết bị'},
                              color_discrete_sequence=['#007bff'])
        st.plotly_chart(fig_traffic, use_container_width=True)

# ==========================================
# 4. CHẾ ĐỘ AUTO-REFRESH LẶP LẠI (TIME LOOP)
# ==========================================
if not df.empty and auto_refresh:
    time.sleep(5) # Đợi 5 giây
    st.rerun()    # Vẽ lại toàn bộ trang web