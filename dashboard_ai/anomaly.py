from collections import deque
from datetime import datetime
import numpy as np
MAIN_FLOW_ERROR = 0.45
MAIN_FLOW_MIN_Q = 0.60
MAIN_PRESSURE_LIMIT = 0.80
MAIN_PRESSURE_SUMQ_MAX = 0.15
ZONE_PRESSURE_LIMIT = 0.65
MICRO_LEAK_FLOW_MAX = 0.05
MICRO_LEAK_PRESSURE = 0.88
ELEVATION_COMPENSATION_KPA = 4.316
SIGNALS = (
    "P_main", "Q_main",
    "P_A", "Q_A",
    "P_B", "Q_B",
    "P_C", "Q_C",
)
ZONES = ("A", "B", "C")
class AnomalyDetector:
    def __init__(
        self,
        window_size=60,
        minimum_samples=15,
        baseline_samples=30,
        z_threshold=3.5,
        fixed_z_threshold=4.0,
        pressure_drop_limit=0.25,
    ):
        self.window_size         = window_size
        self.minimum_samples     = minimum_samples
        self.baseline_samples    = baseline_samples
        self.z_threshold         = z_threshold
        self.fixed_z_threshold   = fixed_z_threshold
        self.pressure_drop_limit = pressure_drop_limit
        self.history = {key: deque(maxlen=window_size) for key in SIGNALS}
        self._baseline_collect = {key: [] for key in SIGNALS}
        self._fixed_baseline = {key: None for key in SIGNALS}
        self.last_result = {
            "anomaly": False,
            "severity": "NORMAL",
            "detections": [],
            "timestamp": None,
        }
    def extract_features(self, telemetry):
        if not isinstance(telemetry, dict):
            raise ValueError("Telemetry must be a dictionary")
        za = telemetry.get("A") or {}
        zb = telemetry.get("B") or {}
        zc = telemetry.get("C") or {}
        if not (isinstance(za, dict) and isinstance(zb, dict) and isinstance(zc, dict)):
            raise ValueError("Zone payloads must be dictionaries")
        def _f(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        p_a_raw = _f(za.get("P", 0.0))
        p_b_raw = _f(zb.get("P", 0.0))
        p_c_raw = _f(zc.get("P", 0.0))
        p_a = max(0.0, p_a_raw - ELEVATION_COMPENSATION_KPA)
        p_b = max(0.0, p_b_raw - ELEVATION_COMPENSATION_KPA)
        p_c = max(0.0, p_c_raw - ELEVATION_COMPENSATION_KPA)
        return {
            "P_main": _f(telemetry.get("P_main", 0.0)),
            "Q_main": _f(telemetry.get("Q_main", 0.0)),
            "P_A":    p_a,
            "Q_A":    _f(za.get("Q", 0.0)),
            "P_B":    p_b,
            "Q_B":    _f(zb.get("Q", 0.0)),
            "P_C":    p_c,
            "Q_C":    _f(zc.get("Q", 0.0)),
        }
    @staticmethod
    def _zone_valve_open(telemetry, zone):
        zone_data = telemetry.get(zone) or {}
        if not isinstance(zone_data, dict):
            return False
        try:
            return bool(int(zone_data.get("V", 0)))
        except (TypeError, ValueError):
            return False
    def _signal_requires_open_valve(self, key):
        return (
            key.startswith(("P_", "Q_"))
            and key not in ("P_main", "Q_main")
        )
    def _zone_of_signal(self, key):
        return key[-1]
    def calculate_robust_score(self, values, current):
        if len(values) < self.minimum_samples:
            return 0.0
        data = np.asarray(values, dtype=float)
        center = float(np.median(data))
        mad = float(np.median(np.abs(data - center)))
        if mad < 1e-8:
            std = float(np.std(data))
            if std < 1e-8:
                if abs(current - center) < 1e-6:
                    return 0.0
                return float("inf")
            return abs(current - center) / std
        robust_sigma = 1.4826 * mad
        return abs(current - center) / robust_sigma
    def _collect_baseline_sample(self, features, telemetry):
        for key in SIGNALS:
            if self._fixed_baseline[key] is not None:
                continue
            if self._signal_requires_open_valve(key):
                if not self._zone_valve_open(telemetry, self._zone_of_signal(key)):
                    continue
            self._baseline_collect[key].append(features[key])
            if len(self._baseline_collect[key]) < self.baseline_samples:
                continue
            tail = self._baseline_collect[key][-self.baseline_samples:]
            data = np.asarray(tail, dtype=float)
            center = float(np.median(data))
            mad    = float(np.median(np.abs(data - center)))
            if center > 0.5 and mad / max(center, 1e-6) > 0.20:
                keep = max(self.baseline_samples // 2, 5)
                self._baseline_collect[key] = self._baseline_collect[key][-keep:]
                continue
            sigma = 1.4826 * mad if mad > 1e-8 else max(float(np.std(data)), 1e-6)       
            if key.startswith("P_"):
                sigma = max(sigma, 0.35)
            elif key.startswith("Q_"):
                sigma = max(sigma, 0.15)
            self._fixed_baseline[key] = {"median": center, "sigma": sigma}
    def _has_baseline(self, key):
        return self._fixed_baseline[key] is not None
    def _is_baseline_ready(self):
        return all(v is not None for v in self._fixed_baseline.values())
    def detect_flow_imbalance(self, features, telemetry):
        detections = []
        main_flow = features["Q_main"]
        zone_flow = sum(
            features[f"Q_{zone}"]
            for zone in ZONES
            if self._zone_valve_open(telemetry, zone)
        )
        difference = abs(main_flow - zone_flow)
        if main_flow > MAIN_FLOW_MIN_Q and difference > MAIN_FLOW_ERROR:
            detections.append({
                "type": "FLOW_IMBALANCE",
                "main_flow": round(main_flow, 3),
                "zone_flow": round(zone_flow, 3),
                "difference": round(difference, 3),
                "message": (
                    f"Main flow {main_flow:.2f} L/min does not match "
                    f"zone sum {zone_flow:.2f} L/min (diff {difference:.2f})"
                ),
            })
        return detections
    def detect_main_pressure_drop(self, features, telemetry):
        if not self._has_baseline("P_main"):
            return []
        baseline = self._fixed_baseline["P_main"]
        if baseline is None or baseline["median"] <= 0:
            return []
        if not all(not self._zone_valve_open(telemetry, z) for z in ZONES):
            return []
        sum_q = sum(features[f"Q_{z}"] for z in ZONES)
        if sum_q >= MAIN_PRESSURE_SUMQ_MAX:
            return []
        current = features["P_main"]
        if current < baseline["median"] * MAIN_PRESSURE_LIMIT:
            return [{
                "type": "MAIN_PRESSURE_DROP",
                "baseline": round(baseline["median"], 3),
                "current":  round(current, 3),
                "message": (
                    "Main pressure below 80% of baseline while all "
                    "branch valves are closed"
                ),
            }]
        return []
    def detect_zone_ratio(self, features, telemetry):
        detections = []
        p_main = features["P_main"]
        if p_main <= 0.5:
            return detections
        for zone in ZONES:
            if not self._zone_valve_open(telemetry, zone):
                continue
            p_zone = features[f"P_{zone}"]
            ratio  = p_zone / p_main
            if ratio < ZONE_PRESSURE_LIMIT:
                detections.append({
                    "type": "ZONE_RATIO_DROP",
                    "zone": zone,
                    "ratio": round(ratio, 3),
                    "message": (
                        f"Zone {zone} pressure ratio {ratio:.2f} "
                        f"below {ZONE_PRESSURE_LIMIT}"
                    ),
                })
        return detections
    def detect_micro_leaks(self, features, telemetry):
        detections = []
        p_main = features["P_main"]
        if p_main <= 0.5:
            return detections
        for zone in ZONES:
            if not self._zone_valve_open(telemetry, zone):
                continue
            q_zone = features[f"Q_{zone}"]
            p_zone = features[f"P_{zone}"]
            if q_zone < MICRO_LEAK_FLOW_MAX and p_zone < MICRO_LEAK_PRESSURE * p_main:
                detections.append({
                    "type": "MICRO_LEAK",
                    "zone": zone,
                    "flow":     round(q_zone, 3),
                    "pressure": round(p_zone, 3),
                    "message": (
                        f"Micro-leak in Zone {zone}: flow {q_zone:.2f} "
                        f"L/min with pressure {p_zone:.2f} kPa"
                    ),
                })
        return detections
    def detect_statistical_anomalies(self, features, telemetry):
        detections = []
        for key, current in features.items():
            if self._signal_requires_open_valve(key):
                if not self._zone_valve_open(telemetry, self._zone_of_signal(key)):
                    continue
            score = self.calculate_robust_score(self.history[key], current)
            if score >= self.z_threshold:
                display_score = 999.0 if score == float("inf") else score
                detections.append({
                    "type": "STATISTICAL_ANOMALY",
                    "sensor": key,
                    "value": round(current, 3),
                    "score": round(display_score, 3),
                    "message": f"Sudden change in {key}",
                })
        return detections
    def detect_fixed_baseline_drift(self, features, telemetry):
        detections = []
        for key, current in features.items():
            baseline = self._fixed_baseline[key]
            if baseline is None:
                continue
            if self._signal_requires_open_valve(key):
                if not self._zone_valve_open(telemetry, self._zone_of_signal(key)):
                    continue
            center = baseline["median"]
            sigma  = baseline["sigma"]
            if sigma < 1e-6:
                if abs(current - center) < 1e-6:
                    continue
                score = float("inf")
            else:
                score = abs(current - center) / sigma
            if score >= self.fixed_z_threshold:
                display_score = 999.0 if score == float("inf") else score
                detections.append({
                    "type": "BASELINE_DRIFT",
                    "sensor": key,
                    "value": round(current, 3),
                    "baseline": round(center, 3),
                    "score": round(display_score, 3),
                    "message": (
                        f"Slow drift on {key}: current {current:.2f} "
                        f"vs frozen baseline {center:.2f}"
                    ),
                })
        return detections
    def detect_pressure_drop(self, features, telemetry):
        detections = []
        for zone in ZONES:
            if not self._zone_valve_open(telemetry, zone):
                continue
            key = f"P_{zone}"
            baseline = self._fixed_baseline[key]
            if baseline is None:
                continue
            center  = baseline["median"]
            current = features[key]
            if center <= 0:
                continue
            drop_ratio = (center - current) / center
            if drop_ratio >= self.pressure_drop_limit:
                detections.append({
                    "type": "PRESSURE_DROP",
                    "zone": zone,
                    "baseline": round(center, 3),
                    "current": round(current, 3),
                    "drop_ratio": round(drop_ratio, 3),
                    "message": (
                        f"Abnormal pressure drop in Zone {zone}: "
                        f"{drop_ratio * 100:.1f}% below baseline"
                    ),
                })
        return detections
    def determine_severity(self, detections):
        if not detections:
            return "NORMAL"
        critical = {
            "FLOW_IMBALANCE",
            "PRESSURE_DROP",
            "MAIN_PRESSURE_DROP",
            "ZONE_RATIO_DROP",
            "MICRO_LEAK",
        }
        if any(d["type"] in critical for d in detections):
            return "HIGH"
        return "MEDIUM"
    def update_history(self, features, telemetry):
        for key, value in features.items():
            if self._signal_requires_open_valve(key):
                if not self._zone_valve_open(telemetry, self._zone_of_signal(key)):
                    continue
            self.history[key].append(value)
    def analyze(self, telemetry):
        if not isinstance(telemetry, dict):
            raise ValueError("Telemetry must be a dictionary")
        features = self.extract_features(telemetry)
        for key, value in features.items():
            if not np.isfinite(value):
                raise ValueError(f"Invalid sensor value: {key}")
            if value < 0:
                raise ValueError(f"Negative sensor value: {key}")
        if telemetry.get("alarm_type") == "LOW_TANK_LEVEL":
            result = {
                "anomaly": True,
                "severity": "HIGH",
                "detections": [{
                    "type": "LOW_TANK_LEVEL",
                    "message": "Tank empty or main sensor disconnected during calibration",
                }],
                "timestamp": datetime.now().isoformat(),
                "baseline_ready": self._is_baseline_ready(),
                "baseline_status": {
                    key: (
                        "frozen" if self._fixed_baseline[key] is not None
                        else f"collecting {len(self._baseline_collect[key])}/{self.baseline_samples}"
                    )
                    for key in SIGNALS
                },
                "features": {k: round(v, 3) for k, v in features.items()},
            }
            self.last_result = result
            return result
        alarming = bool(telemetry.get("alarm", False))
        direct_detections = []
        direct_detections.extend(self.detect_flow_imbalance(features, telemetry))
        direct_detections.extend(self.detect_main_pressure_drop(features, telemetry))
        direct_detections.extend(self.detect_zone_ratio(features, telemetry))
        direct_detections.extend(self.detect_micro_leaks(features, telemetry))
        clean_sample = (not alarming) and (len(direct_detections) == 0)
        if clean_sample:
            self._collect_baseline_sample(features, telemetry)
        stat_detections = []
        stat_detections.extend(self.detect_statistical_anomalies(features, telemetry))
        stat_detections.extend(self.detect_fixed_baseline_drift(features, telemetry))
        stat_detections.extend(self.detect_pressure_drop(features, telemetry))
        detections = direct_detections + stat_detections
        severity   = self.determine_severity(detections)
        self.update_history(features, telemetry)
        result = {
            "anomaly": len(detections) > 0,
            "severity": severity,
            "detections": detections,
            "timestamp": datetime.now().isoformat(),
            "baseline_ready": self._is_baseline_ready(),
            "baseline_status": {
                key: (
                    "frozen" if self._fixed_baseline[key] is not None
                    else f"collecting {len(self._baseline_collect[key])}/{self.baseline_samples}"
                )
                for key in SIGNALS
            },
            "features": {k: round(v, 3) for k, v in features.items()},
        }
        self.last_result = result
        return result
    def reset_zone(self, zone):
        if zone not in ZONES:
            return
        for prefix in ("P_", "Q_"):
            key = f"{prefix}{zone}"
            self.history[key].clear()
            self._baseline_collect[key] = []
            self._fixed_baseline[key] = None
        self.last_result = {
            "anomaly": False,
            "severity": "NORMAL",
            "detections": [],
            "timestamp": datetime.now().isoformat(),
        }
    def reset(self):
        for values in self.history.values():
            values.clear()
        for key in SIGNALS:
            self._baseline_collect[key] = []
            self._fixed_baseline[key] = None
        self.last_result = {
            "anomaly": False,
            "severity": "NORMAL",
            "detections": [],
            "timestamp": None,
        }
    def get_status(self):
        return {
            "samples": {key: len(values) for key, values in self.history.items()},
            "baseline_samples": {key: len(values) for key, values in self._baseline_collect.items()},
            "baseline_ready": self._is_baseline_ready(),
            "baseline_frozen": {
                key: (self._fixed_baseline[key] is not None)
                for key in SIGNALS
            },
            "last_result": self.last_result,
        }
detector = AnomalyDetector()
def detect_anomaly(telemetry):
    return detector.analyze(telemetry)
def reset_zone_detector(zone):
    detector.reset_zone(zone)
def reset_detector():
    detector.reset()
