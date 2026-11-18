#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <Adafruit_ADS1X15.h>
#include <math.h>
#include <string.h>

// ============================================================
// Compilation switches
// ------------------------------------------------------------
// MQTT_ENABLED is intentionally independent of SIMULATION_MODE
// so the dashboard can be tested end-to-end inside Wokwi.
// ============================================================
#ifndef SIMULATION_MODE
#define SIMULATION_MODE true
#endif

#define MQTT_ENABLED true

// ============================================================
// Hardware pin map
// ============================================================
#define FLOW_MAIN_PIN 27
#define FLOW_ZA_PIN   26
#define FLOW_ZB_PIN   25
#define FLOW_ZC_PIN   33

#define VALVE_A_PIN   16
#define VALVE_B_PIN   17
#define VALVE_C_PIN   18

#define RELAY_ACTIVE_HIGH true

// ============================================================
// Network configuration
// ------------------------------------------------------------
// Wokwi defaults (SIMULATION_MODE=true):
//   SSID     = "Wokwi-GUEST"
//   Password = "" (empty)
//   Broker   = "broker.hivemq.com"  (public)
//
// REAL HARDWARE CHECKLIST before flashing:
//   1. Replace WIFI_SSID / WIFI_PASSWORD with the local router.
//   2. Replace MQTT_SERVER with the broker IP (or keep the
//      public broker if the ESP32 has internet access).
//   3. Keep MQTT_CLIENT_ID unique per device on public brokers.
//   4. Keep the topic prefix unique to this team:
//         smartwater/shopak2026/...
//      app.py MUST use the exact same prefix.
// ============================================================
const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const char* MQTT_SERVER   = "broker.hivemq.com";
const uint16_t MQTT_PORT  = 1883;
const char* MQTT_USER     = "";
const char* MQTT_PASSWORD = "";

const char* MQTT_CLIENT_ID       = "smart_water_esp32_shopak_2026";
const char* MQTT_COMMAND_TOPIC   = "smartwater/shopak2026/device/cmd";
const char* MQTT_TELEMETRY_TOPIC = "smartwater/shopak2026/device/telemetry";
const char* MQTT_STATUS_TOPIC    = "smartwater/shopak2026/device/status";

// ============================================================
// Sensor calibration (pulses per litre)
// ============================================================
const float CAL_YFS201 = 450.0f;
const float CAL_YFS401 = 5880.0f;

// ============================================================
// Signal conditioning constants
// ------------------------------------------------------------
// Voltage divider 1/3 (R_top = 20k from sensor, R_bottom = 10k to GND):
//   V_adc = V_sensor * (10 / 30) = V_sensor / 3
//   => V_sensor = V_adc * 3.0
// ============================================================
const float DIVIDER_INVERSE_RATIO = 3.0f;

// Elevation compensation: branch sensors are 0.44 m below the
// main pressure sensor, adding rho*g*h = 4.316 kPa of static
// pressure. Subtract this from every branch reading before
// comparing it against the main pressure or any threshold.
const float ELEVATION_COMPENSATION_KPA = 4.316f;

// ============================================================
// Unified detection thresholds
// ------------------------------------------------------------
// These values MUST match app.py and anomaly.py exactly.
// ============================================================
const float MAIN_FLOW_ERROR_LIMIT  = 1.0f;   // L/min
const float MAIN_FLOW_MIN_Q        = 1.2f;   // L/min
const float MAIN_PRESSURE_LIMIT    = 0.80f;  // P_main < 0.80 * P_ref
const float MAIN_PRESSURE_SUMQ_MAX = 0.5f;   // L/min
const float ZONE_PRESSURE_LIMIT    = 0.65f;  // P_zone_c / P_main < 0.65
const float MICRO_LEAK_FLOW_MAX    = 0.05f;  // L/min
const float MICRO_LEAK_PRESSURE    = 0.88f;  // P_zone_c < 0.88 * P_main

// ============================================================
// Timing
// ============================================================
const uint32_t SAMPLE_INTERVAL         = 1000;
const uint32_t MQTT_RECONNECT_INTERVAL = 5000;
const uint8_t  WINDOW_SIZE             = 10;
const uint8_t  ALARM_CONFIRMATION_COUNT = 3;

// ============================================================
// Global objects
// ============================================================
Adafruit_ADS1115 ads;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

volatile uint32_t pulsesMain = 0;
volatile uint32_t pulsesZA   = 0;
volatile uint32_t pulsesZB   = 0;
volatile uint32_t pulsesZC   = 0;

