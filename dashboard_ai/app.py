import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import time

st.set_page_config(
    page_title="Smart Water Leak Digital Twin",
    page_icon="💧",
    layout="wide"
)

LANG = {
    "العربية": {
        "dir": "rtl",
        "title": "💧 التوأم الرقمي لنظام كشف وعزل تسربات المياه الذكي (Digital Twin)",
        "subtitle": "**مسابقة الابتكار في تكنولوجيا المياه 2026 | تخصص هندسة الميكاترونيات**",
        "sidebar_title": "🎛️ لوحة التحكم في السيناريو الفيزيائي",
        "sec1": "1. استهلاك الصنابير الطبيعي (Needle Valves)",
        "needle_a": "فتحة صنبور Zone A (%)",
        "needle_b": "فتحة صنبور Zone B (%)",
        "needle_c": "فتحة صنبور Zone C (%)",
        "sec2": "2. محاكاة الأعطال والتسربات (Fault Injection)",
        "leak_a": "🚨 إحداث كسر في أنبوب Zone A",
        "leak_b": "🚨 إحداث كسر في أنبوب Zone B",
        "micro_c": "💧 تسرب بطيء جداً (Micro-leak) في Zone C",
        "leak_main": "💥 تسرب في المشعب الرئيسي (Main Manifold)",
        "reset_btn": "🔄 إعادة ضبط النظام وفتح الصمامات (Reset)",
        "alert_main": "💥 إنذار حرج: تسرب في المشعب الرئيسي (Main Manifold)! تم عزل جميع الفروع.",
        "alert_zone": "🚨 كسر وتسرب مكتشف في Zone {z} (هبوط نسبة الضغط إلى {r:.2f}) ← تم إغلاق الصمام {z} تلقائياً!",
        "alert_micro": "💧 تسرب دقيق (Micro-Leak) مكتشف في Zone {z} عبر تحليل هبوط الضغط في منطقة العمى ← تم إغلاق الصمام {z}!",
        "status_ok": "✅ النظام مستقر: جميع قراءات الضغط والتدفق ضمن النطاق الطبيعي.",
        "kpi_tank": "منسوب الخزان العلوي",
        "kpi_press": "الضغط",
        "kpi_main_q": "التدفق الرئيسي (YF-S201)",
        "open": "مفتوح 🟢",
        "closed": "مغلق 🔴",
        "chart_p_title": "مراقبة الضغط اللحظي (kPa) - التعويض الديناميكي",
        "chart_p_y": "الضغط (kPa)",
        "chart_q_title": "معدل التدفق (L/min) - موازنة الشبكة",
        "chart_q_y": "التدفق (L/min)",
        "p_ref_label": "P_Main (المرجع)",
        "q_main_label": "التدفق الرئيسي"
    },
    "English": {
        "dir": "ltr",
        "title": "💧 Smart Water Leak Detection & Isolation Digital Twin",
        "subtitle": "**Water Technology Innovation Competition 2026 | Mechatronics Engineering**",
        "sidebar_title": "🎛️ Physical Scenario Control Panel",
        "sec1": "1. Normal Consumption (Needle Valves)",
        "needle_a": "Zone A Faucet Opening (%)",
        "needle_b": "Zone B Faucet Opening (%)",
        "needle_c": "Zone C Faucet Opening (%)",
        "sec2": "2. Fault & Leak Injection",
        "leak_a": "🚨 Trigger Pipe Burst in Zone A",
        "leak_b": "🚨 Trigger Pipe Burst in Zone B",
        "micro_c": "💧 Trigger Micro-Leak in Zone C",
        "leak_main": "💥 Trigger Main Manifold Leak",
        "reset_btn": "🔄 Reset System & Re-open Valves",
        "alert_main": "💥 CRITICAL ALERT: Main Manifold Leak Detected! All zones isolated.",
        "alert_zone": "🚨 LEAK DETECTED in Zone {z} (Pressure Ratio dropped to {r:.2f}) -> Valve {z} CLOSED automatically!",
        "alert_micro": "💧 MICRO-LEAK DETECTED in Zone {z} via Dead-band Pressure Analysis -> Valve {z} CLOSED!",
        "status_ok": "✅ System Normal: All pressure and flow readings are within expected thresholds.",
        "kpi_tank": "Top Tank Water Level",
        "kpi_press": "Pressure",
        "kpi_main_q": "Main Flow (YF-S201)",
        "open": "OPEN 🟢",
        "closed": "CLOSED 🔴",
        "chart_p_title": "Real-Time Pressure Monitoring (kPa) - Dynamic Compensation",
        "chart_p_y": "Pressure (kPa)",
        "chart_q_title": "Flow Rate Balance (L/min) - Mass Conservation",
        "chart_q_y": "Flow Rate (L/min)",
        "p_ref_label": "P_Main (Reference)",
        "q_main_label": "Main Flow"
    }
}

