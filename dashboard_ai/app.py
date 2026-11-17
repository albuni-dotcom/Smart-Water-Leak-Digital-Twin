from anomaly import detect_anomaly, reset_detector

import json
import queue
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False


st.set_page_config(
    page_title="Smart Water Leak Detection",
    page_icon="💧",
    layout="wide"
)

LANG = {
    "العربية": {
        "title": "💧 نظام كشف وعزل تسربات المياه الذكي",
        "subtitle": "لوحة المراقبة والتحكم في شبكة المياه",
        "language": "اللغة",
        "mode": "وضع التشغيل",
        "simulation": "المحاكاة",
        "hardware": "الاتصال بالنظام الحقيقي",
        "sidebar": "لوحة التحكم",
        "connection": "إعدادات الاتصال",
        "broker": "عنوان MQTT Broker",
        "port": "منفذ MQTT",
        "username": "اسم المستخدم",
        "password": "كلمة المرور",
        "connect": "اتصال",
        "disconnect": "قطع الاتصال",
        "connected": "متصل",
        "disconnected": "غير متصل",
        "mqtt_missing": "مكتبة paho-mqtt غير مثبتة",
        "simulation_controls": "إعدادات المحاكاة",
        "needle": "معدل فتح صنبور المنطقة (%)",
        "leaks": "محاكاة الأعطال",
        "leak_a": "كسر في أنبوب المنطقة A",
        "leak_b": "كسر في أنبوب المنطقة B",
        "micro_c": "تسرب دقيق في المنطقة C",
        "main_leak": "تسرب في المشعب الرئيسي",
        "hardware_controls": "التحكم بالصمامات",
        "open": "فتح",
        "close": "إغلاق",
        "open_all": "فتح جميع الصمامات",
        "close_all": "إغلاق جميع الصمامات",
        "reset": "إعادة ضبط الإنذار",
        "status": "حالة النظام",
        "normal": "النظام يعمل بصورة طبيعية",
        "alarm": "يوجد إنذار تسرب",
        "tank": "حجم المياه الافتراضي",
        "pressure": "الضغط الرئيسي",
        "main_flow": "التدفق الرئيسي",
        "zone_a": "المنطقة A",
        "zone_b": "المنطقة B",
        "zone_c": "المنطقة C",
        "flow": "التدفق",
        "valve": "الصمام",
        "opened": "مفتوح",
        "closed": "مغلق",
        "pressure_chart": "مراقبة الضغط",
        "flow_chart": "مراقبة التدفق",
        "pressure_axis": "الضغط (kPa)",
        "flow_axis": "التدفق (L/min)",
        "time": "الزمن",
        "no_data": "لا توجد بيانات بعد",
        "mqtt_error": "تعذر الاتصال بوسيط MQTT",
        "hardware_warning": "تأكد من تشغيل ESP32 ووسيط MQTT قبل إرسال الأوامر",
        "telemetry_wait": "في انتظار بيانات ESP32...",
        "last_update": "آخر تحديث",
        "alarm_type": "نوع الإنذار",
        "sensor_status": "حالة الحساسات",
        "ready": "جاهزة",
        "not_ready": "غير جاهزة",
        "command_sent": "تم إرسال الأمر",
        "command_failed": "فشل إرسال الأمر",
        "simulation_note": "هذا الوضع محاكاة برمجية ولا يتحكم في الصمامات الحقيقية",
        "hardware_note": "الأوامر في هذا الوضع تُرسل إلى ESP32 عبر MQTT",
        "water_consumption": "استهلاك المياه في المحاكاة",
        "reset_sim": "إعادة تشغيل المحاكاة",
        "mqtt_topics": "موضوعات MQTT",
        "telemetry_topic": "موضوع البيانات",
        "command_topic": "موضوع الأوامر",
        "status_topic": "موضوع الحالة",
        "zone_status": "حالة المناطق",
        "main_status": "حالة الخط الرئيسي"
    },
    "English": {
        "title": "💧 Smart Water Leak Detection System",
        "subtitle": "Water Network Monitoring and Control Dashboard",
        "language": "Language",
        "mode": "Operating Mode",
        "simulation": "Simulation",
        "hardware": "Real Hardware Connection",
        "sidebar": "Control Panel",
        "connection": "Connection Settings",
        "broker": "MQTT Broker Address",
        "port": "MQTT Port",
        "username": "Username",
        "password": "Password",
        "connect": "Connect",
        "disconnect": "Disconnect",
        "connected": "Connected",
        "disconnected": "Disconnected",
        "mqtt_missing": "paho-mqtt library is not installed",
        "simulation_controls": "Simulation Settings",
        "needle": "Zone Faucet Opening (%)",
        "leaks": "Fault Simulation",
        "leak_a": "Pipe Burst in Zone A",
        "leak_b": "Pipe Burst in Zone B",
        "micro_c": "Micro-Leak in Zone C",
        "main_leak": "Main Manifold Leak",
        "hardware_controls": "Valve Controls",
        "open": "Open",
        "close": "Close",
        "open_all": "Open All Valves",
        "close_all": "Close All Valves",
        "reset": "Reset Alarm",
        "status": "System Status",
        "normal": "System Operating Normally",
        "alarm": "Leak Alarm Detected",
        "tank": "Simulated Water Volume",
        "pressure": "Main Pressure",
        "main_flow": "Main Flow",
        "zone_a": "Zone A",
        "zone_b": "Zone B",
        "zone_c": "Zone C",
        "flow": "Flow",
        "valve": "Valve",
        "opened": "OPEN",
        "closed": "CLOSED",
        "pressure_chart": "Pressure Monitoring",
        "flow_chart": "Flow Monitoring",
        "pressure_axis": "Pressure (kPa)",
        "flow_axis": "Flow Rate (L/min)",
        "time": "Time",
        "no_data": "No data available yet",
        "mqtt_error": "Unable to connect to MQTT broker",
        "hardware_warning": "Make sure ESP32 and MQTT broker are running before sending commands",
        "telemetry_wait": "Waiting for ESP32 telemetry...",
        "last_update": "Last Update",
        "alarm_type": "Alarm Type",
        "sensor_status": "Sensor Status",
        "ready": "Ready",
        "not_ready": "Not Ready",
        "command_sent": "Command sent",
        "command_failed": "Command failed",
        "simulation_note": "This is a software simulation and does not control physical valves",
        "hardware_note": "Commands in this mode are sent to ESP32 through MQTT",
        "water_consumption": "Simulated Water Consumption",
        "reset_sim": "Restart Simulation",
        "mqtt_topics": "MQTT Topics",
        "telemetry_topic": "Telemetry Topic",
        "command_topic": "Command Topic",
        "status_topic": "Status Topic",
        "zone_status": "Zone Status",
        "main_status": "Main Line Status"
    }
}