const uint8_t valvePins[3] = { VALVE_A_PIN, VALVE_B_PIN, VALVE_C_PIN };

const uint8_t simulationPressurePins[4] = { 36, 39, 34, 35 };

bool valveState[3] = { false, false, false };

bool alarmActive = false;
bool sensorReady = true;
bool mqttConnected = false;

float pMainBuffer[WINDOW_SIZE]        = {};
float pZoneBuffer[3][WINDOW_SIZE]     = {};

float pMain           = 0.0f;
float pZones[3]       = {};
float pZonesComp[3]   = {};
float qMain           = 0.0f;
float qZones[3]       = {};

float pRefFull = 15.0f;

uint8_t bufferIndex = 0;
uint8_t bufferCount = 0;

uint8_t mainLeakCounter   = 0;
uint8_t zoneLeakCounter[3] = {};

uint32_t lastSampleTime     = 0;
uint32_t lastMqttReconnect  = 0;
uint32_t lastWifiAttempt    = 0;

String lastAlarm = "NONE";

// ============================================================
// Forward declarations (publishTelemetry used by alarm helpers)
// ============================================================
void publishTelemetry();

// ============================================================
// ISRs
// ============================================================
void IRAM_ATTR isrMain() { pulsesMain++; }
void IRAM_ATTR isrZA()   { pulsesZA++;   }
void IRAM_ATTR isrZB()   { pulsesZB++;   }
void IRAM_ATTR isrZC()   { pulsesZC++;   }

// ============================================================
// Relay / valve helpers
// ============================================================
void writeRelay(uint8_t pin, bool energised) {
    if (RELAY_ACTIVE_HIGH) {
        digitalWrite(pin, energised ? HIGH : LOW);
    } else {
        digitalWrite(pin, energised ? LOW : HIGH);
    }
}

void setValve(uint8_t zone, bool open) {
    if (zone >= 3) {
        return;
    }
    writeRelay(valvePins[zone], open);
    valveState[zone] = open;
}

void closeAllValves() {
    for (uint8_t i = 0; i < 3; i++) {
        setValve(i, false);
    }
}

void openAllValves() {
    if (alarmActive || !sensorReady) {
        return;
    }
    for (uint8_t i = 0; i < 3; i++) {
        setValve(i, true);
    }
}

// ============================================================
// Pressure acquisition
// ------------------------------------------------------------
// SIMULATION_MODE:
//   ADC on pins 36/39/34/35 (Wokwi pots) mapping 0..4095 to 0..20 kPa.
// REAL HARDWARE:
//   ADS1115 single-ended read, apply divider inverse ratio (x3.0),
//   then XGZP6847A transfer function: P = (V_sensor - 0.5) * 10.
// ============================================================
float readPressureKPa(uint8_t channel) {
    if (channel >= 4) {
        return 0.0f;
    }

#if SIMULATION_MODE
    int raw = analogRead(simulationPressurePins[channel]);
    return (static_cast<float>(raw) / 4095.0f) * 20.0f;
#else
    int16_t raw = ads.readADC_SingleEnded(channel);
    float vAdc = ads.computeVolts(raw);
    float vSensor = vAdc * DIVIDER_INVERSE_RATIO;
    float pressure = (vSensor - 0.5f) * 10.0f;

    if (pressure < 0.0f) {
        pressure = 0.0f;
    }
    if (pressure > 40.0f) {
        pressure = 40.0f;
    }
    return pressure;
#endif
}

// ============================================================
// Moving average
// ============================================================
float calculateAverage(const float* buffer, uint8_t count) {
    if (count == 0) {
        return 0.0f;
    }
    float total = 0.0f;
    for (uint8_t i = 0; i < count; i++) {
        total += buffer[i];
    }
    return total / static_cast<float>(count);
}

void updatePressureReadings() {
    pMainBuffer[bufferIndex] = readPressureKPa(0);
    for (uint8_t i = 0; i < 3; i++) {
        pZoneBuffer[i][bufferIndex] = readPressureKPa(i + 1);
    }

    bufferIndex = (bufferIndex + 1) % WINDOW_SIZE;
    if (bufferCount < WINDOW_SIZE) {
        bufferCount++;
    }

    pMain = calculateAverage(pMainBuffer, bufferCount);
    for (uint8_t i = 0; i < 3; i++) {
        pZones[i] = calculateAverage(pZoneBuffer[i], bufferCount);
        pZonesComp[i] = pZones[i] - ELEVATION_COMPENSATION_KPA;
        if (pZonesComp[i] < 0.0f) {
            pZonesComp[i] = 0.0f;
        }
    }
}

