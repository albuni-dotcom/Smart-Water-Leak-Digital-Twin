#include <Arduino.h>
#include <Wire.h> // بروتوكول I2C للتخاطب مع ADS1115
#include <WiFi.h>
#include <PubSubClient.h> // بروتوكول MQTT لإرسال القراءات واستقبال الأوامر
#include <Adafruit_ADS1X15.h> // مكتبة محول الإشارة عالي الدقة 16-bit
#define SIMULATION true // غيّرها إلى false عند الرفع على البوردة الحقيقية
#define MQTT_ENABLED true // اجعلها false لاختبار الحساسات محلياً بدون إنترنت
#define FLOW_PMAIN 27
#define FLOW_PA 26
#define FLOW_PB 25
#define FLOW_PC 33
#define VALVE_PA 16
#define VALVE_PB 17
#define VALVE_PC 18
#define RELAY_ACTIVE_HIGH true //يوجد احتمال لتغييرها لfalse لاختلاف منطق الإغلاق والفتح
const char* WIFI = "Wokwi-GUEST";
const char* PASSWORD = "";
//عند التطبيق في الواقع نغير الاسم وكلمة السر للاتصال بشبكة واقعية
const char* MQTT_SERVER = "broker.hivemq.com";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_ID = "smart_water_esp32_shopak_2026";
const char* MQTT_READ = "smartwater/shopak2026/device/cmd";
const char* MQTT_SEND = "smartwater/shopak2026/device/telemetry";
const char* MQTT_STATUS = "smartwater/shopak2026/device/status";
const float YFS201 = 450.0f;
const float YFS401 = 5880.0f;
//أرقام النبضات نظرية يجب تجربتها واستبدالها بالواقعية
const float DIVIDER_COMPENSATION = 3.0f;
//يجب أن يتم الأخذ في الحسبان نسبة الخطأ الواردة في المقاومات على أرض الواقع من الممكن تعديلها لتكون 3.08
const float ELEVATION_COMPENSATION = 4.316f;
//يجب تعديلها لو اختلف فرق الارتفاع بين حساس الضغط الرئيسي وحساسات ضغط الفروع
const float MAIN_FLOW_ERROR = 0.45f;
const float MAIN_FLOW_MIN_Q = 0.60f;

const float MAIN_PRESSURE_LIMIT = 0.80f;
const float MAIN_PRESSURE_SUMQ_MAX = 0.15f;// FLOW

const float ZONE_PRESSURE_LIMIT = 0.65f;

const float DRIPP_FLOW_MAX = 0.05f;
const float DRIPP_PRESSURE = 0.88f;

