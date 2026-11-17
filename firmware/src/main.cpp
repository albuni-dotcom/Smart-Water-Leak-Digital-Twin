
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <Adafruit_ADS1X15.h>
#include <math.h>
#include <string.h>

#define SIMULATION_MODE true
#define MQTT_ENABLED true

#define FLOW_MAIN_PIN 27
#define FLOW_ZA_PIN 26
#define FLOW_ZB_PIN 25
#define FLOW_ZC_PIN 33

#define VALVE_A_PIN 16
#define VALVE_B_PIN 17
#define VALVE_C_PIN 18

#define RELAY_ACTIVE_HIGH true

const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const char* MQTT_SERVER = "192.168.1.100";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER = "";
const char* MQTT_PASSWORD = "";

const char* MQTT_CLIENT_ID = "smart_water_esp32";
const char* MQTT_COMMAND_TOPIC = "smartwater/device/cmd";
const char* MQTT_TELEMETRY_TOPIC = "smartwater/device/telemetry";
const char* MQTT_STATUS_TOPIC = "smartwater/device/status";

const float CAL_YFS201 = 450.0f;
const float CAL_YFS401 = 5880.0f;

const float MAIN_FLOW_ERROR_LIMIT = 0.8f;
const float MAIN_PRESSURE_LIMIT = 0.80f;
const float ZONE_PRESSURE_LIMIT = 0.65f;
const float MICRO_LEAK_PRESSURE_LIMIT = 0.88f;

const uint32_t SAMPLE_INTERVAL = 1000;
const uint32_t MQTT_RECONNECT_INTERVAL = 5000;
const uint8_t WINDOW_SIZE = 10;
const uint8_t ALARM_CONFIRMATION_COUNT = 3;

Adafruit_ADS1115 ads;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

volatile uint32_t pulsesMain = 0;
volatile uint32_t pulsesZA = 0;
volatile uint32_t pulsesZB = 0;
volatile uint32_t pulsesZC = 0;

const uint8_t valvePins[3] = {
    VALVE_A_PIN,
    VALVE_B_PIN,
    VALVE_C_PIN
};

const uint8_t simulationPressurePins[4] = {
    36, 39, 34, 35
};

bool valveState[3] = {
    false,
    false,
    false
};

bool alarmActive = false;
bool sensorReady = true;
bool mqttConnected = false;

float pMainBuffer[WINDOW_SIZE] = {};
float pZoneBuffer[3][WINDOW_SIZE] = {};

float pMain = 0.0f;
float pZones[3] = {};
float qMain = 0.0f;
float qZones[3] = {};

float pRefFull = 15.0f;
float baselineRatios[3] = {
    0.85f,
    0.85f,
    0.85f
};

uint8_t bufferIndex = 0;
uint8_t bufferCount = 0;

uint8_t mainLeakCounter = 0;
uint8_t zoneLeakCounter[3] = {};

uint32_t lastSampleTime = 0;
uint32_t lastMqttReconnect = 0;

String lastAlarm = "NONE";

void IRAM_ATTR isrMain() {
    pulsesMain++;
}

void IRAM_ATTR isrZA() {
    pulsesZA++;
}

void IRAM_ATTR isrZB() {
    pulsesZB++;
}

void IRAM_ATTR isrZC() {
    pulsesZC++;
}