if "history" not in st.session_state:
    st.session_state.history = []

if "water_volume" not in st.session_state:
    st.session_state.water_volume = 14.5

if "sim_valves" not in st.session_state:
    st.session_state.sim_valves = {
        "A": True,
        "B": True,
        "C": True
    }

if "sim_alarm" not in st.session_state:
    st.session_state.sim_alarm = False

if "sim_alarm_type" not in st.session_state:
    st.session_state.sim_alarm_type = "NONE"

if "mqtt_client" not in st.session_state:
    st.session_state.mqtt_client = None

if "mqtt_connected" not in st.session_state:
    st.session_state.mqtt_connected = False

if "mqtt_data" not in st.session_state:
    st.session_state.mqtt_data = {}

if "mqtt_status" not in st.session_state:
    st.session_state.mqtt_status = "DISCONNECTED"

if "last_sim_time" not in st.session_state:
    st.session_state.last_sim_time = time.time()

if "last_mqtt_message" not in st.session_state:
    st.session_state.last_mqtt_message = None

if "last_command_result" not in st.session_state:
    st.session_state.last_command_result = None

if "mqtt_events" not in st.session_state:
    st.session_state.mqtt_events = queue.Queue(maxsize=200)

if "last_telemetry_signature" not in st.session_state:
    st.session_state.last_telemetry_signature = None

if "anomaly_result" not in st.session_state:
    st.session_state.anomaly_result = None

if "last_mqtt_timestamp" not in st.session_state:
    st.session_state.last_mqtt_timestamp = None


selected_lang = st.sidebar.radio(
    "🌐 Language / اللغة",
    ["العربية", "English"],
    horizontal=True
)

T = LANG[selected_lang]
rtl = selected_lang == "العربية"

