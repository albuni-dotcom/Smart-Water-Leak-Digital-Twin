# Smart Water Leak Detection & Isolation System

ESP32-based monitoring and isolation prototype with an Arabic/English Streamlit dashboard, MQTT telemetry and ON/OFF branch valves.

## Repository layout

- `firmware/src/main.cpp`: ESP32 firmware, flow pulse counting, pressure acquisition, leak rules, valve outputs and MQTT.
- `firmware/platformio.ini`: PlatformIO ESP32 build configuration.
- `dashboard_ai/app.py`: Streamlit dashboard, simulation mode and MQTT hardware mode.
- `dashboard_ai/anomaly.py`: Statistical anomaly advisory layer.
- `mechanical/model.scad`: OpenSCAD assembly model.
- `requirements.txt`: Python dashboard dependencies.

## 1. Run the dashboard

From the repository root:

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run dashboard_ai/app.py
```

The dashboard starts in simulation mode. Select the hardware mode to connect to an MQTT broker.

## 2. Configure ESP32 hardware mode

The firmware deliberately defaults to `SIMULATION_MODE=true`. To enable the physical ADS1115/Wi-Fi/MQTT path, change the default in `firmware/src/main.cpp` to false, or define `SIMULATION_MODE=false` in the PlatformIO build flags. Do not deploy with placeholder network settings.

Set these values in `firmware/src/main.cpp` before flashing:

- `WIFI_SSID` and `WIFI_PASSWORD`
- `MQTT_SERVER`, `MQTT_PORT`, and optional MQTT credentials
- `RELAY_ACTIVE_HIGH` to match the actual relay board input polarity
- `CAL_YFS201` and `CAL_YFS401` after calibration against a measured volume

The dashboard and firmware use these topics:

| Purpose | Topic |
|---|---|
| Commands | `smartwater/device/cmd` |
| Telemetry | `smartwater/device/telemetry` |
| Status | `smartwater/device/status` |

Example commands:

```json
{"A":1}
{"B":0}
{"ALL":0}
{"RESET":1}
```

A reset clears the alarm but leaves all valves closed. Reopen a valve only after the cause has been inspected and cleared.

## 3. Sensor and electrical commissioning

The current pressure conversion in firmware assumes a 0.5–4.5 V transducer output corresponding to 0–40 kPa. Verify the exact pressure sensor variant and its transfer function. Verify that the signal voltage presented to every ADS1115 input remains within the ADC supply/input limits; add suitable signal conditioning where required. Do not connect a 5 V sensor output directly to a 3.3 V ADC input without confirming compatibility.

Before connecting water or energizing solenoids:

1. Confirm the relay module is suitable for the ESP32 logic level and the valve coil current.
2. Use a correctly rated isolated 12 V supply for the solenoid valves; do not power coils from ESP32 GPIO pins.
3. Confirm valve normally-open/normally-closed behavior and relay active polarity with the hydraulic line depressurized.
4. Check for leaks, secure the tanks and frame, and provide overflow protection.
5. Verify flow sensor pulse calibration using a known measured volume for each sensor type.
6. Establish pressure baselines with the actual plumbing, valves and expected operating flow.

The firmware uses fixed initial branch pressure ratios as provisional thresholds. They are not factory-calibrated values. Tune them using collected normal-operation data and validate with controlled leak tests.

## 4. Alarm behavior and limits

Main-line alarms close all branch valves. A branch alarm closes the affected branch and raises the system alarm state; the other branches are left in their current state. Reset never automatically reopens valves.

The dashboard anomaly detector is an advisory statistical layer. It does not directly actuate valves. It compares current measurements with recent normal samples, excludes closed branches from flow balance, and does not train its baseline on samples already classified as anomalous or alarmed.

This is a prototype, not a certified safety controller. Do not connect it to an unattended or public water network until electrical protection, hydraulic ratings, sensor calibration, fail-safe behavior, alarm thresholds, communications-loss behavior and manual isolation have been independently tested.

## 5. Validation checklist

- [ ] Build firmware with PlatformIO for `esp32dev`.
- [ ] Run dashboard in simulation mode.
- [ ] Test MQTT connect/disconnect and all command payloads with valves disconnected.
- [ ] Verify telemetry freshness and displayed valve states.
- [ ] Verify relay polarity and power-loss valve state.
- [ ] Calibrate each flow sensor and pressure channel.
- [ ] Run controlled main-line and branch leak tests.
- [ ] Confirm alarms persist as expected and reset leaves valves closed.