// ============================================================
// Startup / reset helper: fill the moving average windows
// with real readings so the mean is correct from the very
// first detection cycle (fix for the Startup Bug).
// ============================================================
void fillPressureBuffers() {
    float mainReading = readPressureKPa(0);
    float zoneReadings[3] = {
        readPressureKPa(1),
        readPressureKPa(2),
        readPressureKPa(3)
    };

    for (uint8_t i = 0; i < WINDOW_SIZE; i++) {
        pMainBuffer[i] = mainReading;
        pZoneBuffer[0][i] = zoneReadings[0];
        pZoneBuffer[1][i] = zoneReadings[1];
        pZoneBuffer[2][i] = zoneReadings[2];
    }

    bufferIndex = 0;
    bufferCount = WINDOW_SIZE;

    pMain = mainReading;
    for (uint8_t i = 0; i < 3; i++) {
        pZones[i] = zoneReadings[i];
        pZonesComp[i] = pZones[i] - ELEVATION_COMPENSATION_KPA;
        if (pZonesComp[i] < 0.0f) {
            pZonesComp[i] = 0.0f;
        }
    }
}

// ============================================================
// Self-calibration
// ============================================================
void calibratePressureReference() {
    Serial.println("[CAL] Closing all valves for self-calibration...");
    closeAllValves();
    delay(1500);

    float total = 0.0f;
    const uint8_t readings = 20;

    for (uint8_t i = 0; i < readings; i++) {
        total += readPressureKPa(0);
        delay(100);
    }

    pRefFull = total / static_cast<float>(readings);

    if (pRefFull < 1.0f) {
        Serial.println("[CAL] WARNING: pressure reference too low, keeping fallback 15.0 kPa");
        pRefFull = 15.0f;
    }

    Serial.print("[CAL] Pressure reference: ");
    Serial.print(pRefFull, 2);
    Serial.println(" kPa");

    fillPressureBuffers();
    closeAllValves();
}

// ============================================================
// MQTT helpers
// ============================================================
void publishStatus(const char* status) {
#if MQTT_ENABLED
    if (mqttClient.connected()) {
        mqttClient.publish(MQTT_STATUS_TOPIC, status, true);
    }
#endif
}

void publishTelemetry() {
    char payload[512];
    snprintf(
        payload,
        sizeof(payload),
        "{\"P_main\":%.2f,\"Q_main\":%.3f,"
        "\"ZA\":{\"P\":%.2f,\"Q\":%.3f,\"V\":%d},"
        "\"ZB\":{\"P\":%.2f,\"Q\":%.3f,\"V\":%d},"
        "\"ZC\":{\"P\":%.2f,\"Q\":%.3f,\"V\":%d},"
        "\"alarm\":%s,\"alarm_type\":\"%s\",\"sensor_ready\":%s}",
        pMain, qMain,
        pZones[0], qZones[0], valveState[0] ? 1 : 0,
        pZones[1], qZones[1], valveState[1] ? 1 : 0,
        pZones[2], qZones[2], valveState[2] ? 1 : 0,
        alarmActive ? "true" : "false",
        lastAlarm.c_str(),
        sensorReady ? "true" : "false"
    );

    Serial.println(payload);

#if MQTT_ENABLED
    if (mqttClient.connected()) {
        mqttClient.publish(MQTT_TELEMETRY_TOPIC, payload);
    }
#endif
}

// ============================================================
// Alarm handling
// ------------------------------------------------------------
// The alarm payload is the SAME full JSON as normal telemetry.
// publishTelemetry() is invoked immediately after the state
// change so the dashboard sees alarm_type without waiting for
// the next 1-second loop tick.
// ============================================================
void activateMainAlarm(const String& message) {
    if (alarmActive) {
        return;
    }

    alarmActive = true;
    lastAlarm = message;

    closeAllValves();

    Serial.print("[ALARM] ");
    Serial.println(message);

    publishStatus("ALARM");
    publishTelemetry();  // immediate update to the dashboard
}

void resetAlarm() {
    alarmActive   = false;
    mainLeakCounter = 0;
    for (uint8_t i = 0; i < 3; i++) {
        zoneLeakCounter[i] = 0;
    }
    lastAlarm = "NONE";

    // Safety: RESET clears the alarm but keeps every valve closed.
    closeAllValves();

    // Refill the moving-average windows with fresh readings so
    // the next detection cycle starts from real data, not from
    // stale values captured during the fault window.
    fillPressureBuffers();

    Serial.println("[RESET] Alarm cleared. All valves remain CLOSED.");

    publishStatus("READY");
    publishTelemetry();  // immediate update to the dashboard
}

