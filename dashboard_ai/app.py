"""
Smart Water Leak Detection & Isolation System — Streamlit Dashboard.

Modes
-----
* Simulation: the dashboard runs an in-process digital twin of the
  firmware. Raw sensor values are generated with elevation-aware
  physics; the same direct rules as firmware/src/main.cpp are applied
  with 3-sample confirmation; and anomaly.py runs in parallel as an
  advisory statistical layer.

* Hardware: the dashboard connects to an MQTT broker, subscribes to
  the ESP32 telemetry/status topics, and lets the operator open/close
  valves. anomaly.py runs on every fresh telemetry message.

Both modes share the same thresholds (imported from anomaly.py) so the
dashboard and the firmware cannot silently diverge.

Safety
------
RESET clears the alarm and keeps every valve CLOSED. The operator must
explicitly OPEN the branches after verifying the cause has been fixed.
"""

from anomaly import (
    detect_anomaly,
    reset_detector,
    MAIN_FLOW_ERROR_LIMIT,
    MAIN_FLOW_MIN_Q,
    MAIN_PRESSURE_LIMIT,
    MAIN_PRESSURE_SUMQ_MAX,
    ZONE_PRESSURE_LIMIT,
    MICRO_LEAK_FLOW_MAX,
    MICRO_LEAK_PRESSURE,
    ELEVATION_COMPENSATION_KPA,
)

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


# ============================================================
# Configuration — MUST match firmware/src/main.cpp
# ============================================================
ALARM_CONFIRMATION_COUNT = 3

TOPIC_PREFIX     = "smartwater/shopak2026"
COMMAND_TOPIC    = f"{TOPIC_PREFIX}/device/cmd"
TELEMETRY_TOPIC  = f"{TOPIC_PREFIX}/device/telemetry"
STATUS_TOPIC     = f"{TOPIC_PREFIX}/device/status"

INITIAL_TANK_VOLUME_L = 14.5
TANK_TOP_AREA_CM2     = 750.0   # 30 cm x 25 cm
TANK_COLUMN_M         = 1.50    # main static head at the manifold
BRANCH_FRICTION_K     = 0.6     # kPa per (L/min) along a branch
MAIN_FRICTION_K       = 0.45    # kPa per (L/min) along the main line
BURST_LEAK_DROP_KPA   = 5.5
MICRO_LEAK_DROP_KPA   = 1.8
MANIFOLD_LEAK_DROP_KPA = 4.5
MANIFOLD_LEAK_FLOW_LPM = 3.2


# ============================================================
# Language strings
# ============================================================
LANG = {
    "العربية": {
        "title": "💧 نظام كشف وعزل تسربات المياه الذكي",
        "subtitle": "لوحة المراقبة والتحكم في شبكة المياه",
        "language": "اللغة",
        "mode": "وضع التشغيل",
        "simulation": "المحاكاة",
        "hardware": "الاتصال بالنظام الحقيقي",
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
        "sim_valve_controls": "تحكم صمامات المحاكاة",
        "open": "فتح",
        "close": "إغلاق",
        "open_all": "فتح جميع الصمامات",
        "close_all": "إغلاق جميع الصمامات",
        "reset": "إعادة ضبط الإنذار",
        "reset_sim": "إعادة تشغيل المحاكاة",
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
        "mqtt_topics": "موضوعات MQTT",
        "telemetry_topic": "موضوع البيانات",
        "command_topic": "موضوع الأوامر",
        "status_topic": "موضوع الحالة",
        "zone_status": "حالة المناطق",
        "main_status": "حالة الخط الرئيسي",
        "baseline_status": "حالة خط الأساس (anomaly)",
        "baseline_ready": "خط الأساس جاهز",
        "baseline_collecting": "جمع خط الأساس",
        "anomaly_header": "تحذير إحصائي (استشاري فقط)",
        "elevation_note": (
            "ملاحظة فيزيائية: حساسات الفروع أخفض من الحساس الرئيسي بـ 0.44م، "
            "لذا تقرأ ضغطاً أعلى بـ 4.316 kPa. طبقة الكشف تعوّض هذا تلقائياً "
            "قبل تطبيق العتبات."
        ),
        "refill_tank": "إعادة ملء الخزان",
        "tank_low_warning": "منسوب الخزان منخفض. اضغط على «إعادة ملء الخزان» لتجنب إنذار كاذب بتسرب المشعب.",
        "reset_note_sim": "بعد RESET تبقى جميع الصمامات مغلقة للأمان. استخدم أزرار الفتح أعلاه لإعادة التشغيل.",
    },
    "English": {
        "title": "💧 Smart Water Leak Detection System",
        "subtitle": "Water Network Monitoring and Control Dashboard",
        "language": "Language",
        "mode": "Operating Mode",
        "simulation": "Simulation",
        "hardware": "Real Hardware Connection",
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
        "sim_valve_controls": "Simulation Valve Controls",
        "open": "Open",
        "close": "Close",
        "open_all": "Open All Valves",
        "close_all": "Close All Valves",
        "reset": "Reset Alarm",
        "reset_sim": "Restart Simulation",
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
        "mqtt_topics": "MQTT Topics",
        "telemetry_topic": "Telemetry Topic",
        "command_topic": "Command Topic",
        "status_topic": "Status Topic",
        "zone_status": "Zone Status",
        "main_status": "Main Line Status",
        "baseline_status": "Baseline Status (anomaly)",
        "baseline_ready": "Baseline ready",
        "baseline_collecting": "Baseline collecting",
        "anomaly_header": "Statistical warning (advisory only)",
        "elevation_note": (
            "Physics note: branch sensors are 0.44 m below the main sensor, "
            "so they read ~4.316 kPa higher. The detection layer compensates "
            "this automatically before applying thresholds."
        ),
        "refill_tank": "Refill Tank",
        "tank_low_warning": "Tank level is low. Click \"Refill Tank\" to avoid a false main-manifold leak alarm.",
        "reset_note_sim": "After RESET all valves remain CLOSED for safety. Use the OPEN buttons above to restart.",
    },
}