void writeRelay(uint8_t pin, bool enabled) {
    if (RELAY_ACTIVE_HIGH) {
        digitalWrite(pin, enabled ? HIGH : LOW);
    } else {
        digitalWrite(pin, enabled ? LOW : HIGH);
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

float readPressureKPa(uint8_t channel) {
    if (channel >= 4) {
        return 0.0f;
    }

#if SIMULATION_MODE
    int raw = analogRead(simulationPressurePins[channel]);
    return (static_cast<float>(raw) / 4095.0f) * 20.0f;
#else
    int16_t raw = ads.readADC_SingleEnded(channel);
    float voltage = ads.computeVolts(raw);
    float pressure = (voltage - 0.5f) * 10.0f;

    if (pressure < 0.0f) {
        pressure = 0.0f;
    }

    if (pressure > 40.0f) {
        pressure = 40.0f;
    }

    return pressure;
#endif
}

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

float calculateFlow(uint32_t pulses, float pulsesPerLiter) {
    return (static_cast<float>(pulses) / pulsesPerLiter) * 60.0f;
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
    }
}

void calibratePressureReference() {
    closeAllValves();
    delay(1500);

    float total = 0.0f;
    const uint8_t readings = 20;

    for (uint8_t i = 0; i < readings; i++) {
        total += readPressureKPa(0);
        delay(100);
    }

    pRefFull = total / static_cast<float>(readings);

    if (pRefFull < 0.1f) {
        pRefFull = 15.0f;
    }

    Serial.print("Pressure reference: ");
    Serial.print(pRefFull, 2);
    Serial.println(" kPa");

    closeAllValves();
}

void publishStatus(const char* status) {
#if MQTT_ENABLED
    if (mqttClient.connected()) {
        mqttClient.publish(MQTT_STATUS_TOPIC, status, true);
    }
#endif
}

void activateAlarm(const String& message) {
    if (alarmActive) {
        return;
    }

    alarmActive = true;
    lastAlarm = message;

    closeAllValves();

    Serial.print("ALARM: ");
    Serial.println(message);

    publishStatus("ALARM");

#if MQTT_ENABLED
    if (mqttClient.connected()) {
        String payload = "{\"alarm\":\"" + message + "\",\"active\":true}";
        mqttClient.publish(MQTT_TELEMETRY_TOPIC, payload.c_str());
    }
#endif
}

void resetAlarm() {
    alarmActive = false;
    mainLeakCounter = 0;

    for (uint8_t i = 0; i < 3; i++) {
        zoneLeakCounter[i] = 0;
    }

    lastAlarm = "NONE";
    closeAllValves();

    Serial.println("Alarm reset. All valves remain closed.");
    publishStatus("READY");
}

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

    const char* zoneKeys[3] = {
        "A",
        "B",
        "C"
    };

    for (uint8_t i = 0; i < 3; i++) {
        if (readJsonValue(message, zoneKeys[i], value)) {
            if (value == 0) {
                setValve(i, false);
            } else if (!alarmActive && sensorReady) {
                setValve(i, true);
            }
        }
    }

    Serial.print("MQTT command received: ");
    Serial.println(message);
}

void connectWiFi() {
#if !SIMULATION_MODE
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    uint32_t startTime = millis();

    while (WiFi.status() != WL_CONNECTED && millis() - startTime < 20000) {
        delay(250);
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("WiFi connected: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("WiFi connection failed.");
    }
#endif
}

void reconnectMQTT() {
#if MQTT_ENABLED && !SIMULATION_MODE
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
        connected = mqttClient.connect(
            MQTT_CLIENT_ID,
            MQTT_USER,
            MQTT_PASSWORD
        );
    } else {
        connected = mqttClient.connect(MQTT_CLIENT_ID);
    }

    if (connected) {
        mqttConnected = true;
        mqttClient.subscribe(MQTT_COMMAND_TOPIC);
        publishStatus(alarmActive ? "ALARM" : "READY");
        Serial.println("MQTT connected.");
    } else {
        mqttConnected = false;
        Serial.print("MQTT connection failed: ");
        Serial.println(mqttClient.state());
    }
#endif
}

void publishTelemetry() {
    String payload = "{";

    payload += "\"P_main\":";
    payload += String(pMain, 2);

    payload += ",\"Q_main\":";
    payload += String(qMain, 3);

    payload += ",\"ZA\":{\"P\":";
    payload += String(pZones[0], 2);
    payload += ",\"Q\":";
    payload += String(qZones[0], 3);
    payload += ",\"V\":";
    payload += valveState[0] ? "1" : "0";
    payload += "}";

    payload += ",\"ZB\":{\"P\":";
    payload += String(pZones[1], 2);
    payload += ",\"Q\":";
    payload += String(qZones[1], 3);
    payload += ",\"V\":";
    payload += valveState[1] ? "1" : "0";
    payload += "}";

    payload += ",\"ZC\":{\"P\":";
    payload += String(pZones[2], 2);
    payload += ",\"Q\":";
    payload += String(qZones[2], 3);
    payload += ",\"V\":";
    payload += valveState[2] ? "1" : "0";
    payload += "}";

    payload += ",\"alarm\":";
    payload += alarmActive ? "true" : "false";

    payload += ",\"alarm_type\":\"";
    payload += lastAlarm;
    payload += "\"";

    payload += ",\"sensor_ready\":";
    payload += sensorReady ? "true" : "false";

    payload += "}";

    Serial.println(payload);

#if MQTT_ENABLED && !SIMULATION_MODE
    if (mqttClient.connected()) {
        mqttClient.publish(
            MQTT_TELEMETRY_TOPIC,
            payload.c_str()
        );
    }
#endif
}