selected_lang = st.sidebar.radio("🌐 Language / اللغة", ["العربية", "English"], horizontal=True)
T = LANG[selected_lang]

st.markdown(f"""
    <style>
        .main .block-container {{ direction: {T['dir']}; text-align: {'right' if T['dir']=='rtl' else 'left'}; }}
    </style>
""", unsafe_allow_html=True)

st.title(T["title"])
st.markdown(T["subtitle"])

if "water_volume" not in st.session_state:
    st.session_state.water_volume = 14.5
if "valves" not in st.session_state:
    st.session_state.valves = {"A": True, "B": True, "C": True}
if "history" not in st.session_state:
    st.session_state.history = pd.DataFrame(columns=["Time", "P_Main", "P_A", "P_B", "P_C", "Q_Main", "Q_A", "Q_B", "Q_C"])
if "step" not in st.session_state:
    st.session_state.step = 0

st.sidebar.header(T["sidebar_title"])

st.sidebar.subheader(T["sec1"])
needle_a = st.sidebar.slider(T["needle_a"], 0, 100, 40)
needle_b = st.sidebar.slider(T["needle_b"], 0, 100, 30)
needle_c = st.sidebar.slider(T["needle_c"], 0, 100, 0)

st.sidebar.subheader(T["sec2"])
leak_zone_a = st.sidebar.checkbox(T["leak_a"], value=False)
leak_zone_b = st.sidebar.checkbox(T["leak_b"], value=False)
micro_leak_c = st.sidebar.checkbox(T["micro_c"], value=False)
manifold_leak = st.sidebar.checkbox(T["leak_main"], value=False)

st.sidebar.divider()
if st.sidebar.button(T["reset_btn"]):
    st.session_state.water_volume = 14.5
    st.session_state.valves = {"A": True, "B": True, "C": True}
    st.session_state.history = pd.DataFrame(columns=["Time", "P_Main", "P_A", "P_B", "P_C", "Q_Main", "Q_A", "Q_B", "Q_C"])
    st.session_state.step = 0
    st.rerun()

tank_water_height = (st.session_state.water_volume * 1000.0) / 750.0 / 100.0
total_head = 1.50 + tank_water_height
p_static = (1000.0 * 9.81 * total_head) / 1000.0

noise = lambda scale=0.08: np.random.normal(0, scale)

def calc_zone_physics(valve_open, needle_pct, is_burst, is_micro):
    if not valve_open:
        return 0.0, 0.0
    q_normal = (needle_pct / 100.0) * 1.8
    q_leak = 2.8 if is_burst else (0.22 if is_micro else 0.0)
    q_total = q_normal + q_leak
    q_measured = 0.0 if q_total < 0.3 else q_total + noise(0.03)
    return q_total, max(0.0, q_measured)

q_true_a, q_a = calc_zone_physics(st.session_state.valves["A"], needle_a, leak_zone_a, False)
q_true_b, q_b = calc_zone_physics(st.session_state.valves["B"], needle_b, leak_zone_b, False)
q_true_c, q_c = calc_zone_physics(st.session_state.valves["C"], needle_c, False, micro_leak_c)

q_manifold_leak = 3.2 if manifold_leak else 0.0
q_true_main = q_true_a + q_true_b + q_true_c + q_manifold_leak
q_main = 0.0 if q_true_main < 1.0 else q_true_main + noise(0.05)