# ============================================================
# Streamlit page configuration
# ============================================================
st.set_page_config(
    page_title="Smart Water Leak Detection",
    page_icon="💧",
    layout="wide",
)


# ============================================================
# Session state initialisation
# ============================================================
def _init_session_state():
    if "history" not in st.session_state:
        st.session_state.history = []

    if "water_volume" not in st.session_state:
        st.session_state.water_volume = INITIAL_TANK_VOLUME_L

    if "sim_valves" not in st.session_state:
        st.session_state.sim_valves = {"A": True, "B": True, "C": True}

    if "sim_alarm" not in st.session_state:
        st.session_state.sim_alarm = False

    if "sim_alarm_type" not in st.session_state:
        st.session_state.sim_alarm_type = "NONE"

    if "sim_p_ref" not in st.session_state:
        initial_height = (
            (INITIAL_TANK_VOLUME_L * 1000.0) / TANK_TOP_AREA_CM2 / 100.0
        )
        st.session_state.sim_p_ref = 9.81 * (TANK_COLUMN_M + initial_height)

    if "sim_main_counter" not in st.session_state:
        st.session_state.sim_main_counter = 0

    if "sim_zone_counters" not in st.session_state:
        st.session_state.sim_zone_counters = {"A": 0, "B": 0, "C": 0}

    if "last_sim_time" not in st.session_state:
        st.session_state.last_sim_time = time.time()

    if "mqtt_client" not in st.session_state:
        st.session_state.mqtt_client = None

    if "mqtt_connected" not in st.session_state:
        st.session_state.mqtt_connected = False

    if "mqtt_data" not in st.session_state:
        st.session_state.mqtt_data = {}

    if "mqtt_status" not in st.session_state:
        st.session_state.mqtt_status = "DISCONNECTED"

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

    if "previous_mode" not in st.session_state:
        st.session_state.previous_mode = None

    if "pending_disconnect" not in st.session_state:
        st.session_state.pending_disconnect = False


_init_session_state()


# ============================================================
# MQTT callbacks
# ============================================================
def _enqueue(userdata, item):
    """Non-blocking enqueue: drop the oldest item if the queue is full.
    This prevents the paho network thread from blocking on a full queue
    (which would break keepalive and disconnect the client).
    """
    try:
        userdata.put_nowait(item)
    except queue.Full:
        try:
            userdata.get_nowait()
        except queue.Empty:
            pass
        try:
            userdata.put_nowait(item)
        except queue.Full:
            pass
    except Exception:
        pass


def on_connect(client, userdata, flags, rc):
    events = userdata
    if rc == 0:
        try:
            client.subscribe(TELEMETRY_TOPIC, qos=0)
            client.subscribe(STATUS_TOPIC, qos=0)
        except Exception:
            pass
        _enqueue(userdata, ("connect", 0))
    else:
        _enqueue(userdata, ("connect", int(rc)))


def on_disconnect(client, userdata, rc):
    _enqueue(userdata, ("disconnect", int(rc)))


def on_message(client, userdata, message):
    try:
        payload = message.payload.decode("utf-8")
        _enqueue(userdata, ("message", message.topic, payload))
    except UnicodeDecodeError:
        _enqueue(userdata, ("invalid",))
    except Exception:
        _enqueue(userdata, ("invalid",))