st.markdown(
    f"""
    <style>
    .main .block-container {{
        direction: {"rtl" if rtl else "ltr"};
        text-align: {"right" if rtl else "left"};
    }}
    div[data-testid="stMetric"] {{
        text-align: {"right" if rtl else "left"};
    }}
    </style>
    """,
    unsafe_allow_html=True
)

st.title(T["title"])
st.markdown(T["subtitle"])

mode = st.sidebar.radio(
    T["mode"],
    [T["simulation"], T["hardware"]],
    index=0
)

simulation_mode = mode == T["simulation"]

COMMAND_TOPIC = "smartwater/device/cmd"
TELEMETRY_TOPIC = "smartwater/device/telemetry"
STATUS_TOPIC = "smartwater/device/status"


def on_connect(client, userdata, flags, rc):
    events = userdata
    if rc == 0:
        client.subscribe(TELEMETRY_TOPIC, qos=0)
        client.subscribe(STATUS_TOPIC, qos=0)
        events.put(("connect", 0))
    else:
        events.put(("connect", int(rc)))


def on_disconnect(client, userdata, rc):
    userdata.put(("disconnect", int(rc)))


def on_message(client, userdata, message):
    try:
        payload = message.payload.decode("utf-8")
        userdata.put(("message", message.topic, payload))
    except UnicodeDecodeError:
        userdata.put(("invalid",))


def process_mqtt_events():
    events = st.session_state.mqtt_events

    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            break

        if event[0] == "connect":
            connected = event[1] == 0
            st.session_state.mqtt_connected = connected
            st.session_state.mqtt_status = (
                "CONNECTED" if connected else "CONNECTION_FAILED"
            )
        elif event[0] == "disconnect":
            st.session_state.mqtt_connected = False
            st.session_state.mqtt_status = "DISCONNECTED"
        elif event[0] == "invalid":
            st.session_state.last_mqtt_message = "INVALID_MESSAGE"
        elif event[0] == "message":
            _, topic, payload = event
            if topic == TELEMETRY_TOPIC:
                try:
                    data = json.loads(payload)
                    if not isinstance(data, dict):
                        raise ValueError("Telemetry must be a JSON object")

                    signature = json.dumps(data, sort_keys=True)
                    st.session_state.mqtt_data = data
                    st.session_state.last_mqtt_message = datetime.now().strftime(
                        "%H:%M:%S"
                    )
                    st.session_state.last_mqtt_timestamp = datetime.now()

                    if signature != st.session_state.last_telemetry_signature:
                        st.session_state.last_telemetry_signature = signature
                        append_history(data)
                        try:
                            st.session_state.anomaly_result = detect_anomaly(data)
                        except (ValueError, TypeError, KeyError):
                            st.session_state.anomaly_result = None
                except (json.JSONDecodeError, ValueError, TypeError):
                    st.session_state.last_mqtt_message = "INVALID_MESSAGE"
            elif topic == STATUS_TOPIC:
                st.session_state.mqtt_status = payload


def connect_mqtt(host, port, username, password):
    if not MQTT_AVAILABLE:
        st.session_state.mqtt_status = "MISSING_LIBRARY"
        return False

    try:
        disconnect_mqtt()

        client = mqtt.Client(
            client_id=f"smartwater_dashboard_{int(time.time())}"
        )

        if username:
            client.username_pw_set(username, password)

        client.user_data_set(st.session_state.mqtt_events)
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        client.connect(host, int(port), keepalive=30)
        client.loop_start()

        st.session_state.mqtt_client = client
        st.session_state.mqtt_status = "CONNECTING"

        return True

    except Exception as exc:
        st.session_state.mqtt_connected = False
        st.session_state.mqtt_status = str(exc)
        st.session_state.mqtt_client = None
        return False


def disconnect_mqtt():
    client = st.session_state.get("mqtt_client")

    if client is not None:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    st.session_state.mqtt_client = None
    st.session_state.mqtt_connected = False
    st.session_state.mqtt_status = "DISCONNECTED"
    while not st.session_state.mqtt_events.empty():
        try:
            st.session_state.mqtt_events.get_nowait()
        except queue.Empty:
            break


def send_mqtt_command(command):
    client = st.session_state.get("mqtt_client")

    if client is None or not st.session_state.mqtt_connected:
        st.session_state.last_command_result = "FAILED"
        return False

    try:
        payload = json.dumps(command)
        result = client.publish(COMMAND_TOPIC, payload, qos=1)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            st.session_state.last_command_result = "SENT"
            return True

        st.session_state.last_command_result = "FAILED"
        return False

    except Exception:
        st.session_state.last_command_result = "FAILED"
        return False