// ============================================================
// Minimal JSON value reader (int / bool only)
// ============================================================
bool readJsonValue(const char* payload, const char* key, int& value) {
    char searchKey[24];
    snprintf(searchKey, sizeof(searchKey), "\"%s\"", key);

    const char* position = strstr(payload, searchKey);
    if (position == nullptr) {
        return false;
    }

    position = strchr(position, ':');
    if (position == nullptr) {
        return false;
    }
    position++;

    while (*position == ' ' || *position == '\t') {
        position++;
    }

    if (strncmp(position, "true", 4) == 0) {
        value = 1;
        return true;
    }
    if (strncmp(position, "false", 5) == 0) {
        value = 0;
        return true;
    }
    if (*position == '1' || *position == '0') {
        value = *position - '0';
        return true;
    }
    return false;
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (strcmp(topic, MQTT_COMMAND_TOPIC) != 0) {
        return;
    }

    char message[256];
    if (length >= sizeof(message)) {
        return;
    }
    memcpy(message, payload, length);
    message[length] = '\0';

    int value = 0;

    if (readJsonValue(message, "RESET", value) && value == 1) {
        resetAlarm();
        return;
    }

    if (readJsonValue(message, "ALL", value)) {
        if (value == 0) {
            closeAllValves();
        } else if (!alarmActive && sensorReady) {
            openAllValves();
        }
    }

    const char* zoneKeys[3] = { "A", "B", "C" };
    for (uint8_t i = 0; i < 3; i++) {
        if (readJsonValue(message, zoneKeys[i], value)) {
            if (value == 0) {
                setValve(i, false);
            } else if (!alarmActive && sensorReady) {
                setValve(i, true);
            }
        }
    }

    Serial.print("[CMD] ");
    Serial.println(message);

    // Echo current state immediately so the dashboard reflects
    // the new valve positions without waiting for the next tick.
    publishTelemetry();
}

// ============================================================
// Wi-Fi / MQTT connectivity
// ============================================================
void connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) {
        return;
    }
    if (millis() - lastWifiAttempt < MQTT_RECONNECT_INTERVAL) {
        return;
    }
    lastWifiAttempt = millis();
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("[WIFI] Connecting to ");
    Serial.println(WIFI_SSID);
}

void reconnectMQTT() {
#if MQTT_ENABLED
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }
    if (mqttClient.connected()) {
        return;
    }
    if (millis() - lastMqttReconnect < MQTT_RECONNECT_INTERVAL) {
        return;
    }
    lastMqttReconnect = millis();

    bool connected;
    if (strlen(MQTT_USER) > 0) {
        connected = mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASSWORD);
    } else {
        connected = mqttClient.connect(MQTT_CLIENT_ID);
    }

    if (connected) {
        mqttConnected = true;
        mqttClient.subscribe(MQTT_COMMAND_TOPIC);
        publishStatus(alarmActive ? "ALARM" : "READY");
        Serial.println("[MQTT] Connected.");
    } else {
        mqttConnected = false;
        Serial.print("[MQTT] Connect failed, rc=");
        Serial.println(mqttClient.state());
    }
#endif
}

// ============================================================
// Detection rules (unified with app.py and anomaly.py)
// ============================================================
void detectMainLeak() {
    float sumZones = qZones[0] + qZones[1] + qZones[2];
    float flowDifference = fabs(qMain - sumZones);

    bool pressureDrop = (
        pRefFull > 1.0f &&
        pMain < pRefFull * MAIN_PRESSURE_LIMIT &&
        sumZones < MAIN_PRESSURE_SUMQ_MAX
    );

    bool flowImbalance = (
        qMain > MAIN_FLOW_MIN_Q &&
        flowDifference > MAIN_FLOW_ERROR_LIMIT
    );

    if (pressureDrop || flowImbalance) {
        if (mainLeakCounter < ALARM_CONFIRMATION_COUNT) {
            mainLeakCounter++;
        }
    } else {
        mainLeakCounter = 0;
    }

    if (mainLeakCounter >= ALARM_CONFIRMATION_COUNT) {
        activateMainAlarm("MAIN_MANIFOLD_LEAK");
    }
}

