"""
Anomaly detection layer for the Smart Water Leak Detection & Isolation System.

Design notes
------------
1. Two detection families run side-by-side:

   - Direct rules (no history required)
       * Flow imbalance:        |Q_main - sum(Q_zones)| > 1.0  when Q_main > 1.2
       * Main pressure drop:    P_main < 0.80 * P_ref  when sum(Q_zones) < 0.5
       * Zone pressure ratio:   P_zone_comp / P_main < 0.65
       * Micro-leak:            Q_zone < 0.05  and  P_zone_comp < 0.88 * P_main

     These match firmware/src/main.cpp exactly. They work from the first
     sample and are also used to decide whether a sample is "clean enough"
     to feed the baseline collector.

   - Statistical rules (need history)
       * Moving-window z-score:  catches sudden jumps.
       * Fixed-baseline drift:   catches slow leaks that the moving
                                 window would otherwise adapt to.

2. The fixed baseline is *frozen* once each signal has collected
   `baseline_samples` clean samples. It is never updated afterwards,
   which is what makes slow-leak detection possible. Any drift in
   the current reading relative to that frozen median is flagged.

3. `std == 0` handling: if the historical window is perfectly constant,
   any difference in the current reading is a definite anomaly.
   The robust score returns `+inf` in that case instead of 0.0.

4. Elevation compensation: branch sensors sit 0.44 m below the main
   pressure sensor, adding rho*g*h = 4.316 kPa. Every zone pressure is
   compensated (subtract 4.316, clamp to 0) inside `extract_features`,
   so all thresholds work on comparable values. The published telemetry
   still carries the raw zone readings.

5. Baseline collection constraint (hardware mode only): a zone signal
   is only collected while its valve is open. On ESP32 boot all valves
   are closed for safety, so the operator must open A, B, C before the
   baseline can freeze. In simulation mode the valves start open and
   the baseline freezes automatically.
"""

from collections import deque
from datetime import datetime

import numpy as np


# ============================================================
# Unified thresholds
# ------------------------------------------------------------
# MUST match firmware/src/main.cpp exactly. If you change a value
# here, change it there too, otherwise the digital twin stops
# being a twin.
# ============================================================
MAIN_FLOW_ERROR_LIMIT   = 1.0     # L/min
MAIN_FLOW_MIN_Q         = 1.2     # L/min
MAIN_PRESSURE_LIMIT     = 0.80    # P_main < 0.80 * P_ref
MAIN_PRESSURE_SUMQ_MAX  = 0.5     # L/min
ZONE_PRESSURE_LIMIT     = 0.65    # P_zone_comp / P_main
MICRO_LEAK_FLOW_MAX     = 0.05    # L/min
MICRO_LEAK_PRESSURE     = 0.88    # P_zone_comp < 0.88 * P_main

# Elevation compensation: rho * g * h with h = 0.44 m, water at 20 C.
ELEVATION_COMPENSATION_KPA = 4.316

SIGNALS = (
    "P_main", "Q_main",
    "P_A", "Q_A",
    "P_B", "Q_B",
    "P_C", "Q_C",
)

ZONES = ("A", "B", "C")