def append_history(data):
    row = {
        "Time": datetime.now().strftime("%H:%M:%S"),
        "P_Main": data.get("P_main", 0.0),
        "P_A": data.get("ZA", {}).get("P", 0.0),
        "P_B": data.get("ZB", {}).get("P", 0.0),
        "P_C": data.get("ZC", {}).get("P", 0.0),
        "Q_Main": data.get("Q_main", 0.0),
        "Q_A": data.get("ZA", {}).get("Q", 0.0),
        "Q_B": data.get("ZB", {}).get("Q", 0.0),
        "Q_C": data.get("ZC", {}).get("Q", 0.0)
    }

    st.session_state.history.append(row)
    st.session_state.history = st.session_state.history[-60:]


def add_simulation_history(data):
    row = {
        "Time": datetime.now().strftime("%H:%M:%S"),
        "P_Main": data["P_main"],
        "P_A": data["ZA"]["P"],
        "P_B": data["ZB"]["P"],
        "P_C": data["ZC"]["P"],
        "Q_Main": data["Q_main"],
        "Q_A": data["ZA"]["Q"],
        "Q_B": data["ZB"]["Q"],
        "Q_C": data["ZC"]["Q"]
    }

    st.session_state.history.append(row)
    st.session_state.history = st.session_state.history[-60:]


def calculate_zone(valve_open, needle_percent, burst, micro):
    if not valve_open:
        return 0.0, 0.0

    normal_flow = (needle_percent / 100.0) * 1.8

    if burst:
        leak_flow = 2.8
    elif micro:
        leak_flow = 0.22
    else:
        leak_flow = 0.0

    true_flow = normal_flow + leak_flow
    measured_flow = 0.0 if true_flow < 0.3 else max(
        0.0,
        true_flow + np.random.normal(0, 0.03)
    )

    return true_flow, measured_flow


def run_simulation(
    needle_a,
    needle_b,
    needle_c,
    leak_a,
    leak_b,
    micro_c,
    manifold_leak
):
    valves = st.session_state.sim_valves

    q_true_a, q_a = calculate_zone(
        valves["A"], needle_a, leak_a, False
    )

    q_true_b, q_b = calculate_zone(
        valves["B"], needle_b, leak_b, False
    )

    q_true_c, q_c = calculate_zone(
        valves["C"], needle_c, False, micro_c
    )

    q_manifold = 3.2 if manifold_leak else 0.0

    q_true_main = q_true_a + q_true_b + q_true_c + q_manifold

    q_main = 0.0 if q_true_main < 1.0 else max(
        0.0,
        q_true_main + np.random.normal(0, 0.05)
    )

    tank_height = (
        st.session_state.water_volume * 1000.0
    ) / 750.0 / 100.0

    total_head = 1.50 + tank_height
    p_static = (1000.0 * 9.81 * total_head) / 1000.0

    p_main = max(
        0.5,
        p_static
        - q_true_main * 0.45
        - (4.5 if manifold_leak else 0.0)
        + np.random.normal(0, 0.08)
    )

    p_a = 0.0 if not valves["A"] else max(
        0.2,
        p_main * 0.90
        - q_true_a * 0.6
        - (5.5 if leak_a else 0.0)
        + np.random.normal(0, 0.08)
    )

    p_b = 0.0 if not valves["B"] else max(
        0.2,
        p_main * 0.90
        - q_true_b * 0.6
        - (5.5 if leak_b else 0.0)
        + np.random.normal(0, 0.08)
    )

    p_c = 0.0 if not valves["C"] else max(
        0.2,
        p_main * 0.90
        - q_true_c * 0.6
        - (1.8 if micro_c else 0.0)
        + np.random.normal(0, 0.08)
    )

    alarms = []

    main_pressure_drop = p_main < p_static * 0.78

    main_flow_difference = (
        abs(q_main - (q_a + q_b + q_c)) > 0.6
        and q_main > 1.0
    )

    if main_pressure_drop or main_flow_difference:
        alarms.append("MAIN_MANIFOLD_LEAK")
        st.session_state.sim_valves = {
            "A": False,
            "B": False,
            "C": False
        }

    zone_values = [
        ("A", p_a, q_a, needle_a),
        ("B", p_b, q_b, needle_b),
        ("C", p_c, q_c, needle_c)
    ]

    for zone, pressure, flow, needle in zone_values:
        if not st.session_state.sim_valves[zone]:
            continue

        ratio = pressure / p_main if p_main > 0 else 1.0

        if ratio < 0.58:
            alarms.append(f"ZONE_{zone}_LEAK")
            st.session_state.sim_valves[zone] = False

        elif flow == 0.0 and needle == 0 and pressure < p_main * 0.80:
            alarms.append(f"MICRO_LEAK_{zone}")
            st.session_state.sim_valves[zone] = False

    if alarms:
        st.session_state.sim_alarm = True
        st.session_state.sim_alarm_type = alarms[0]

    elapsed = max(
        0.0,
        min(time.time() - st.session_state.last_sim_time, 5.0)
    )

    st.session_state.last_sim_time = time.time()

    st.session_state.water_volume = max(
        0.0,
        st.session_state.water_volume
        - (q_true_main / 60.0) * elapsed
    )

    return {
        "P_main": p_main,
        "Q_main": q_main,
        "ZA": {
            "P": p_a,
            "Q": q_a,
            "V": int(st.session_state.sim_valves["A"])
        },
        "ZB": {
            "P": p_b,
            "Q": q_b,
            "V": int(st.session_state.sim_valves["B"])
        },
        "ZC": {
            "P": p_c,
            "Q": q_c,
            "V": int(st.session_state.sim_valves["C"])
        },
        "alarm": st.session_state.sim_alarm,
        "alarm_type": st.session_state.sim_alarm_type,
        "sensor_ready": True
    }