void detectZoneLeaks() {
    if (alarmActive) {
        return;
    }

    for (uint8_t i = 0; i < 3; i++) {
        if (!valveState[i]) {
            zoneLeakCounter[i] = 0;
            continue;
        }

        float ratio = 1.0f;
        if (pMain > 0.5f) {
            ratio = pZonesComp[i] / pMain;
        }

        bool pressureRatioDrop = (ratio < ZONE_PRESSURE_LIMIT);

        bool microLeak = (
            qZones[i] < MICRO_LEAK_FLOW_MAX &&
            pZonesComp[i] < MICRO_LEAK_PRESSURE * pMain
        );

        if (pressureRatioDrop || microLeak) {
            if (zoneLeakCounter[i] < ALARM_CONFIRMATION_COUNT) {
                zoneLeakCounter[i]++;
            }
        } else {
            zoneLeakCounter[i] = 0;
        }

        if (zoneLeakCounter[i] >= ALARM_CONFIRMATION_COUNT) {
            char alarmMessage[32];
            snprintf(alarmMessage, sizeof(alarmMessage), "ZONE_%c_LEAK", 'A' + i);

            setValve(i, false);

            Serial.print("[ALARM] ");
            Serial.println(alarmMessage);

            alarmActive = true;
            lastAlarm = alarmMessage;

            publishStatus("ALARM");
            publishTelemetry();  // immediate update to the dashboard

            zoneLeakCounter[i] = 0;
            break;  // one zone per tick; operator must RESET to continue
        }
    }
}

// ============================================================
// Flow sensors
// ============================================================
void readFlowSensors() {
    uint32_t mainCount;
    uint32_t zoneACount;
    uint32_t zoneBCount;
    uint32_t zoneCCount;

    noInterrupts();
    mainCount  = pulsesMain;
    zoneACount = pulsesZA;
    zoneBCount = pulsesZB;
    zoneCCount = pulsesZC;
    pulsesMain = 0;
    pulsesZA   = 0;
    pulsesZB   = 0;
    pulsesZC   = 0;
    interrupts();

    qMain = ((float)mainCount / CAL_YFS201) * 60.0f;

    qZones[0] = ((float)zoneACount / CAL_YFS401) * 60.0f;
    qZones[1] = ((float)zoneBCount / CAL_YFS401) * 60.0f;
    qZones[2] = ((float)zoneCCount / CAL_YFS401) * 60.0f;
}

// ============================================================
// Initialisation
// ============================================================
void initializeSensors() {
#if !SIMULATION_MODE
    if (!ads.begin(0x48)) {
        sensorReady = false;
        closeAllValves();
        Serial.println("[ERROR] ADS1115 init failed.");
        return;
    }
    ads.setGain(GAIN_TWOTHIRDS);
    ads.setDataRate(RATE_ADS1115_128SPS);
#else
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
#endif
    sensorReady = true;
}

void initializePins() {
    for (uint8_t i = 0; i < 3; i++) {
        pinMode(valvePins[i], OUTPUT);
    }
    closeAllValves();

    pinMode(FLOW_MAIN_PIN, INPUT_PULLUP);
    pinMode(FLOW_ZA_PIN,   INPUT_PULLUP);
    pinMode(FLOW_ZB_PIN,   INPUT_PULLUP);
    pinMode(FLOW_ZC_PIN,   INPUT_PULLUP);

    attachInterrupt(digitalPinToInterrupt(FLOW_MAIN_PIN), isrMain, FALLING);
    attachInterrupt(digitalPinToInterrupt(FLOW_ZA_PIN),   isrZA,   FALLING);
    attachInterrupt(digitalPinToInterrupt(FLOW_ZB_PIN),   isrZB,   FALLING);
    attachInterrupt(digitalPinToInterrupt(FLOW_ZC_PIN),   isrZC,   FALLING);
}

void setup() {
    Serial.begin(115200);
    delay(500);

    initializePins();
    Wire.begin(21, 22);
    initializeSensors();

    if (!sensorReady) {
        closeAllValves();
        return;
    }

    calibratePressureReference();

#if MQTT_ENABLED
    connectWiFi();
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);
    reconnectMQTT();
#endif

    Serial.println("[BOOT] Smart Water Leak Detection System Ready.");
    Serial.println("[BOOT] All valves are initially CLOSED.");
}

void loop() {
    if (!sensorReady) {
        closeAllValves();
        delay(1000);
        return;
    }

#if MQTT_ENABLED
    connectWiFi();
    reconnectMQTT();
    if (mqttClient.connected()) {
        mqttClient.loop();
    }
#endif

    uint32_t currentTime = millis();

    if (currentTime - lastSampleTime >= SAMPLE_INTERVAL) {
        lastSampleTime = currentTime;

        readFlowSensors();
        updatePressureReadings();

        if (!alarmActive) {
            detectMainLeak();
            detectZoneLeaks();
        }

        publishTelemetry();
    }
}
