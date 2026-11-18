# Smart Water Leak Detection & Isolation System (V2)

ESP32-based, gravity-fed, three-branch water network prototype that detects,
localizes, and isolates leaks, with a bilingual Streamlit dashboard, an
advisory statistical anomaly layer built on a frozen baseline, and an
OpenSCAD mechanical model. Developed for the Water Technology Innovation
Competition 2026 (Mechatronics Engineering).

---

## Repository layout

- `firmware/src/main.cpp` — ESP32 firmware: flow pulse counting, ADS1115
  pressure acquisition with divider and elevation compensation, four unified
  leak rules with 3-sample confirmation, relay control, Wi-Fi and MQTT.
- `firmware/platformio.ini` — PlatformIO ESP32 build configuration.
- `firmware/diagram.json` — Wokwi circuit (3 valves, 4 flow buttons, 4 pressure pots).
- `firmware/wokwi.toml` — Wokwi target firmware paths.
- `dashboard_ai/app.py` — Streamlit dashboard: simulation mode, hardware mode,
  valve commands, bilingual UI, live charts.
- `dashboard_ai/anomaly.py` — Advisory anomaly layer: direct rules plus a
  frozen baseline for slow-leak detection.
- `mechanical/model.scad` — OpenSCAD 3D assembly (frame, tanks, hydraulic
  network, control enclosure).
- `requirements.txt` — Python dashboard dependencies (`paho-mqtt<2.0.0`).

---

## 1. Quick start — running the dashboard