def draw_charts(history, T):
    if not history:
        st.info(T["no_data"])
        return

    df = pd.DataFrame(history)

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        fig_pressure = go.Figure()

        for column, name in [
            ("P_Main", "Main"),
            ("P_A", "Zone A"),
            ("P_B", "Zone B"),
            ("P_C", "Zone C")
        ]:
            fig_pressure.add_trace(
                go.Scatter(
                    x=df["Time"],
                    y=df[column],
                    name=name,
                    mode="lines+markers"
                )
            )

        fig_pressure.update_layout(
            title=T["pressure_chart"],
            yaxis_title=T["pressure_axis"],
            xaxis_title=T["time"],
            height=370,
            margin=dict(l=15, r=15, t=50, b=15)
        )

        st.plotly_chart(
            fig_pressure,
            use_container_width=True
        )

    with chart_col2:
        fig_flow = go.Figure()

        for column, name in [
            ("Q_Main", "Main"),
            ("Q_A", "Zone A"),
            ("Q_B", "Zone B"),
            ("Q_C", "Zone C")
        ]:
            fig_flow.add_trace(
                go.Scatter(
                    x=df["Time"],
                    y=df[column],
                    name=name,
                    mode="lines+markers"
                )
            )

        fig_flow.update_layout(
            title=T["flow_chart"],
            yaxis_title=T["flow_axis"],
            xaxis_title=T["time"],
            height=370,
            margin=dict(l=15, r=15, t=50, b=15)
        )

        st.plotly_chart(
            fig_flow,
            use_container_width=True
        )