# ============================================================
# MQTT management
# ============================================================
def connect_mqtt(host, port, username, password):
    if not MQTT_AVAILABLE:
        st.session_state.mqtt_status = "MISSING_LIBRARY"
        return False

    try:
        disconnect_mqtt()

        client = mqtt.Client(
            client_id=f"smartwater_dashboard_shopak2026_{int(time.time())}"
        )

        if username:
            client.username_pw_set(username, password)

        client.user_data_set(st.session_state.mqtt_events)
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        # connect_async does not block the Streamlit thread; the network
        # loop reports success via on_connect, or keeps retrying.
        client.connect_async(host, int(port), keepalive=30)
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
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    st.session_state.mqtt_client = None
    st.session_state.mqtt_connected = False
    st.session_state.mqtt_status = "DISCONNECTED"
    st.session_state.last_telemetry_signature = None

    while True:
        try:
            st.session_state.mqtt_events.get_nowait()
        except queue.Empty:
            break


def send_mqtt_command(command):
    client = st.session_state.get("mqtt_client")

    if client is None:
        st.session_state.last_command_result = "FAILED"
        return False

    try:
        if not client.is_connected():
            st.session_state.last_command_result = "FAILED"
            return False
    except Exception:
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


def process_mqtt_events():
    events = st.session_state.mqtt_events

    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            break
        except Exception:
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
            try:
                _, topic, payload = event
            except ValueError:
                continue

            if topic == TELEMETRY_TOPIC:
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    st.session_state.last_mqtt_message = "INVALID_MESSAGE"
                    continue

                if not isinstance(data, dict):
                    st.session_state.last_mqtt_message = "INVALID_MESSAGE"
                    continue

                # Reject payloads that do not match the agreed schema.
                # The firmware now always sends the full JSON, so a
                # missing key indicates a genuine protocol violation.
                required_keys = (
                    "P_main", "Q_main", "ZA", "ZB", "ZC",
                    "alarm", "alarm_type", "sensor_ready",
                )
                if not all(k in data for k in required_keys):
                    st.session_state.last_mqtt_message = "INVALID_MESSAGE"
                    continue

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
                    except (ValueError, TypeError, KeyError, AttributeError):
                        st.session_state.anomaly_result = None

            elif topic == STATUS_TOPIC:
                st.session_state.mqtt_status = payload


# ============================================================
# History helpers
# ============================================================
def append_history(data):
    try:
        row = {
            "Time": datetime.now().strftime("%H:%M:%S"),
            "P_Main": float(data.get("P_main", 0.0)),
            "P_A":    float((data.get("ZA") or {}).get("P", 0.0)),
            "P_B":    float((data.get("ZB") or {}).get("P", 0.0)),
            "P_C":    float((data.get("ZC") or {}).get("P", 0.0)),
            "Q_Main": float(data.get("Q_main", 0.0)),
            "Q_A":    float((data.get("ZA") or {}).get("Q", 0.0)),
            "Q_B":    float((data.get("ZB") or {}).get("Q", 0.0)),
            "Q_C":    float((data.get("ZC") or {}).get("Q", 0.0)),
        }
    except (TypeError, ValueError, AttributeError):
        return

    st.session_state.history.append(row)
    st.session_state.history = st.session_state.history[-60:]


def add_simulation_history(data):
    try:
        row = {
            "Time": datetime.now().strftime("%H:%M:%S"),
            "P_Main": float(data["P_main"]),
            "P_A":    float(data["ZA"]["P"]),
            "P_B":    float(data["ZB"]["P"]),
            "P_C":    float(data["ZC"]["P"]),
            "Q_Main": float(data["Q_main"]),
            "Q_A":    float(data["ZA"]["Q"]),
            "Q_B":    float(data["ZB"]["Q"]),
            "Q_C":    float(data["ZC"]["Q"]),
        }
    except (KeyError, TypeError, ValueError):
        return

    st.session_state.history.append(row)
    st.session_state.history = st.session_state.history[-60:]


# ============================================================
# Simulation: physics
# ------------------------------------------------------------
# Raw readings are what the ADS1115 would actually see. Because the
# branch sensors sit 0.44 m below the main sensor, their raw readings
# are ~4.316 kPa HIGHER than the main sensor at the same hydraulic
# point. Detection compares compensated values; the dashboard shows
# raw values (exactly what the firmware publishes).
# ============================================================
def _calculate_zone_flow(valve_open, needle_percent, burst, micro):
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

    # YF-S401 dead-band ~0.3 L/min: any true flow below that is
    # measured as 0 by the pulse counter.
    measured_flow = 0.0 if true_flow < 0.3 else max(
        0.0,
        true_flow + np.random.normal(0, 0.03),
    )

    return true_flow, measured_flow