void detectMainLeak() {
    float sumZones = qZones[0] + qZones[1] + qZones[2];
    float flowDifference = fabs(qMain - sumZones);

    bool pressureDrop = (
        pRefFull > 0.5f &&
        pMain < pRefFull * MAIN_PRESSURE_LIMIT &&
        sumZones < 0.5f
    );

    bool flowImbalance = (
        qMain > 1.0f &&
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
        activateAlarm("MAIN_MANIFOLD_LEAK");
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
            ratio = pZones[i] / pMain;
        }

        bool pressureRatioDrop = (
            ratio < baselineRatios[i] * ZONE_PRESSURE_LIMIT
        );

        bool microLeak = (
            qZones[i] < 0.05f &&
            pRefFull > 0.5f &&
            pZones[i] < pRefFull * MICRO_LEAK_PRESSURE_LIMIT
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

            snprintf(
                alarmMessage,
                sizeof(alarmMessage),
                "ZONE_%c_LEAK",
                'A' + i
            );

            setValve(i, false);

            Serial.print("Zone leak detected: ");
            Serial.println(alarmMessage);

            lastAlarm = alarmMessage;

            zoneLeakCounter[i] = 0;

#if MQTT_ENABLED && !SIMULATION_MODE
            if (mqttClient.connected()) {
                String payload = "{\"alarm\":\"";
                payload += alarmMessage;
                payload += "\",\"zone\":\"";
                payload += static_cast<char>('A' + i);
                payload += "\",\"active\":true}";

                mqttClient.publish(
                    MQTT_TELEMETRY_TOPIC,
                    payload.c_str()
                );
            }
#endif
        }
    }
}

void readFlowSensors() {
    uint32_t mainCount;
    uint32_t zoneACount;
    uint32_t zoneBCount;
    uint32_t zoneCCount;

    noInterrupts();

    mainCount = pulsesMain;
    zoneACount = pulsesZA;
    zoneBCount = pulsesZB;
    zoneCCount = pulsesZC;

    pulsesMain = 0;
    pulsesZA = 0;
    pulsesZB = 0;
    pulsesZC = 0;

    interrupts();

    qMain = calculateFlow(mainCount, CAL_YFS201);

    qZones[0] = calculateFlow(zoneACount, CAL_YFS401);
    qZones[1] = calculateFlow(zoneBCount, CAL_YFS401);
    qZones[2] = calculateFlow(zoneCCount, CAL_YFS401);
}

void initializeSensors() {
#if !SIMULATION_MODE
    if (!ads.begin(0x48)) {
        sensorReady = false;
        closeAllValves();
        Serial.println("ADS1115 initialization failed.");
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
    pinMode(FLOW_ZA_PIN, INPUT_PULLUP);
    pinMode(FLOW_ZB_PIN, INPUT_PULLUP);
    pinMode(FLOW_ZC_PIN, INPUT_PULLUP);

    attachInterrupt(
        digitalPinToInterrupt(FLOW_MAIN_PIN),
        isrMain,
        FALLING
    );

    attachInterrupt(
        digitalPinToInterrupt(FLOW_ZA_PIN),
        isrZA,
        FALLING
    );

    attachInterrupt(
        digitalPinToInterrupt(FLOW_ZB_PIN),
        isrZB,
        FALLING
    );

    attachInterrupt(
        digitalPinToInterrupt(FLOW_ZC_PIN),
        isrZC,
        FALLING
    );
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

#if MQTT_ENABLED && !SIMULATION_MODE
    connectWiFi();

    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);

    reconnectMQTT();
#endif

    Serial.println("Smart Water Leak Detection System Ready.");
    Serial.println("All valves are initially closed.");
}

void loop() {
    if (!sensorReady) {
        closeAllValves();
        delay(1000);
        return;
    }

#if MQTT_ENABLED && !SIMULATION_MODE
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
    }

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