def display_dashboard(data, T, hardware=False):
    zone_a = data.get("ZA", {})
    zone_b = data.get("ZB", {})
    zone_c = data.get("ZC", {})

    p_main = float(data.get("P_main", 0.0))
    q_main = float(data.get("Q_main", 0.0))

    p_a = float(zone_a.get("P", 0.0))
    p_b = float(zone_b.get("P", 0.0))
    p_c = float(zone_c.get("P", 0.0))

    q_a = float(zone_a.get("Q", 0.0))
    q_b = float(zone_b.get("Q", 0.0))
    q_c = float(zone_c.get("Q", 0.0))

    v_a = bool(zone_a.get("V", 0))
    v_b = bool(zone_b.get("V", 0))
    v_c = bool(zone_c.get("V", 0))

    alarm = bool(data.get("alarm", False))
    alarm_type = data.get("alarm_type", "NONE")

    if alarm:
        st.error(f"{T['alarm']} — {alarm_type}")
    else:
        st.success(T["normal"])

    if hardware:
        tank_value = "N/A"
        tank_delta = None
    else:
        tank_value = f"{st.session_state.water_volume:.2f} L"
        tank_delta = T["water_consumption"]

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(
        T["tank"],
        tank_value
    )

    c2.metric(
        T["pressure"],
        f"{p_main:.2f} kPa"
    )

    c3.metric(
        T["main_flow"],
        f"{q_main:.2f} L/min"
    )

    c4.metric(
        T["zone_a"],
        f"{q_a:.2f} L/min",
        f"{p_a:.2f} kPa"
    )

    c5.metric(
        T["zone_b"],
        f"{q_b:.2f} L/min",
        f"{p_b:.2f} kPa"
    )

    st.subheader(T["zone_status"])

    zone_cols = st.columns(3)

    zone_data = [
        (T["zone_a"], p_a, q_a, v_a),
        (T["zone_b"], p_b, q_b, v_b),
        (T["zone_c"], p_c, q_c, v_c)
    ]

    for col, (name, pressure, flow, valve) in zip(
        zone_cols,
        zone_data
    ):
        with col:
            st.metric(
                name,
                f"{flow:.2f} L/min",
                f"{pressure:.2f} kPa"
            )

            if valve:
                st.success(f"{T['valve']}: {T['opened']}")
            else:
                st.error(f"{T['valve']}: {T['closed']}")

    if hardware:
        st.caption(
            f"{T['last_update']}: "
            f"{st.session_state.last_mqtt_message or T['telemetry_wait']}"
        )

        sensor_ready = data.get("sensor_ready", False)

        if sensor_ready:
            st.success(T["sensor_status"] + ": " + T["ready"])
        else:
            st.error(T["sensor_status"] + ": " + T["not_ready"])

        if st.session_state.last_mqtt_timestamp is not None:
            age = (datetime.now() - st.session_state.last_mqtt_timestamp).total_seconds()
            if age > 5:
                st.warning("Telemetry is stale: no fresh ESP32 data received for more than 5 seconds.")

        anomaly = st.session_state.anomaly_result
        if anomaly and anomaly.get("anomaly"):
            st.warning(
                "Dashboard anomaly analysis (" + anomaly.get("severity", "UNKNOWN") + "): "
                + "; ".join(item.get("message", item.get("type", "Anomaly")) for item in anomaly.get("detections", []))
            )

    draw_charts(st.session_state.history, T)


st.sidebar.divider()

if simulation_mode:
    st.sidebar.info(T["simulation_note"])

    st.sidebar.subheader(T["simulation_controls"])

    needle_a = st.sidebar.slider(
        f"{T['zone_a']} — {T['needle']}",
        0,
        100,
        40,
        key="sim_needle_a"
    )

    needle_b = st.sidebar.slider(
        f"{T['zone_b']} — {T['needle']}",
        0,
        100,
        30,
        key="sim_needle_b"
    )

    needle_c = st.sidebar.slider(
        f"{T['zone_c']} — {T['needle']}",
        0,
        100,
        0,
        key="sim_needle_c"
    )

    st.sidebar.subheader(T["leaks"])

    leak_a = st.sidebar.checkbox(
        T["leak_a"],
        key="sim_leak_a"
    )

    leak_b = st.sidebar.checkbox(
        T["leak_b"],
        key="sim_leak_b"
    )

    micro_c = st.sidebar.checkbox(
        T["micro_c"],
        key="sim_micro_c"
    )

    manifold_leak = st.sidebar.checkbox(
        T["main_leak"],
        key="sim_main_leak"
    )

    if st.sidebar.button(T["reset_sim"], use_container_width=True):
        st.session_state.water_volume = 14.5
        st.session_state.sim_valves = {
            "A": True,
            "B": True,
            "C": True
        }
        st.session_state.sim_alarm = False
        st.session_state.sim_alarm_type = "NONE"
        st.session_state.history = []
        st.session_state.last_sim_time = time.time()
        st.rerun()

    if st.sidebar.button(T["reset"], use_container_width=True):
        st.session_state.sim_alarm = False
        st.session_state.sim_alarm_type = "NONE"
        st.session_state.sim_valves = {
            "A": True,
            "B": True,
            "C": True
        }
        st.rerun()

    simulation_data = run_simulation(
        needle_a,
        needle_b,
        needle_c,
        leak_a,
        leak_b,
        micro_c,
        manifold_leak
    )

    add_simulation_history(simulation_data)

    display_dashboard(
        simulation_data,
        T,
        hardware=False
    )