def _compute_physics(
    needle_a, needle_b, needle_c,
    leak_a, leak_b, micro_c, manifold_leak,
):
    valves = st.session_state.sim_valves

    q_true_a, q_a = _calculate_zone_flow(valves["A"], needle_a, leak_a, False)
    q_true_b, q_b = _calculate_zone_flow(valves["B"], needle_b, leak_b, False)
    q_true_c, q_c = _calculate_zone_flow(valves["C"], needle_c, False, micro_c)

    q_manifold = MANIFOLD_LEAK_FLOW_LPM if manifold_leak else 0.0
    q_true_main = q_true_a + q_true_b + q_true_c + q_manifold

    q_main_measured = 0.0 if q_true_main < 1.0 else max(
        0.0,
        q_true_main + np.random.normal(0, 0.05),
    )

    tank_height = (
        st.session_state.water_volume * 1000.0
    ) / TANK_TOP_AREA_CM2 / 100.0

    p_static = 9.81 * (TANK_COLUMN_M + tank_height)

    p_main = max(
        0.5,
        p_static
        - q_true_main * MAIN_FRICTION_K
        - (MANIFOLD_LEAK_DROP_KPA if manifold_leak else 0.0)
        + np.random.normal(0, 0.08),
    )

    # Branch raw pressure = main pressure + elevation head - friction
    # - leak drop. The 4.316 kPa elevation head is what the branch
    # sensor physically reads in excess of the main sensor.
    p_a_raw = max(
        0.0,
        p_main + ELEVATION_COMPENSATION_KPA
        - q_true_a * BRANCH_FRICTION_K
        - (BURST_LEAK_DROP_KPA if leak_a else 0.0)
        + np.random.normal(0, 0.08),
    ) if valves["A"] else 0.0

    p_b_raw = max(
        0.0,
        p_main + ELEVATION_COMPENSATION_KPA
        - q_true_b * BRANCH_FRICTION_K
        - (BURST_LEAK_DROP_KPA if leak_b else 0.0)
        + np.random.normal(0, 0.08),
    ) if valves["B"] else 0.0

    p_c_raw = max(
        0.0,
        p_main + ELEVATION_COMPENSATION_KPA
        - q_true_c * BRANCH_FRICTION_K
        - (MICRO_LEAK_DROP_KPA if micro_c else 0.0)
        + np.random.normal(0, 0.08),
    ) if valves["C"] else 0.0

    return {
        "q_true_main": q_true_main,
        "p_main": p_main,
        "q_main": q_main_measured,
        "p_a_raw": p_a_raw,
        "p_b_raw": p_b_raw,
        "p_c_raw": p_c_raw,
        "q_a": q_a,
        "q_b": q_b,
        "q_c": q_c,
    }


# ============================================================
# Simulation: firmware-equivalent detection
# ------------------------------------------------------------
# Mirrors firmware/src/main.cpp exactly:
#   * same thresholds (imported from anomaly.py)
#   * elevation compensation applied before zone comparisons
#   * 3-sample confirmation before triggering
#   * RESET keeps every valve closed
# ============================================================
def _apply_detection(physics):
    p_main = physics["p_main"]
    q_main = physics["q_main"]

    q_zones = {
        "A": physics["q_a"],
        "B": physics["q_b"],
        "C": physics["q_c"],
    }
    p_zones_comp = {
        "A": max(0.0, physics["p_a_raw"] - ELEVATION_COMPENSATION_KPA),
        "B": max(0.0, physics["p_b_raw"] - ELEVATION_COMPENSATION_KPA),
        "C": max(0.0, physics["p_c_raw"] - ELEVATION_COMPENSATION_KPA),
    }
    valve_open = dict(st.session_state.sim_valves)

    # --- Main manifold leak ---
    sum_q = q_zones["A"] + q_zones["B"] + q_zones["C"]
    flow_diff = abs(q_main - sum_q)

    pressure_drop = (
        st.session_state.sim_p_ref > 1.0
        and p_main < st.session_state.sim_p_ref * MAIN_PRESSURE_LIMIT
        and sum_q < MAIN_PRESSURE_SUMQ_MAX
    )
    flow_imbalance = (
        q_main > MAIN_FLOW_MIN_Q
        and flow_diff > MAIN_FLOW_ERROR_LIMIT
    )

    if pressure_drop or flow_imbalance:
        st.session_state.sim_main_counter = min(
            ALARM_CONFIRMATION_COUNT,
            st.session_state.sim_main_counter + 1,
        )
    else:
        st.session_state.sim_main_counter = 0

    if st.session_state.sim_main_counter >= ALARM_CONFIRMATION_COUNT:
        st.session_state.sim_alarm = True
        st.session_state.sim_alarm_type = "MAIN_MANIFOLD_LEAK"
        st.session_state.sim_valves = {"A": False, "B": False, "C": False}
        st.session_state.sim_main_counter = 0
        return

    # --- Zone leaks ---
    for zone in ("A", "B", "C"):
        if not valve_open[zone]:
            st.session_state.sim_zone_counters[zone] = 0
            continue

        ratio = (
            p_zones_comp[zone] / p_main if p_main > 0.5 else 1.0
        )

        pressure_ratio_drop = ratio < ZONE_PRESSURE_LIMIT
        micro_leak = (
            q_zones[zone] < MICRO_LEAK_FLOW_MAX
            and p_zones_comp[zone] < MICRO_LEAK_PRESSURE * p_main
        )

        if pressure_ratio_drop or micro_leak:
            st.session_state.sim_zone_counters[zone] = min(
                ALARM_CONFIRMATION_COUNT,
                st.session_state.sim_zone_counters[zone] + 1,
            )
        else:
            st.session_state.sim_zone_counters[zone] = 0

        if st.session_state.sim_zone_counters[zone] >= ALARM_CONFIRMATION_COUNT:
            st.session_state.sim_alarm = True
            st.session_state.sim_alarm_type = f"ZONE_{zone}_LEAK"
            st.session_state.sim_valves[zone] = False
            st.session_state.sim_zone_counters[zone] = 0
            return  # one zone per tick, mirroring firmware


