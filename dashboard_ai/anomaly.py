
from collections import deque
from statistics import median
from datetime import datetime
import numpy as np


class AnomalyDetector:
    def __init__(
        self,
        window_size=60,
        minimum_samples=15,
        z_threshold=3.5,
        pressure_drop_limit=0.25,
        flow_change_limit=0.50
    ):
        self.window_size = window_size
        self.minimum_samples = minimum_samples
        self.z_threshold = z_threshold
        self.pressure_drop_limit = pressure_drop_limit
        self.flow_change_limit = flow_change_limit

        self.history = {
            "P_main": deque(maxlen=window_size),
            "Q_main": deque(maxlen=window_size),
            "P_A": deque(maxlen=window_size),
            "P_B": deque(maxlen=window_size),
            "P_C": deque(maxlen=window_size),
            "Q_A": deque(maxlen=window_size),
            "Q_B": deque(maxlen=window_size),
            "Q_C": deque(maxlen=window_size)
        }

        self.last_result = {
            "anomaly": False,
            "severity": "NORMAL",
            "detections": [],
            "timestamp": None
        }

    def extract_features(self, telemetry):
        za = telemetry.get("ZA", {})
        zb = telemetry.get("ZB", {})
        zc = telemetry.get("ZC", {})

        return {
            "P_main": float(telemetry.get("P_main", 0.0)),
            "Q_main": float(telemetry.get("Q_main", 0.0)),
            "P_A": float(za.get("P", 0.0)),
            "P_B": float(zb.get("P", 0.0)),
            "P_C": float(zc.get("P", 0.0)),
            "Q_A": float(za.get("Q", 0.0)),
            "Q_B": float(zb.get("Q", 0.0)),
            "Q_C": float(zc.get("Q", 0.0))
        }

    def calculate_robust_score(self, values, current):
        if len(values) < self.minimum_samples:
            return 0.0

        data = np.asarray(values, dtype=float)

        center = float(np.median(data))
        mad = float(np.median(np.abs(data - center)))

        if mad < 1e-8:
            standard_deviation = float(np.std(data))

            if standard_deviation < 1e-8:
                return 0.0

            return abs(current - center) / standard_deviation

        robust_sigma = 1.4826 * mad

        return abs(current - center) / robust_sigma

    def detect_statistical_anomalies(self, features):
        detections = []

        for key, current in features.items():
            values = self.history[key]

            if len(values) < self.minimum_samples:
                continue

            score = self.calculate_robust_score(
                values,
                current
            )

            if score >= self.z_threshold:
                detections.append({
                    "type": "STATISTICAL_ANOMALY",
                    "sensor": key,
                    "value": round(current, 3),
                    "score": round(score, 3),
                    "message": f"Unusual change detected in {key}"
                })

        return detections

    def detect_pressure_drop(self, features):
        detections = []

        for zone in ["A", "B", "C"]:
            key = f"P_{zone}"
            values = self.history[key]

            if len(values) < self.minimum_samples:
                continue

            baseline = float(np.median(values))
            current = features[key]

            if baseline <= 0:
                continue

            drop_ratio = (baseline - current) / baseline

            if drop_ratio >= self.pressure_drop_limit:
                detections.append({
                    "type": "PRESSURE_DROP",
                    "zone": zone,
                    "baseline": round(baseline, 3),
                    "current": round(current, 3),
                    "drop_ratio": round(drop_ratio, 3),
                    "message": f"Abnormal pressure drop in Zone {zone}"
                })

        return detections

    def detect_flow_imbalance(self, features):
        detections = []

        main_flow = features["Q_main"]

        zone_flow = (
            features["Q_A"]
            + features["Q_B"]
            + features["Q_C"]
        )

        difference = abs(main_flow - zone_flow)

        if (
            main_flow > 1.0
            and difference > self.flow_change_limit
        ):
            detections.append({
                "type": "FLOW_IMBALANCE",
                "main_flow": round(main_flow, 3),
                "zone_flow": round(zone_flow, 3),
                "difference": round(difference, 3),
                "message": "Main flow does not match total zone flow"
            })

        return detections

    def detect_zone_anomalies(self, features, telemetry):
        detections = []

        zones = {
            "A": telemetry.get("ZA", {}),
            "B": telemetry.get("ZB", {}),
            "C": telemetry.get("ZC", {})
        }

        for zone, data in zones.items():
            valve_open = bool(data.get("V", 0))
            pressure = features[f"P_{zone}"]
            flow = features[f"Q_{zone}"]

            if not valve_open:
                continue

            if pressure <= 0.1 and flow > 0.1:
                detections.append({
                    "type": "PRESSURE_FLOW_MISMATCH",
                    "zone": zone,
                    "pressure": round(pressure, 3),
                    "flow": round(flow, 3),
                    "message": f"Pressure and flow mismatch in Zone {zone}"
                })

        return detections

    def determine_severity(self, detections):
        if not detections:
            return "NORMAL"

        critical_types = {
            "FLOW_IMBALANCE",
            "PRESSURE_DROP"
        }

        if any(
            item["type"] in critical_types
            for item in detections
        ):
            return "HIGH"

        return "MEDIUM"

    def update_history(self, features):
        for key, value in features.items():
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

        detections = []

        detections.extend(
            self.detect_statistical_anomalies(features)
        )

        detections.extend(
            self.detect_pressure_drop(features)
        )

        detections.extend(
            self.detect_flow_imbalance(features)
        )

        detections.extend(
            self.detect_zone_anomalies(features, telemetry)
        )

        severity = self.determine_severity(detections)

        result = {
            "anomaly": len(detections) > 0,
            "severity": severity,
            "detections": detections,
            "timestamp": datetime.now().isoformat(),
            "features": {
                key: round(value, 3)
                for key, value in features.items()
            }
        }

        self.update_history(features)

        self.last_result = result

        return result

    def reset(self):
        for values in self.history.values():
            values.clear()

        self.last_result = {
            "anomaly": False,
            "severity": "NORMAL",
            "detections": [],
            "timestamp": None
        }

    def get_status(self):
        return {
            "samples": {
                key: len(values)
                for key, values in self.history.items()
            },
            "last_result": self.last_result
        }


detector = AnomalyDetector()


def detect_anomaly(telemetry):
    return detector.analyze(telemetry)


def reset_detector():
    detector.reset()