const uint32_t CHECK_TIME_CYCLE = 1000;
const uint32_t MQTT_RECONNECT= 5000;
const uint8_t  NUMBER_DATA_CYCLE = 5;
const uint8_t  ALARM_CONFIRMATION = 3;
Adafruit_ADS1115 ads;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
volatile uint32_t pulses_Main = 0;
volatile uint32_t pulses_A = 0;
volatile uint32_t pulses_B = 0;
volatile uint32_t pulses_C = 0;
const uint8_t valve_P[3] = { VALVE_PA, VALVE_PB, VALVE_PC };
#if SIMULATION
const uint8_t simulation_Pressure_P[4] = { 36, 39, 34, 35 };
#endif
bool valveState[3] = { false, false, false };
bool alarm_active = false;
bool main_Alarm = false;                
bool zone_Alarm[3] = { false, false, false };
bool sensor_ready = true;
float pMain_Buffer[NUMBER_DATA_CYCLE] = {};
float pZone_Buffer[3][NUMBER_DATA_CYCLE] = {};
float pMain = 0.0f;
float pZones[3] = {};
float pZones_Comp[3] = {};
float qMain = 0.0f;
float qZones[3] = {};
float pFull = 15.0f;
uint8_t buffer_Index = 0;
uint8_t buffer_Count = 0;
uint8_t main_Leak_Counter = 0;
uint8_t zone_Leak_Counter[3] = {};
uint32_t last_Sample_Time = 0;
uint32_t last_Mqtt_Reconnect = 0;
uint32_t last_Wifi_Attempt = 0;
String lastAlarm = "NONE";
void publish_SEND();
void IRAM_ATTR isrMain() { pulses_Main++; }
void IRAM_ATTR isrA() { pulses_A++;   }
void IRAM_ATTR isrB() { pulses_B++;   }
void IRAM_ATTR isrC() { pulses_C++;   }
void write_Relay(uint8_t pin, bool open) {
    digitalWrite(pin, (open == RELAY_ACTIVE_HIGH) ? HIGH : LOW);
}
void set_Valve(uint8_t zone, bool open) {
    write_Relay(valve_P[zone], open);
    valveState[zone] = open;
}
void close_All_Valves() {
    for (uint8_t i = 0; i < 3; i++) {
        set_Valve(i, false);
    }
}
void open_All_Valves() {
    if (main_Alarm || !sensor_ready) {
        return;
    }
    for (uint8_t i = 0; i < 3; i++) {
        if (!zone_Alarm[i]) {
            set_Valve(i, true);
        }
    }
}
float read_Pressure_KPa(uint8_t channel) {
#if SIMULATION
    int raw = analogRead(simulation_Pressure_P[channel]);
    return (static_cast<float>(raw) / 4095.0f) * 20.0f;
#else
    int16_t raw = ads.readADC_SingleEnded(channel);
    float vAdc = ads.computeVolts(raw);
    float vSensor = vAdc * DIVIDER_COMPENSATION;
    float pressure = (vSensor - 0.5f) * 10.0f;
    return constrain(pressure, 0.0f, 40.0f);
#endif
}
float calculate_Average(const float* buffer, uint8_t count) {
    float total = 0.0f;
    for (uint8_t i = 0; i < count; i++) {
        total += buffer[i];
    }
    return total / static_cast<float>(count);
}
void update_Pressure_Readings() {
    pMain_Buffer[buffer_Index] = read_Pressure_KPa(0);
    for (uint8_t i = 0; i < 3; i++) {
        pZone_Buffer[i][buffer_Index] = read_Pressure_KPa(i + 1);
    }
    buffer_Index = (buffer_Index + 1) % NUMBER_DATA_CYCLE;
    if (buffer_Count < NUMBER_DATA_CYCLE) {
        buffer_Count++;
    }
    pMain = calculate_Average(pMain_Buffer, buffer_Count);
    for (uint8_t i = 0; i < 3; i++) {
        pZones[i] = calculate_Average(pZone_Buffer[i], buffer_Count);
        pZones_Comp[i] = pZones[i] - ELEVATION_COMPENSATION;
        if (pZones_Comp[i] < 0.0f) {
            pZones_Comp[i] = 0.0f;
        }
    }
}
void fill_Pressure_Buffers() {
    float mainReading = read_Pressure_KPa(0);
    float zoneReadings[3] = {
        read_Pressure_KPa(1),
        read_Pressure_KPa(2),
        read_Pressure_KPa(3)
    };
    for (uint8_t i = 0; i < NUMBER_DATA_CYCLE; i++) {
        pMain_Buffer[i] = mainReading;
        pZone_Buffer[0][i] = zoneReadings[0];
        pZone_Buffer[1][i] = zoneReadings[1];
        pZone_Buffer[2][i] = zoneReadings[2];
    }
    buffer_Index = 0;
    buffer_Count = NUMBER_DATA_CYCLE;
    pMain = mainReading;
    for (uint8_t i = 0; i < 3; i++) {
        pZones[i] = zoneReadings[i];
        pZones_Comp[i] = pZones[i] - ELEVATION_COMPENSATION;
        if (pZones_Comp[i] < 0.0f) {
            pZones_Comp[i] = 0.0f;
        }
    }
}
void calibrate_Pressure_Reference() {
    Serial.println("[CAL] Closing all valves for self-calibration...");
    close_All_Valves();
    delay(1500);
    float total = 0.0f;
    for (uint8_t i = 0; i < 20; i++) {
        total += read_Pressure_KPa(0);
        delay(100);
    }
    pFull = total / 20.0f;
    if (pFull < 1.0f) {
        Serial.println("[CAL] ERROR: Tank empty or main sensor disconnected!");
        pFull = 0.0f;
        alarm_active = true;
        lastAlarm = "LOW_TANK_LEVEL";
        fill_Pressure_Buffers();
        return;
    }
    Serial.print("[CAL] Pressure reference: ");
    Serial.print(pFull, 2);
    Serial.println(" kPa");
    fill_Pressure_Buffers();
}