def run_simulation_tick(
    needle_a, needle_b, needle_c,
    leak_a, leak_b, micro_c, manifold_leak,
):
    physics = _compute_physics(
        needle_a, needle_b, needle_c,
        leak_a, leak_b, micro_c, manifold_leak,
    )

    # Build the preliminary telemetry BEFORE detection so the anomaly
    # detector sees the physical state (open valves, flow present)
    # rather than the post-isolation state.
    preliminary = {
        "P_main": physics["p_main"],
        "Q_main": physics["q_main"],
        "ZA": {
            "P": physics["p_a_raw"], "Q": physics["q_a"],
            "V": int(st.session_state.sim_valves["A"]),
        },
        "ZB": {
            "P": physics["p_b_raw"], "Q": physics["q_b"],
            "V": int(st.session_state.sim_valves["B"]),
        },
        "ZC": {
            "P": physics["p_c_raw"], "Q": physics["q_c"],
            "V": int(st.session_state.sim_valves["C"]),
        },
        "alarm": st.session_state.sim_alarm,
        "alarm_type": st.session_state.sim_alarm_type,
        "sensor_ready": True,
    }

    # Advisory statistical layer.
    try:
        st.session_state.anomaly_result = detect_anomaly(preliminary)
    except (ValueError, TypeError, KeyError, AttributeError):
        st.session_state.anomaly_result = None

    # Firmware-equivalent detection.
    if not st.session_state.sim_alarm:
        _apply_detection(physics)

    # Tank drain (uses true flow, not measured, so consumption is
    # physically accurate regardless of dead-band effects).
    elapsed = max(
        0.0,
        min(time.time() - st.session_state.last_sim_time, 5.0),
    )
    st.session_state.last_sim_time = time.time()

    st.session_state.water_volume = max(
        0.0,
        st.session_state.water_volume
        - (physics["q_true_main"] / 60.0) * elapsed,
    )

    final_telemetry = {
        "P_main": physics["p_main"],
        "Q_main": physics["q_main"],
        "ZA": {
            "P": physics["p_a_raw"], "Q": physics["q_a"],
            "V": int(st.session_state.sim_valves["A"]),
        },
        "ZB": {
            "P": physics["p_b_raw"], "Q": physics["q_b"],
            "V": int(st.session_state.sim_valves["B"]),
        },
        "ZC": {
            "P": physics["p_c_raw"], "Q": physics["q_c"],
            "V": int(st.session_state.sim_valves["C"]),
        },
        "alarm": st.session_state.sim_alarm,
        "alarm_type": st.session_state.sim_alarm_type,
        "sensor_ready": True,
    }

    add_simulation_history(final_telemetry)
    return final_telemetry


