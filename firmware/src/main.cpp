#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>

// غيّر هذه القيمة إلى false عند رفع الكود على القطع الحقيقية (ADS1115)
#define SIMULATION_MODE true

// تعريف أطراف حساسات التدفق (Interrupt Pins)
#define FLOW_MAIN_PIN 27
#define FLOW_ZA_PIN   26
#define FLOW_ZB_PIN   25
#define FLOW_ZC_PIN   33

// تعريف أطراف صمامات العزل (Relay Pins)
#define VALVE_A_PIN   16
#define VALVE_B_PIN   17
#define VALVE_C_PIN   18

Adafruit_ADS1115 ads;

// عدادات النبضات اللحظية
volatile uint32_t pulsesMain = 0;
volatile uint32_t pulsesZA = 0;
volatile uint32_t pulsesZB = 0;
volatile uint32_t pulsesZC = 0;

// معاملات التحويل المعتمدة (Pulse per Liter)
const float CAL_YFS201 = 450.0;  // الخط الرئيسي
const float CAL_YFS401 = 5880.0; // الفروع الثلاثة

// مرشح المتوسط المتحرك (نافذة 10 قراءات)
const int WINDOW_SIZE = 10;
float pMainBuf[WINDOW_SIZE] = {0};
float pZoneBuf[3][WINDOW_SIZE] = {0};
int bufIdx = 0;

float pRefFull = 15.0; // كيلوباسكال (يتم تحديثه بالمعايرة الذاتية)
float baselineRatios[3] = {0.85, 0.85, 0.85};
bool valveState[3] = {true, true, true}; // true = مفتوح

// دوال المقاطعة لعد النبضات بدقة
void IRAM_ATTR isrMain() { pulsesMain = pulsesMain + 1; }
void IRAM_ATTR isrZA()   { pulsesZA = pulsesZA + 1; }
void IRAM_ATTR isrZB()   { pulsesZB = pulsesZB + 1; }
void IRAM_ATTR isrZC()   { pulsesZC = pulsesZC + 1; }

float readPressureKPa(uint8_t channel) {
#if SIMULATION_MODE
  const int simPins[4] = {36, 39, 34, 35}; // أطراف المقاومات المتغيرة في Wokwi
  int raw = analogRead(simPins[channel]);
  // تحويل قراءة المنزلق (0 - 4095) إلى ضغط (0 - 20 kPa)
  return (raw / 4095.0) * 20.0;
#else
  int16_t adc = ads.readADC_SingleEnded(channel);
  float voltage = ads.computeVolts(adc);
  // معادلة تحويل XGZP6847A (0.5V - 4.5V -> 0 - 40 kPa)
  float kpa = (voltage - 0.5) * (40.0 / 4.0);
  return (kpa < 0.0) ? 0.0 : kpa;
#endif
}

float getMovingAvg(float* buffer) {
  float sum = 0;
  for (int i = 0; i < WINDOW_SIZE; i++) sum += buffer[i];
  return sum / WINDOW_SIZE;
}

void setValve(int zoneIndex, bool open) {
  const int pins[3] = {VALVE_A_PIN, VALVE_B_PIN, VALVE_C_PIN};
  digitalWrite(pins[zoneIndex], open ? HIGH : LOW);
  valveState[zoneIndex] = open;
}

void selfCalibration() {
  Serial.println("[CALIBRATION] Closing all valves for self-calibration...");
  for (int i = 0; i < 3; i++) setValve(i, false);
  delay(2000); // انتظار استقرار موجة الضغط

  float sumP = 0;
  for (int i = 0; i < 20; i++) {
    sumP += readPressureKPa(0);
    delay(100);
  }
  pRefFull = sumP / 20.0;
  float waterHeight = (pRefFull * 1000.0) / (1000.0 * 9.81); // بالمتر

  Serial.printf("[CALIBRATION] Done! P_main_ref = %.2f kPa | Water Head = %.2f m\n", pRefFull, waterHeight);
  for (int i = 0; i < 3; i++) setValve(i, true); // إعادة فتح الصمامات
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);

  pinMode(VALVE_A_PIN, OUTPUT);
  pinMode(VALVE_B_PIN, OUTPUT);
  pinMode(VALVE_C_PIN, OUTPUT);

  pinMode(FLOW_MAIN_PIN, INPUT_PULLUP);
  pinMode(FLOW_ZA_PIN, INPUT_PULLUP);
  pinMode(FLOW_ZB_PIN, INPUT_PULLUP);
  pinMode(FLOW_ZC_PIN, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(FLOW_MAIN_PIN), isrMain, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_ZA_PIN), isrZA, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_ZB_PIN), isrZB, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_ZC_PIN), isrZC, FALLING);