void publish_Status(const char* status) {
#if MQTT_ENABLED
    if (mqttClient.connected()) {
        mqttClient.publish(MQTT_STATUS, status, true);
    }
#endif
}
void publish_SEND() {
    char payload[512];
    snprintf(
        payload,
        sizeof(payload),
        "{\"P_main\":%.2f,\"Q_main\":%.3f,"
        "\"A\":{\"P\":%.2f,\"Q\":%.3f,\"V\":%d},"
        "\"B\":{\"P\":%.2f,\"Q\":%.3f,\"V\":%d},"
        "\"C\":{\"P\":%.2f,\"Q\":%.3f,\"V\":%d},"
        "\"alarm\":%s,\"alarm_type\":\"%s\",\"sensor_ready\":%s}",
        pMain, qMain,
        pZones[0], qZones[0], valveState[0] ? 1 : 0,
        pZones[1], qZones[1], valveState[1] ? 1 : 0,
        pZones[2], qZones[2], valveState[2] ? 1 : 0,
        alarm_active ? "true" : "false",
        lastAlarm.c_str(),
        sensor_ready ? "true" : "false"
    );
    Serial.println(payload);
#if MQTT_ENABLED
    if (mqttClient.connected()) {
        mqttClient.publish(MQTT_SEND, payload);
    }
#endif
}
void activate_Main_Alarm(const String& message) {
    if (main_Alarm) {
        return;
    }
    main_Alarm   = true;
    alarm_active = true;
    lastAlarm    = message;
    close_All_Valves();
    Serial.print("[ALARM] ");
    Serial.println(message);
    publish_Status("ALARM");
    publish_SEND();
}
void update_Global_Alarm_State() {
    alarm_active = (main_Alarm || zone_Alarm[0] || zone_Alarm[1] || zone_Alarm[2]);
    if (!alarm_active) {
        lastAlarm = "NONE";
        publish_Status("READY");
    } else if (!main_Alarm) {
        String activeZones = "";
        for (uint8_t i = 0; i < 3; i++) {
            if (zone_Alarm[i]) {
                if (activeZones.length() > 0) activeZones += "_";
                activeZones += String((char)('A' + i));
            }
        }
        lastAlarm = "ZONE_" + activeZones + "_LEAK";
    }
}
void reset_Zone_Alarm(uint8_t zone) {
    if (zone >= 3) return;
    zone_Alarm[zone]        = false;
    zone_Leak_Counter[zone] = 0;
    set_Valve(zone, false);
    fill_Pressure_Buffers();
    update_Global_Alarm_State();
    Serial.printf("[RESET] Zone %c alarm cleared.\n", 'A' + zone);
    publish_SEND();
}
void reset_Alarm() {
    main_Alarm        = false;
    alarm_active      = false;
    main_Leak_Counter = 0;
    for (uint8_t i = 0; i < 3; i++) {
        zone_Alarm[i]        = false;
        zone_Leak_Counter[i] = 0;
    }
    lastAlarm = "NONE";
    close_All_Valves();
    fill_Pressure_Buffers();
    Serial.println("[RESET] All alarms cleared. All valves remain CLOSED.");
    publish_Status("READY");
    publish_SEND();
}
bool read_Json_Value(const char* payload, const char* key, int& value) {
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
void mqtt_Call_back(char* topic, byte* payload, unsigned int length) {
    if (strcmp(topic, MQTT_READ) != 0) {
        return;
    }
    char message[256];
    if (length >= sizeof(message)) {
        return;
    }
    memcpy(message, payload, length);
    message[length] = '\0';
    int value = 0;
    if (read_Json_Value(message, "RESET", value) && value == 1) {
        reset_Alarm();
        return;
    }
    const char* resetKeys[3] = { "RESET_A", "RESET_B", "RESET_C" };
    for (uint8_t i = 0; i < 3; i++) {
        if (read_Json_Value(message, resetKeys[i], value) && value == 1) {
            reset_Zone_Alarm(i);
            return;
        }
    }
    if (read_Json_Value(message, "ALL", value)) {
        if (value == 0) {
            close_All_Valves();
        } else {
            open_All_Valves();
        }
    }
    const char* zoneKeys[3] = { "A", "B", "C" };
    for (uint8_t i = 0; i < 3; i++) {
        if (read_Json_Value(message, zoneKeys[i], value)) {
            if (value == 0) {
                set_Valve(i, false);
            } else if (!main_Alarm && !zone_Alarm[i] && sensor_ready) {
                set_Valve(i, true);
            }
        }
    }
    Serial.print("[CMD] ");
    Serial.println(message);
    publish_SEND();
}
void connect_WiFi() {
    if (WiFi.status() == WL_CONNECTED) {
        return;
    }
    if (millis() - last_Wifi_Attempt < MQTT_RECONNECT) {
        return;
    }
    last_Wifi_Attempt = millis();
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI, PASSWORD);
    Serial.print("[WIFI] Connecting to ");
    Serial.println(WIFI);
}
void reconnect_MQTT() {
#if MQTT_ENABLED
    if (WiFi.status() != WL_CONNECTED || mqttClient.connected()) {
        return;
    }
    if (millis() - last_Mqtt_Reconnect < MQTT_RECONNECT) {
        return;
    }
    last_Mqtt_Reconnect = millis();
    bool connected = mqttClient.connect(MQTT_ID);
    if (connected) {
        mqttClient.subscribe(MQTT_READ);
        publish_Status(alarm_active ? "ALARM" : "READY");
        Serial.println("[MQTT] Connected.");
    } else {
        Serial.print("[MQTT] Connect failed, rc=");
        Serial.println(mqttClient.state());
    }
#endif
}
void detect_Main_Leak() {
    float sumZones = qZones[0] + qZones[1] + qZones[2];
    float flowDifference = fabs(qMain - sumZones);
    bool allValvesClosed = (!valveState[0] && !valveState[1] && !valveState[2]);
    bool pressureDrop = (
        allValvesClosed &&
        pFull > 1.0f &&
        pMain < pFull * MAIN_PRESSURE_LIMIT &&
        sumZones < MAIN_PRESSURE_SUMQ_MAX
    );
    bool flowImbalance = (
        qMain > MAIN_FLOW_MIN_Q &&
        flowDifference > MAIN_FLOW_ERROR
    );
    if (pressureDrop || flowImbalance) {
        if (main_Leak_Counter < ALARM_CONFIRMATION) {
            main_Leak_Counter++;
        }
    } else {
        main_Leak_Counter = 0;
    }
    if (main_Leak_Counter >= ALARM_CONFIRMATION) {
        activate_Main_Alarm("MAIN_MANIFOLD_LEAK");
    }
}
void detect_Zone_Leaks() {
    for (uint8_t i = 0; i < 3; i++) {
        if (!valveState[i] || zone_Alarm[i]) {
            zone_Leak_Counter[i] = 0;
            continue;
        }
        float ratio = 1.0f;
        if (pMain > 0.5f) {
            ratio = pZones_Comp[i] / pMain;
        }
        bool pressureRatioDrop = (ratio < ZONE_PRESSURE_LIMIT);
        bool microLeak = (
            qZones[i] < DRIPP_FLOW_MAX &&
            pZones_Comp[i] < DRIPP_PRESSURE * pMain
        );
        if (pressureRatioDrop || microLeak) {
            if (zone_Leak_Counter[i] < ALARM_CONFIRMATION) {
                zone_Leak_Counter[i]++;
            }
        } else {
            zone_Leak_Counter[i] = 0;
        }
        if (zone_Leak_Counter[i] >= ALARM_CONFIRMATION) {
            char alarmMessage[32];
            snprintf(alarmMessage, sizeof(alarmMessage), "ZONE_%c_LEAK", 'A' + i);
            set_Valve(i, false);
            zone_Alarm[i] = true;
            alarm_active  = true;
            lastAlarm     = alarmMessage;
            Serial.print("[ALARM] ");
            Serial.println(alarmMessage);
            publish_Status("ALARM");
            publish_SEND();
            zone_Leak_Counter[i] = 0;
        }
    }
}
void read_Flow_Sensors() {
    uint32_t mainCount;
    uint32_t ACount;
    uint32_t BCount;
    uint32_t CCount;
    noInterrupts();
    mainCount = pulses_Main;
    ACount = pulses_A;
    BCount = pulses_B;
    CCount = pulses_C;
    pulses_Main = 0;
    pulses_A    = 0;
    pulses_B    = 0;
    pulses_C    = 0;
    interrupts();
    qMain = ((float)mainCount / YFS201) * 60.0f;
    qZones[0] = ((float)ACount / YFS401) * 60.0f;
    qZones[1] = ((float)BCount / YFS401) * 60.0f;
    qZones[2] = ((float)CCount / YFS401) * 60.0f;
}
void initialize_Sensors() {
#if !SIMULATION
    if (!ads.begin(0x48)) {
        sensor_ready = false;
        close_All_Valves();
        Serial.println("[ERROR] ADS1115 init failed.");
        return;
    }
    ads.setGain(GAIN_TWOTHIRDS);
    ads.setDataRate(RATE_ADS1115_128SPS);
#else
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
#endif
    sensor_ready = true;
}
void initialize_Pins() {
    for (uint8_t i = 0; i < 3; i++) {
        pinMode(valve_P[i], OUTPUT);
    }
    close_All_Valves();
    pinMode(FLOW_PMAIN, INPUT_PULLUP);
    pinMode(FLOW_PA,INPUT_PULLUP);
    pinMode(FLOW_PB,INPUT_PULLUP);
    pinMode(FLOW_PC,INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(FLOW_PMAIN), isrMain, FALLING);
    attachInterrupt(digitalPinToInterrupt(FLOW_PA), isrA,    FALLING);
    attachInterrupt(digitalPinToInterrupt(FLOW_PB), isrB,    FALLING);
    attachInterrupt(digitalPinToInterrupt(FLOW_PC), isrC,    FALLING);
}
void setup() {
    Serial.begin(115200);
    delay(500);
    initialize_Pins();
    Wire.begin(21, 22);
    initialize_Sensors();
    if (!sensor_ready) {
        close_All_Valves();
        return;
    }
    calibrate_Pressure_Reference();
#if MQTT_ENABLED
    connect_WiFi();
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqtt_Call_back);
    mqttClient.setBufferSize(512);
    reconnect_MQTT();
#endif
    Serial.println("[BOOT] Smart Water Leak Detection System Ready.");
    Serial.println("[BOOT] All valves are initially CLOSED.");
}
void loop() {
    if (!sensor_ready) {
        close_All_Valves();
        delay(1000);
        return;
    }
#if MQTT_ENABLED
    connect_WiFi();
    reconnect_MQTT();
    if (mqttClient.connected()) {
        mqttClient.loop();
    }
#endif
    uint32_t currentTime = millis();
    if (currentTime - last_Sample_Time >= CHECK_TIME_CYCLE) {
        last_Sample_Time = currentTime;
        read_Flow_Sensors();
        update_Pressure_Readings();
        detect_Main_Leak();
        detect_Zone_Leaks();
        publish_SEND();
    }
}