else:
    st.sidebar.subheader(T["connection"])

    if not MQTT_AVAILABLE:
        st.sidebar.error(T["mqtt_missing"])
        st.error(T["mqtt_missing"])
        st.code("pip install paho-mqtt")
        st.stop()

    broker_host = st.sidebar.text_input(
        T["broker"],
        value="192.168.1.100"
    )

    broker_port = st.sidebar.number_input(
        T["port"],
        min_value=1,
        max_value=65535,
        value=1883
    )

    broker_username = st.sidebar.text_input(
        T["username"],
        value=""
    )

    broker_password = st.sidebar.text_input(
        T["password"],
        value="",
        type="password"
    )

    st.sidebar.subheader(T["mqtt_topics"])

    st.sidebar.code(
        f"{T['command_topic']}: {COMMAND_TOPIC}\n"
        f"{T['telemetry_topic']}: {TELEMETRY_TOPIC}\n"
        f"{T['status_topic']}: {STATUS_TOPIC}"
    )

    connect_col, disconnect_col = st.sidebar.columns(2)

    with connect_col:
        if st.button(T["connect"], use_container_width=True):
            result = connect_mqtt(
                broker_host,
                broker_port,
                broker_username,
                broker_password
            )

            if result:
                st.rerun()

    with disconnect_col:
        if st.button(T["disconnect"], use_container_width=True):
            disconnect_mqtt()
            st.rerun()

    process_mqtt_events()

    if st.session_state.mqtt_connected:
        st.sidebar.success(T["connected"])
    else:
        st.sidebar.error(T["disconnected"])

    st.sidebar.info(T["hardware_note"])
    st.warning(T["hardware_warning"])

    st.subheader(T["hardware_controls"])

    valve_columns = st.columns(3)

    for index, zone in enumerate(["A", "B", "C"]):
        with valve_columns[index]:
            st.markdown(f"### {T['zone_' + zone.lower()]}")

            current_data = st.session_state.mqtt_data
            zone_data = current_data.get("Z" + zone, {})
            valve_is_open = bool(zone_data.get("V", 0))

            if valve_is_open:
                st.success(T["opened"])
            else:
                st.error(T["closed"])

            open_col, close_col = st.columns(2)

            with open_col:
                if st.button(
                    T["open"],
                    key=f"open_{zone}",
                    use_container_width=True,
                    disabled=not st.session_state.mqtt_connected
                ):
                    if send_mqtt_command({zone: 1}):
                        st.toast(T["command_sent"])
                    else:
                        st.error(T["command_failed"])

            with close_col:
                if st.button(
                    T["close"],
                    key=f"close_{zone}",
                    use_container_width=True,
                    disabled=not st.session_state.mqtt_connected
                ):
                    if send_mqtt_command({zone: 0}):
                        st.toast(T["command_sent"])
                    else:
                        st.error(T["command_failed"])

    all_open_col, all_close_col, reset_col = st.columns(3)

    with all_open_col:
        if st.button(
            T["open_all"],
            use_container_width=True,
            disabled=not st.session_state.mqtt_connected
        ):
            if send_mqtt_command({"ALL": 1}):
                st.toast(T["command_sent"])
            else:
                st.error(T["command_failed"])

    with all_close_col:
        if st.button(
            T["close_all"],
            use_container_width=True,
            disabled=not st.session_state.mqtt_connected
        ):
            if send_mqtt_command({"ALL": 0}):
                st.toast(T["command_sent"])
            else:
                st.error(T["command_failed"])

    with reset_col:
        if st.button(
            T["reset"],
            use_container_width=True,
            disabled=not st.session_state.mqtt_connected
        ):
            if send_mqtt_command({"RESET": 1}):
                st.toast(T["command_sent"])
            else:
                st.error(T["command_failed"])

    if st.session_state.mqtt_data:
        telemetry = st.session_state.mqtt_data

        display_dashboard(
            telemetry,
            T,
            hardware=True
        )
    else:
        st.info(T["telemetry_wait"])

    if st.session_state.mqtt_status not in [
        "CONNECTED",
        "CONNECTING",
        "READY"
    ]:
        st.caption(
            f"{T['status']}: {st.session_state.mqtt_status}"
        )


if simulation_mode:
    time.sleep(1)
    st.rerun()
else:
    time.sleep(1)
    st.rerun()