# ============================================================
# Display helpers
# ============================================================
def draw_charts(history, T):
    if not history:
        st.info(T["no_data"])
        return

    try:
        df = pd.DataFrame(history)
    except Exception:
        st.info(T["no_data"])
        return

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        fig_pressure = go.Figure()
        for column, name in [
            ("P_Main", "Main"),
            ("P_A", "Zone A"),
            ("P_B", "Zone B"),
            ("P_C", "Zone C"),
        ]:
            if column not in df.columns:
                continue
            fig_pressure.add_trace(
                go.Scatter(
                    x=df["Time"], y=df[column],
                    name=name, mode="lines+markers",
                )
            )
        fig_pressure.update_layout(
            title=T["pressure_chart"],
            yaxis_title=T["pressure_axis"],
            xaxis_title=T["time"],
            height=370,
            margin=dict(l=15, r=15, t=50, b=15),
        )
        st.plotly_chart(fig_pressure, use_container_width=True)

    with chart_col2:
        fig_flow = go.Figure()
        for column, name in [
            ("Q_Main", "Main"),
            ("Q_A", "Zone A"),
            ("Q_B", "Zone B"),
            ("Q_C", "Zone C"),
        ]:
            if column not in df.columns:
                continue
            fig_flow.add_trace(
                go.Scatter(
                    x=df["Time"], y=df[column],
                    name=name, mode="lines+markers",
                )
            )
        fig_flow.update_layout(
            title=T["flow_chart"],
            yaxis_title=T["flow_axis"],
            xaxis_title=T["time"],
            height=370,
            margin=dict(l=15, r=15, t=50, b=15),
        )
        st.plotly_chart(fig_flow, use_container_width=True)


def _render_anomaly_banner(T):
    anomaly = st.session_state.get("anomaly_result")
    if not anomaly or not anomaly.get("anomaly"):
        return

    severity = anomaly.get("severity", "UNKNOWN")
    messages = "; ".join(
        item.get("message", item.get("type", "Anomaly"))
        for item in anomaly.get("detections", [])
    )
    if messages:
        st.warning(f"{T['anomaly_header']} ({severity}): {messages}")


def _render_baseline_status(T, hardware=False):
    anomaly = st.session_state.get("anomaly_result")
    if not anomaly:
        return

    ready = anomaly.get("baseline_ready", False)
    if ready:
        st.caption(f"✅ {T['baseline_ready']}")
    else:
        status = anomaly.get("baseline_status", {})
        collecting = ", ".join(
            f"{k}: {v}" for k, v in status.items()
            if not v.startswith("frozen")
        )
        if collecting:
            st.caption(f"⏳ {T['baseline_collecting']} — {collecting}")


def display_dashboard(data, T, hardware=False):
    zone_a = data.get("ZA") or {}
    zone_b = data.get("ZB") or {}
    zone_c = data.get("ZC") or {}

    try:
        p_main = float(data.get("P_main", 0.0))
        q_main = float(data.get("Q_main", 0.0))
        p_a = float(zone_a.get("P", 0.0))
        p_b = float(zone_b.get("P", 0.0))
        p_c = float(zone_c.get("P", 0.0))
        q_a = float(zone_a.get("Q", 0.0))
        q_b = float(zone_b.get("Q", 0.0))
        q_c = float(zone_c.get("Q", 0.0))
    except (TypeError, ValueError):
        st.error("Invalid telemetry payload — values could not be cast to float.")
        return

    v_a = bool(int(zone_a.get("V", 0) or 0))
    v_b = bool(int(zone_b.get("V", 0) or 0))
    v_c = bool(int(zone_c.get("V", 0) or 0))

    alarm = bool(data.get("alarm", False))
    alarm_type = data.get("alarm_type", "NONE")

    if alarm:
        st.error(f"{T['alarm']} — {alarm_type}")
    else:
        st.success(T["normal"])

    # KPI row
    c1, c2, c3, c4, c5 = st.columns(5)

    if hardware:
        tank_value = "N/A"
    else:
        tank_value = f"{st.session_state.water_volume:.2f} L"

    c1.metric(T["tank"], tank_value)
    c2.metric(T["pressure"], f"{p_main:.2f} kPa")
    c3.metric(T["main_flow"], f"{q_main:.2f} L/min")
    c4.metric(
        T["zone_a"], f"{q_a:.2f} L/min", f"{p_a:.2f} kPa"
    )
    c5.metric(
        T["zone_b"], f"{q_b:.2f} L/min", f"{p_b:.2f} kPa"
    )

    st.subheader(T["zone_status"])

    zone_cols = st.columns(3)
    zone_data = [
        (T["zone_a"], p_a, q_a, v_a),
        (T["zone_b"], p_b, q_b, v_b),
        (T["zone_c"], p_c, q_c, v_c),
    ]

    for col, (name, pressure, flow, valve) in zip(zone_cols, zone_data):
        with col:
            st.metric(name, f"{flow:.2f} L/min", f"{pressure:.2f} kPa")
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
            st.success(f"{T['sensor_status']}: {T['ready']}")
        else:
            st.error(f"{T['sensor_status']}: {T['not_ready']}")

        if st.session_state.last_mqtt_timestamp is not None:
            age = (
                datetime.now() - st.session_state.last_mqtt_timestamp
            ).total_seconds()
            if age > 5:
                st.warning(
                    "Telemetry is stale: no fresh ESP32 data for more "
                    f"than 5 seconds (age = {age:.1f}s)."
                )

    _render_anomaly_banner(T)
    _render_baseline_status(T, hardware=hardware)

    st.caption(T["elevation_note"])

    draw_charts(st.session_state.history, T)