From the repository root:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run dashboard_ai/app.py
```
The dashboard opens in Simulation mode with no external dependencies.
Select Real Hardware Connection in the sidebar to attach to an MQTT
broker.

2. Operating modes
The firmware and the dashboard are designed to work in two configurations.
Both share the same thresholds, the same MQTT contract, and the same JSON
schema.

2.1 Wokwi simulation mode (SIMULATION_MODE = true)
Used for end-to-end testing of the dashboard against the virtual ESP32,
including the MQTT path, without any physical hardware.

Open the project in Wokwi (or run the PlatformIO build with the
-DSIMULATION_MODE=true build flag).

The firmware reads the four pressure slide potentiometers on
GPIO 36 / 39 / 34 / 35 and the four flow buttons on
GPIO 27 / 26 / 25 / 33. The three relay modules on GPIO 16 / 17 / 18
drive the three green LEDs that represent the valves.

Wi-Fi is preconfigured for Wokwi-GUEST and MQTT targets the public
broker broker.hivemq.com:1883. No credentials are required.

In the dashboard, choose Real Hardware Connection and connect to
broker.hivemq.com on port 1883.

Open the three valves from the dashboard (they start closed after
boot for safety). Data flows within ~1 second.

Notes:

Public brokers are shared. The firmware uses a unique client ID
(smart_water_esp32_shopak_2026) and a team-specific topic prefix
(smartwater/shopak2026/...) to avoid collisions. Do not change them
without changing the dashboard accordingly.

The flow buttons generate one pulse per press. Continuous flow cannot be
simulated by hand; use the simulation mode of the dashboard for flow
scenarios, and use Wokwi only to prove the MQTT and valve paths.

2.2 Real hardware mode (SIMULATION_MODE = false)
This is the default value in firmware/src/main.cpp. Before flashing:

Replace WIFI_SSID and WIFI_PASSWORD with the local router credentials.

Replace MQTT_SERVER with either the local broker IP or keep the public
broker if the ESP32 has internet access.

Adjust RELAY_ACTIVE_HIGH to match the input polarity of the physical
relay board.

Verify CAL_YFS201 and CAL_YFS401 against a known measured volume.

Verify the pressure sensor variant and its transfer function.

The firmware calibrates the full-column pressure pRefFull at boot with all
valves closed, keeps every valve closed afterwards, and only opens a valve
when the operator explicitly commands it from the dashboard.

3. MQTT contract
All three components (firmware, dashboard, anomaly layer) use the same
topics and JSON schema. The team prefix smartwater/shopak2026 is
mandatory on public brokers.

Purpose	Topic	Retained
Commands	smartwater/shopak2026/device/cmd	No
Telemetry	smartwater/shopak2026/device/telemetry	No
Status	smartwater/shopak2026/device/status	Yes
Telemetry schema (every message, including alarm states)
json
{
  "P_main": 13.42,
  "Q_main": 1.203,
  "ZA": { "P": 17.85, "Q": 0.401, "V": 1 },
  "ZB": { "P": 17.91, "Q": 0.402, "V": 1 },
  "ZC": { "P": 17.88, "Q": 0.000, "V": 1 },
  "alarm": false,
  "alarm_type": "NONE",
  "sensor_ready": true
}
Zone pressures (P) are the raw sensor readings (before elevation
compensation) so the dashboard can show what the sensors physically read.
The detection layer compensates them internally.

The alarm message is always the full schema above. There is no separate
short alarm message anymore; this guarantees the dashboard never records
zeros and the anomaly layer never analyzes an incomplete payload.

alarm_type is one of NONE, MAIN_MANIFOLD_LEAK, ZONE_A_LEAK,
ZONE_B_LEAK, ZONE_C_LEAK.

Command schema
json
{"A": 1}      // open valve A
{"B": 0}      // close valve B
{"ALL": 1}    // open all valves (rejected while an alarm is active)
{"ALL": 0}    // close all valves
{"RESET": 1}  // clear the alarm and KEEP every valve closed
RESET never reopens a valve. After investigating the cause and confirming
that the branch is safe, the operator must explicitly send {"A": 1} (or
equivalent) from the dashboard.

4. Physics and electrical design (V2)
4.1 Voltage divider (1/3) on every pressure channel
The XGZP6847A pressure transducers are 5 V powered and output 0.5 V to
4.5 V. The ADS1115 inputs tolerate at most the ADC supply voltage (3.3 V
in this project). A divider is inserted on each of the four pressure
channels:

R_top = 20 kΩ from the sensor output to the ADC input.

R_bottom = 10 kΩ from the ADC input to GND.

Divider ratio = 10 / (20 + 10) = 1/3.

So V_adc = V_sensor / 3, and the firmware recovers the sensor voltage
by multiplying the ADC voltage by 3.0 before applying the pressure
transfer function:

text
V_sensor = V_adc * 3.0
P_kPa    = (V_sensor - 0.5) * 10.0     // 0.5 .. 4.5 V  ->  0 .. 40 kPa
Firmware constant: DIVIDER_INVERSE_RATIO = 3.0f.

4.2 Elevation compensation (4.316 kPa)
The branch pressure sensors sit 0.44 m below the main pressure sensor on
the manifold. Static column head at that height difference is:

text
ΔP = ρ * g * h = 1000 * 9.81 * 0.44 / 1000 = 4.316 kPa
Every branch pressure is compensated by subtracting this value before any
threshold comparison:

text
P_zone_compensated = max(0, P_zone_raw - 4.316)
Firmware constant: ELEVATION_COMPENSATION_KPA = 4.316f.
Dashboard: ELEVATION_COMPENSATION_KPA in anomaly.py.
Anomaly layer: applied inside extract_features.

4.3 Hydraulic layout (per zone)
text
Top tank (15 L, 1.5 m head)
    -> Main line (12 mm) with filter
    -> Main flow sensor YF-S201 + main pressure sensor
    -> Distribution manifold
        -> Solenoid valve (12 V Direct Acting, NC)
        -> Branch flow sensor YF-S401 (8 mm)
        -> Branch pressure sensor with air trap
        -> Experimental leak point (needle valve)
        -> Needle valve (consumption)
        -> Bottom tank (18 L)
Three zones only (A, B, C). A fourth branch is not supported by the
firmware, the dashboard, or the 3D model.

5. Unified detection rules
The same constants exist in three files: firmware/src/main.cpp,
dashboard_ai/anomaly.py, and the simulation block of
dashboard_ai/app.py. They must be kept in sync. Any change must be
applied to all three files together.

Rule	Condition
Main flow imbalance	abs(Q_main - sum(Q_zones)) > 1.0 and Q_main > 1.2
Main manifold pressure drop	P_main < 0.80 * P_ref and sum(Q_zones) < 0.5
Zone pressure ratio drop	P_zone_compensated / P_main < 0.65
Micro-leak (dead-band)	Q_zone < 0.05 and P_zone_compensated < 0.88 * P_main
All four rules require three consecutive positive samples (3 seconds at
1 Hz) before the firmware actuates a valve. The anomaly layer is advisory
only: it does not actuate valves.

The 1.0 / 1.2 limit on flow imbalance is deliberately above the sum of the
three YF-S401 dead-bands (3 × 0.3 = 0.9 L/min), so genuine micro-flows are
not misreported as imbalance.

6. Anomaly detector — frozen baseline
dashboard_ai/anomaly.py runs two detection families side by side.

Direct rules. They evaluate the four conditions above on the current
sample. They do not depend on history and fire immediately.

Statistical rules. They need history and a baseline:

A moving window (60 samples) catches sudden jumps via a robust
z-score (median + MAD, z_threshold = 3.5). If the window is perfectly
constant, any difference is treated as a definite anomaly
(score = +inf) instead of 0.

A frozen baseline freezes per signal after 30 clean samples
(baseline_samples = 30). It is never updated afterwards, which is what
lets it catch slow leaks that the moving window would otherwise adapt to
(fixed_z_threshold = 4.0).

The frozen baseline is protected against false positives from sensor noise
and from natural tank-level drift by a minimum physically meaningful sigma:

sigma >= 0.35 kPa for any pressure signal.

sigma >= 0.15 L/min for any flow signal.

Only clean samples (no alarm flag, no direct rule triggered) are used to
collect and freeze the baseline. This prevents a leak from contaminating
the reference.

The detector never actuates valves. It produces a JSON result that the
dashboard renders as a warning banner. Pressing RESET on the dashboard calls
reset_detector() and clears both the moving window and the frozen baseline.

7. Sensor and electrical commissioning
The pressure transfer function is documented for a 0.5–4.5 V / 0–40 kPa
variant of the XGZP6847A. Verify the exact variant before relying on the
conversion. Never feed a 5 V sensor output directly to a 3.3 V ADC input
without the 1/3 divider described in §4.1.

Before connecting water or energizing solenoids:

Confirm the relay board input polarity and drive current.

Confirm RELAY_ACTIVE_HIGH matches the physical relay.

Power the valve coils from the correct isolated 12 V supply; never from
ESP32 GPIO pins.

Verify NC/NO behavior of every solenoid with the line depressurized.

Calibrate CAL_YFS201 and CAL_YFS401 against a measured volume.

Verify each pressure channel against a known column height after the
divider is installed.

Confirm the tanks are secured, the frame is stable, and overflow
protection is in place.

8. Alarm behavior and limits
A main manifold alarm closes all three branch valves.

A zone alarm closes only the affected branch and sets the global
alarm flag. Other branches keep their current state.

RESET clears the alarm and keeps every valve closed. The operator
must explicitly re-open a branch after confirming the cause has been
fixed.

The dashboard's statistical anomaly banner is advisory. It never opens or
closes a valve.

The firmware treats missing telemetry as normal. The dashboard warns when
telemetry is older than 5 seconds but does not attempt to infer a leak
from that alone.

This is a prototype, not a certified safety controller. Do not connect
it to an unattended or public water network until electrical protection,
hydraulic ratings, sensor calibration, fail-safe behavior, alarm thresholds,
communications-loss behavior, and manual isolation have been independently
tested.

9. Validation checklist (before the competition demo)
Build and static checks
□ pio run -e esp32dev builds without warnings.
□ pip install -r requirements.txt installs paho-mqtt<2.0.0.
□ streamlit run dashboard_ai/app.py opens without errors.
□ openscad mechanical/model.scad renders without warnings.
Wokwi simulation
□ SIMULATION_MODE = true is active.
□ Serial Monitor shows [WIFI] Connecting to Wokwi-GUEST,
[MQTT] Connected., [BOOT] All valves are initially CLOSED.
□ Dashboard connects to broker.hivemq.com:1883.
□ Opening A from the dashboard lights led_a and shows V:1 in
telemetry within ~1 s.
□ Closing A from the dashboard extinguishes led_a.
□ {"RESET": 1} clears any alarm and leaves all three valves closed.
□ {"ALL": 0} closes all valves; {"ALL": 1} opens them while no
alarm is active.
Dashboard simulation
□ Baseline status reaches Baseline ready after 30 clean samples.
□ Opening a needle alone does not trigger BASELINE_DRIFT.
□ Triggering a Zone A burst produces ZONE_A_LEAK after 3 confirmation
samples, and only valve A closes.
□ Triggering a main manifold leak produces MAIN_MANIFOLD_LEAK and
closes all valves.
□ RESET clears the alarm and leaves all valves closed; the OPEN
buttons in the sidebar re-open a branch safely.
□ Refill Tank resets water_volume to 14.5 L to prevent a false
pressure-drop alarm.
□ Switching between Simulation and Hardware clears history, resets the
anomaly detector, and disconnects the MQTT client cleanly.
Hardware (only after Wokwi passes)
□ Relay polarity matches RELAY_ACTIVE_HIGH; valve state is the same
after power loss (NC default).
□ Each flow sensor reads a consistent value for a measured volume.
□ Each pressure channel reads correctly after the 1/3 divider.
□ Zone pressures differ from P_main by roughly 4.316 kPa at rest,
confirming elevation compensation matches the physical geometry.
□ A controlled main-line leak closes all three valves within ~3 s.
□ A controlled branch leak closes only that branch.
□ Communication loss (disconnect Wi-Fi) leaves the valves in their
last commanded state; the operator notices via the dashboard's
"Telemetry is stale" warning.
10. Known limitations
Three branches only. A fourth is not wired or coded.

Public MQTT brokers are shared. The team prefix and unique client ID are
mandatory to avoid collisions. On a private broker they are still
recommended.

The firmware uses fixed threshold constants. They are physically derived
and tested, but they are not factory-calibrated. Collect normal-operation
data and retune before deployment.

The anomaly layer is advisory. It is not part of the actuation path and
must not be treated as one.