#if !SIMULATION_MODE
  if (!ads.begin(0x48)) {
    Serial.println("[ERROR] Failed to initialize ADS1115!");
  }
  ads.setGain(GAIN_TWOTHIRDS); // نطاق ±6.144V
#else
  Serial.println("[INFO] Running in Wokwi SIMULATION_MODE (Potentiometers active).");
#endif

  selfCalibration();
}

void loop() {
  static unsigned long lastMillis = 0;
  if (millis() - lastMillis >= 1000) { // تحديث كل 1 ثانية
    noInterrupts();
    uint32_t cMain = pulsesMain; pulsesMain = 0;
    uint32_t cZA   = pulsesZA;   pulsesZA = 0;
    uint32_t cZB   = pulsesZB;   pulsesZB = 0;
    uint32_t cZC   = pulsesZC;   pulsesZC = 0;
    interrupts();

    // حساب التدفق بوحدة L/min
    float qMain = (cMain / CAL_YFS201) * 60.0;
    float qZones[3] = {
      (cZA / CAL_YFS401) * 60.0,
      (cZB / CAL_YFS401) * 60.0,
      (cZC / CAL_YFS401) * 60.0
    };

    // قراءة الضغوط وتحديث مرشح المتوسط المتحرك
    pMainBuf[bufIdx] = readPressureKPa(0);
    for (int i = 0; i < 3; i++) {
      pZoneBuf[i][bufIdx] = readPressureKPa(i + 1);
    }
    bufIdx = (bufIdx + 1) % WINDOW_SIZE;

    float pMain = getMovingAvg(pMainBuf);
    float pZones[3] = {
      getMovingAvg(pZoneBuf[0]),
      getMovingAvg(pZoneBuf[1]),
      getMovingAvg(pZoneBuf[2])
    };

    // --- تطبيق قواعد كشف التسرب الأربعة ---
    // 1. كشف تسرب المشعب الرئيسي (القيمة المطلقة + موازنة التدفق)
    float sumQ = qZones[0] + qZones[1] + qZones[2];
    if ((pMain < pRefFull * 0.80 && sumQ < 0.5) || abs(qMain - sumQ) > 0.5) {
      Serial.println("[ALERT] MAIN MANIFOLD LEAK DETECTED! Isolating all zones.");
      for (int i = 0; i < 3; i++) setValve(i, false);
    }

    // 2. كشف تسرب الفروع (نسبة الضغط + منطقة العمى Dead-band)
    for (int i = 0; i < 3; i++) {
      if (!valveState[i]) continue;
      float ratio = (pMain > 0.5) ? (pZones[i] / pMain) : 1.0;

      // الشرط 1: انخفاض نسبة الضغط P_zone / P_main
      if (ratio < baselineRatios[i] * 0.65) {
        Serial.printf("[ALERT] LEAK IN ZONE %c (Ratio Drop: %.2f)! Closing Valve.\n", 'A' + i, ratio);
        setValve(i, false);
      }
      // الشرط 4: منطقة العمى (التدفق صفر ولكن الضغط ينخفض تدريجياً)
      else if (qZones[i] == 0.0 && pZones[i] < pRefFull * 0.88) {
        Serial.printf("[ALERT] MICRO-LEAK IN ZONE %c (Dead-band Pressure Drop)! Closing Valve.\n", 'A' + i);
        setValve(i, false);
      }
    }

    // طباعة حزمة البيانات بصيغة JSON (جاهزة للإرسال عبر MQTT)
    Serial.printf("{\"P_main\":%.2f,\"Q_main\":%.2f,\"ZA\":{\"P\":%.2f,\"Q\":%.2f,\"V\":%d},\"ZB\":{\"P\":%.2f,\"Q\":%.2f,\"V\":%d},\"ZC\":{\"P\":%.2f,\"Q\":%.2f,\"V\":%d}}\n",
                  pMain, qMain, pZones[0], qZones[0], valveState[0], pZones[1], qZones[1], valveState[1], pZones[2], qZones[2], valveState[2]);

    lastMillis = millis();
  }
}