# ============================================================
# UI entry point
# ============================================================
selected_lang = st.sidebar.radio(
    "🌐 Language / اللغة",
    ["العربية", "English"],
    horizontal=True,
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
    unsafe_allow_html=True,
)

st.title(T["title"])
st.markdown(T["subtitle"])

mode = st.sidebar.radio(
    T["mode"],
    [T["simulation"], T["hardware"]],
    index=0,
)
simulation_mode = mode == T["simulation"]


# -------- Mode switch cleanup --------
if st.session_state.previous_mode != mode:
    st.session_state.previous_mode = mode
    st.session_state.history = []
    st.session_state.anomaly_result = None
    st.session_state.last_telemetry_signature = None
    reset_detector()

    if simulation_mode:
        # Leaving hardware mode — shut down the MQTT client.
        disconnect_mqtt()
    else:
        # Entering hardware mode — freeze simulation state, do not
        # carry any sim leak counters into the hardware dashboard.
        st.session_state.sim_main_counter = 0
        st.session_state.sim_zone_counters = {"A": 0, "B": 0, "C": 0}


st.sidebar.divider()


# ============================================================
# SIMULATION MODE
# ============================================================
if simulation_mode:
    st.sidebar.info(T["simulation_note"])

    # --- Fault and consumption sliders ---
    st.sidebar.subheader(T["simulation_controls"])

    needle_a = st.sidebar.slider(
        f"{T['zone_a']} — {T['needle']}", 0, 100, 40,
        key="sim_needle_a",
    )
    needle_b = st.sidebar.slider(
        f"{T['zone_b']} — {T['needle']}", 0, 100, 30,
        key="sim_needle_b",
    )
    needle_c = st.sidebar.slider(
        f"{T['zone_c']} — {T['needle']}", 0, 100, 0,
        key="sim_needle_c",
    )

    st.sidebar.subheader(T["leaks"])

    leak_a = st.sidebar.checkbox(T["leak_a"], key="sim_leak_a")
    leak_b = st.sidebar.checkbox(T["leak_b"], key="sim_leak_b")
    micro_c = st.sidebar.checkbox(T["micro_c"], key="sim_micro_c")
    manifold_leak = st.sidebar.checkbox(T["main_leak"], key="sim_main_leak")

    st.sidebar.divider()

    # --- Manual valve controls (needed after RESET) ---
    st.sidebar.subheader(T["sim_valve_controls"])

    valve_cols = st.sidebar.columns(2)
    with valve_cols[0]:
        if st.button(T["open_all"], use_container_width=True):
            st.session_state.sim_valves = {"A": True, "B": True, "C": True}
            st.session_state.sim_main_counter = 0
            st.session_state.sim_zone_counters = {"A": 0, "B": 0, "C": 0}
            st.rerun()
    with valve_cols[1]:
        if st.button(T["close_all"], use_container_width=True):
            st.session_state.sim_valves = {"A": False, "B": False, "C": False}
            st.rerun()

    for zone in ("A", "B", "C"):
        cols = st.sidebar.columns(2)
        with cols[0]:
            if st.button(
                f"{T['open']} {zone}",
                key=f"sim_open_{zone}",
                use_container_width=True,
                disabled=st.session_state.sim_valves[zone],
            ):
                st.session_state.sim_valves[zone] = True
                st.session_state.sim_zone_counters[zone] = 0
                st.rerun()
        with cols[1]:
            if st.button(
                f"{T['close']} {zone}",
                key=f"sim_close_{zone}",
                use_container_width=True,
                disabled=not st.session_state.sim_valves[zone],
            ):
                st.session_state.sim_valves[zone] = False
                st.rerun()

    st.sidebar.divider()

    # --- RESET (clears alarm + closes valves + resets detector) ---
    if st.sidebar.button(T["reset"], use_container_width=True):
        st.session_state.sim_alarm = False
        st.session_state.sim_alarm_type = "NONE"
        st.session_state.sim_main_counter = 0
        st.session_state.sim_zone_counters = {"A": 0, "B": 0, "C": 0}
        st.session_state.sim_valves = {"A": False, "B": False, "C": False}
        st.session_state.anomaly_result = None
        reset_detector()
        st.sidebar.info(T["reset_note_sim"])
        st.rerun()

    # --- RESET SIM (full restart) ---
    if st.sidebar.button(T["reset_sim"], use_container_width=True):
        st.session_state.water_volume = INITIAL_TANK_VOLUME_L
        st.session_state.sim_valves = {"A": True, "B": True, "C": True}
        st.session_state.sim_alarm = False
        st.session_state.sim_alarm_type = "NONE"
        st.session_state.sim_main_counter = 0
        st.session_state.sim_zone_counters = {"A": 0, "B": 0, "C": 0}
        st.session_state.history = []
        st.session_state.anomaly_result = None
        st.session_state.last_sim_time = time.time()
        reset_detector()
        st.rerun()

    # --- Refill tank ---
    if st.sidebar.button(T["refill_tank"], use_container_width=True):
        st.session_state.water_volume = INITIAL_TANK_VOLUME_L
        st.rerun()

    # --- Advance simulation one tick ---
    simulation_data = run_simulation_tick(
        needle_a, needle_b, needle_c,
        leak_a, leak_b, micro_c, manifold_leak,
    )

    if st.session_state.water_volume < 2.0:
        st.warning(T["tank_low_warning"])

    display_dashboard(simulation_data, T, hardware=False)