class AnomalyDetector:
    """Statistical + rule-based advisory detector.

    The detector never actuates valves. It only produces a JSON-serialisable
    result that the dashboard renders as warnings.
    """

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

        # Moving window: always fed with fresh data.
        self.history = {key: deque(maxlen=window_size) for key in SIGNALS}

        # Fixed-baseline collection phase.
        self._baseline_collect = {key: [] for key in SIGNALS}

        # Frozen baselines: {key: {"median": float, "sigma": float} | None}
        self._fixed_baseline = {key: None for key in SIGNALS}

        self.last_result = {
            "anomaly": False,
            "severity": "NORMAL",
            "detections": [],
            "timestamp": None,
        }

    # --------------------------------------------------------
    # Feature extraction
    # --------------------------------------------------------
    def extract_features(self, telemetry):
        if not isinstance(telemetry, dict):
            raise ValueError("Telemetry must be a dictionary")

        za = telemetry.get("ZA") or {}
        zb = telemetry.get("ZB") or {}
        zc = telemetry.get("ZC") or {}

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

        # Elevation compensation applied once, here, so every threshold
        # below works on values comparable with P_main.
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

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------
    @staticmethod
    def _zone_valve_open(telemetry, zone):
        zone_data = telemetry.get(f"Z{zone}") or {}
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

    # --------------------------------------------------------
    # Robust score with std == 0 handling
    # --------------------------------------------------------
    def calculate_robust_score(self, values, current):
        if len(values) < self.minimum_samples:
            return 0.0

        data   = np.asarray(values, dtype=float)
        center = float(np.median(data))
        mad    = float(np.median(np.abs(data - center)))

        if mad < 1e-8:
            std = float(np.std(data))
            if std < 1e-8:
                # Perfectly constant history. Any difference is a
                # definite anomaly; identical value is not.
                if abs(current - center) < 1e-6:
                    return 0.0
                return float("inf")
            return abs(current - center) / std

        robust_sigma = 1.4826 * mad
        return abs(current - center) / robust_sigma

    # --------------------------------------------------------
    # Fixed-baseline collection and freeze
    # --------------------------------------------------------
    def _collect_baseline_sample(self, features, telemetry):
        """Append a sample to the collection buffer, and freeze each
        signal independently once it has enough clean samples.
        """
        for key in SIGNALS:
            if self._fixed_baseline[key] is not None:
                continue  # already frozen

            if self._signal_requires_open_valve(key):
                if not self._zone_valve_open(telemetry, self._zone_of_signal(key)):
                    continue

            self._baseline_collect[key].append(features[key])

            if len(self._baseline_collect[key]) < self.baseline_samples:
                continue

            # Stability check: if the collection window is internally
            # unstable (e.g. a slow leak contaminated the early samples),
            # drop the older half and keep collecting.
            tail = self._baseline_collect[key][-self.baseline_samples:]
            data = np.asarray(tail, dtype=float)
            center = float(np.median(data))
            mad    = float(np.median(np.abs(data - center)))

            if center > 0.5 and mad / max(center, 1e-6) > 0.20:
                keep = max(self.baseline_samples // 2, 5)
                self._baseline_collect[key] = self._baseline_collect[key][-keep:]
                continue

            sigma = 1.4826 * mad if mad > 1e-8 else max(float(np.std(data)), 1e-6)

            # Minimum physically meaningful sigma: prevents false
            # BASELINE_DRIFT warnings from real sensor noise (XGZP6847A
            # is +/-0.6 kPa) or natural tank-level drift (a few kPa).
            if key.startswith("P_"):
                sigma = max(sigma, 0.35)
            elif key.startswith("Q_"):
                sigma = max(sigma, 0.15)

            self._fixed_baseline[key] = {"median": center, "sigma": sigma}

    def _has_baseline(self, key):
        return self._fixed_baseline[key] is not None

    def _is_baseline_ready(self):
        return all(v is not None for v in self._fixed_baseline.values())

    # --------------------------------------------------------
    # Direct rules (no history required)
    # --------------------------------------------------------
    def detect_flow_imbalance(self, features, telemetry):
        detections = []

        main_flow = features["Q_main"]
        zone_flow = sum(
            features[f"Q_{zone}"]
            for zone in ZONES
            if self._zone_valve_open(telemetry, zone)
        )

        difference = abs(main_flow - zone_flow)

        if main_flow > MAIN_FLOW_MIN_Q and difference > MAIN_FLOW_ERROR_LIMIT:
            detections.append({
                "type": "FLOW_IMBALANCE",
                "main_flow":  round(main_flow, 3),
                "zone_flow":  round(zone_flow, 3),
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

        # This rule only makes sense when all branch valves are closed:
        # otherwise normal consumption could also lower P_main.
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

        # Guard: a near-zero P_main (empty tank or main line cut) would
        # make the comparison 0.0 < 0.0 ambiguous and produce false
        # micro-leak alarms across every open branch. Require a
        # physically meaningful main pressure before evaluating.
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
    # --------------------------------------------------------
    # Statistical rules (need history / baseline)
    # --------------------------------------------------------
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
                    "value":    round(current, 3),
                    "baseline": round(center, 3),
                    "score":    round(display_score, 3),
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
                    "current":  round(current, 3),
                    "drop_ratio": round(drop_ratio, 3),
                    "message": (
                        f"Abnormal pressure drop in Zone {zone}: "
                        f"{drop_ratio * 100:.1f}% below baseline"
                    ),
                })

        return detections

    # --------------------------------------------------------
    # Severity
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # Moving-window update
    # --------------------------------------------------------
    def update_history(self, features, telemetry):
        for key, value in features.items():
            if self._signal_requires_open_valve(key):
                if not self._zone_valve_open(telemetry, self._zone_of_signal(key)):
                    continue
            self.history[key].append(value)

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------
    def analyze(self, telemetry):
        if not isinstance(telemetry, dict):
            raise ValueError("Telemetry must be a dictionary")

        features = self.extract_features(telemetry)

        for key, value in features.items():
            if not np.isfinite(value):
                raise ValueError(f"Invalid sensor value: {key}")
            if value < 0:
                raise ValueError(f"Negative sensor value: {key}")

        alarming = bool(telemetry.get("alarm", False))

        # 1. Direct rules first: they work without any history and they
        #    decide whether the current sample is clean enough to feed
        #    the baseline collector.
        direct_detections = []
        direct_detections.extend(self.detect_flow_imbalance(features, telemetry))
        direct_detections.extend(self.detect_main_pressure_drop(features, telemetry))
        direct_detections.extend(self.detect_zone_ratio(features, telemetry))
        direct_detections.extend(self.detect_micro_leaks(features, telemetry))

        clean_sample = (not alarming) and (len(direct_detections) == 0)

        if clean_sample:
            self._collect_baseline_sample(features, telemetry)

        # 2. Statistical rules (need history / baseline).
        stat_detections = []
        stat_detections.extend(self.detect_statistical_anomalies(features, telemetry))
        stat_detections.extend(self.detect_fixed_baseline_drift(features, telemetry))
        stat_detections.extend(self.detect_pressure_drop(features, telemetry))

        detections = direct_detections + stat_detections
        severity   = self.determine_severity(detections)

        # 3. Always update the moving window so sudden-change detection
        #    keeps seeing fresh data. The frozen baseline is separate and
        #    never moves, which is what allows slow-leak detection.
        self.update_history(features, telemetry)

        result = {
            "anomaly":         len(detections) > 0,
            "severity":        severity,
            "detections":      detections,
            "timestamp":       datetime.now().isoformat(),
            "baseline_ready":  self._is_baseline_ready(),
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

    def reset(self):
        """Full reset: clears moving window, baseline collection and frozen
        baselines. Call this from the dashboard when the operator presses
        RESET and wants the detector to re-learn the system from scratch.
        """
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
            "samples":          {key: len(values) for key, values in self.history.items()},
            "baseline_samples": {key: len(values) for key, values in self._baseline_collect.items()},
            "baseline_ready":   self._is_baseline_ready(),
            "baseline_frozen":  {
                key: (self._fixed_baseline[key] is not None)
                for key in SIGNALS
            },
            "last_result": self.last_result,
        }


# Module-level singleton: the dashboard imports `detect_anomaly` and
# `reset_detector` directly.
detector = AnomalyDetector()


def detect_anomaly(telemetry):
    return detector.analyze(telemetry)


def reset_detector():
    detector.reset()