p_main = max(0.5, p_static - (q_true_main * 0.45) - (4.5 if manifold_leak else 0.0) + noise())
p_a = 0.0 if not st.session_state.valves["A"] else max(0.2, p_main * 0.90 - (q_true_a * 0.6) - (5.5 if leak_zone_a else 0.0) + noise())
p_b = 0.0 if not st.session_state.valves["B"] else max(0.2, p_main * 0.90 - (q_true_b * 0.6) - (5.5 if leak_zone_b else 0.0) + noise())
p_c = 0.0 if not st.session_state.valves["C"] else max(0.2, p_main * 0.90 - (q_true_c * 0.6) - (1.8 if micro_leak_c else 0.0) + noise())

alerts = []

if (p_main < p_static * 0.78) or (abs(q_main - (q_a + q_b + q_c)) > 0.6 and q_main > 1.0):
    alerts.append(T["alert_main"])
    st.session_state.valves = {"A": False, "B": False, "C": False}

for z_name, p_z, q_z, n_pct in [("A", p_a, q_a, needle_a), ("B", p_b, q_b, needle_b), ("C", p_c, q_c, needle_c)]:
    if st.session_state.valves[z_name]:
        ratio = p_z / p_main if p_main > 0 else 1.0
        if ratio < 0.58:
            alerts.append(T["alert_zone"].format(z=z_name, r=ratio))
            st.session_state.valves[z_name] = False
        elif q_z == 0.0 and n_pct == 0 and p_z < p_main * 0.80:
            alerts.append(T["alert_micro"].format(z=z_name))
            st.session_state.valves[z_name] = False

if alerts:
    for msg in alerts:
        st.error(msg)
else:
    st.success(T["status_ok"])

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric(T["kpi_tank"], f"{st.session_state.water_volume:.2f} L", f"{T['kpi_press']}: {p_main:.2f} kPa")
col2.metric(T["kpi_main_q"], f"{q_main:.2f} L/min")
col3.metric("Zone A (YF-S401)", f"{q_a:.2f} L/min", f"{T['kpi_press']}: {p_a:.2f} kPa | {T['open'] if st.session_state.valves['A'] else T['closed']}")
col4.metric("Zone B (YF-S401)", f"{q_b:.2f} L/min", f"{T['kpi_press']}: {p_b:.2f} kPa | {T['open'] if st.session_state.valves['B'] else T['closed']}")
col5.metric("Zone C (YF-S401)", f"{q_c:.2f} L/min", f"{T['kpi_press']}: {p_c:.2f} kPa | {T['open'] if st.session_state.valves['C'] else T['closed']}")

st.session_state.step += 1
st.session_state.water_volume = max(1.0, st.session_state.water_volume - (q_true_main / 60.0))

new_row = pd.DataFrame([{
    "Time": st.session_state.step,
    "P_Main": p_main, "P_A": p_a, "P_B": p_b, "P_C": p_c,
    "Q_Main": q_main, "Q_A": q_a, "Q_B": q_b, "Q_C": q_c
}])
st.session_state.history = pd.concat([st.session_state.history, new_row], ignore_index=True).tail(40)

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["P_Main"], name=T["p_ref_label"], line=dict(width=3, dash='dash')))
    fig_p.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["P_A"], name="Zone A"))
    fig_p.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["P_B"], name="Zone B"))
    fig_p.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["P_C"], name="Zone C"))
    fig_p.update_layout(title=T["chart_p_title"], yaxis_title=T["chart_p_y"], height=350)
    st.plotly_chart(fig_p, use_container_width=True)

with chart_col2:
    fig_q = go.Figure()
    fig_q.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["Q_Main"], name=T["q_main_label"], line=dict(width=3)))
    fig_q.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["Q_A"], name="Zone A"))
    fig_q.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["Q_B"], name="Zone B"))
    fig_q.add_trace(go.Scatter(x=st.session_state.history["Time"], y=st.session_state.history["Q_C"], name="Zone C"))
    fig_q.update_layout(title=T["chart_q_title"], yaxis_title=T["chart_q_y"], height=350)
    st.plotly_chart(fig_q, use_container_width=True)

time.sleep(1)
st.rerun()