# ============================================================
# HARDWARE MODE
# ============================================================
else:
    st.sidebar.subheader(T["connection"])

    if not MQTT_AVAILABLE:
        st.sidebar.error(T["mqtt_missing"])
        st.error(T["mqtt_missing"])
        st.code("pip install 'paho-mqtt<2.0.0'")
        st.stop()

    broker_host = st.sidebar.text_input(
        T["broker"], value="broker.hivemq.com",
    )
    broker_port = st.sidebar.number_input(
        T["port"], min_value=1, max_value=65535, value=1883,
    )
    broker_username = st.sidebar.text_input(T["username"], value="")
    broker_password = st.sidebar.text_input(
        T["password"], value="", type="password",
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
            if connect_mqtt(
                broker_host, broker_port,
                broker_username, broker_password,
            ):
                st.rerun()

    with disconnect_col:
        if st.button(T["disconnect"], use_container_width=True):
            disconnect_mqtt()
            st.rerun()

    # Refresh connected state from the paho client (source of truth).
    client = st.session_state.get("mqtt_client")
    if client is not None:
        try:
            st.session_state.mqtt_connected = bool(client.is_connected())
        except Exception:
            st.session_state.mqtt_connected = False

    process_mqtt_events()

    if st.session_state.mqtt_connected:
        st.sidebar.success(T["connected"])
    else:
        st.sidebar.error(T["disconnected"])

    st.sidebar.info(T["hardware_note"])
    if not st.session_state.mqtt_connected:
        st.warning(T["hardware_warning"])

    st.subheader(T["hardware_controls"])

    valve_columns = st.columns(3)
    for index, zone in enumerate(["A", "B", "C"]):
        with valve_columns[index]:
            st.markdown(f"### {T['zone_' + zone.lower()]}")

            current_data = st.session_state.mqtt_data or {}
            zone_data = current_data.get("Z" + zone) or {}
            try:
                valve_is_open = bool(int(zone_data.get("V", 0) or 0))
            except (TypeError, ValueError):
                valve_is_open = False

            if valve_is_open:
                st.success(T["opened"])
            else:
                st.error(T["closed"])

            open_col, close_col = st.columns(2)
            with open_col:
                if st.button(
                    T["open"],
                    key=f"hw_open_{zone}",
                    use_container_width=True,
                    disabled=not st.session_state.mqtt_connected,
                ):
                    if send_mqtt_command({zone: 1}):
                        st.toast(T["command_sent"])
                    else:
                        st.error(T["command_failed"])
            with close_col:
                if st.button(
                    T["close"],
                    key=f"hw_close_{zone}",
                    use_container_width=True,
                    disabled=not st.session_state.mqtt_connected,
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
            disabled=not st.session_state.mqtt_connected,
        ):
            if send_mqtt_command({"ALL": 1}):
                st.toast(T["command_sent"])
            else:
                st.error(T["command_failed"])

    with all_close_col:
        if st.button(
            T["close_all"],
            use_container_width=True,
            disabled=not st.session_state.mqtt_connected,
        ):
            if send_mqtt_command({"ALL": 0}):
                st.toast(T["command_sent"])
            else:
                st.error(T["command_failed"])

    with reset_col:
        if st.button(
            T["reset"],
            use_container_width=True,
            disabled=not st.session_state.mqtt_connected,
        ):
            if send_mqtt_command({"RESET": 1}):
                st.toast(T["command_sent"])
                st.session_state.anomaly_result = None
                reset_detector()
            else:
                st.error(T["command_failed"])

    if st.session_state.mqtt_data:
        display_dashboard(
            st.session_state.mqtt_data, T, hardware=True,
        )
    else:
        st.info(T["telemetry_wait"])

    if st.session_state.mqtt_status not in (
        "CONNECTED", "CONNECTING", "READY",
    ):
        st.caption(f"{T['status']}: {st.session_state.mqtt_status}")


# ============================================================
# Advance one tick
# ============================================================
time.sleep(1)
st.rerun()
