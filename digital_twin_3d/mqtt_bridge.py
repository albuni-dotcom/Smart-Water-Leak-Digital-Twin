#!/usr/bin/env python3
"""
mqtt_bridge.py — bidirectional MQTT <-> UDP bridge for the
Smart Water Leak Detection Digital Twin (V2).

Architecture
------------
    ESP32 firmware  ──MQTT──▶  this bridge  ──UDP──▶  Godot 4 (MainTwin.gd)
                                ▲    │
                                │    ▼
                             Godot 4  (commands)

MQTT side
~~~~~~~~~
* Subscribes to ``smartwater/shopak2026/device/telemetry`` and
  ``smartwater/shopak2026/device/status``.
* Publishes to ``smartwater/shopak2026/device/cmd`` when Godot asks.
* Uses the paho-mqtt 1.x API (VERSION1). The project pins
  ``paho-mqtt<2.0.0`` in ``requirements.txt``.

UDP side
~~~~~~~~
* The bridge binds ``127.0.0.1:4242`` and receives JSON command
  datagrams from Godot. Each incoming datagram updates
  ``last_peer_addr`` (peer learning) so the bridge knows where to
  send telemetry back. This makes a single fixed port (4242) enough
  for the Godot side: Godot opens one UDP socket, sends from it,
  and receives telemetry on the same socket.
* Telemetry forwarded to Godot is wrapped as::

      {"topic": "telemetry" | "status", "ts": <unix_float>, "data": {...}}

  so the Godot client can route it without re-parsing MQTT headers.
* If no peer has ever spoken, telemetry is sent to the optional
  ``--forward-host / --forward-port`` fallback (default
  ``127.0.0.1:4243``). Set both to disable the fallback.
* The special packet ``{"__hello": true}`` from Godot is consumed
  silently to trigger peer learning without spamming the ESP32.

Safety
------
* Commands from Godot are JSON-validated before being published.
* Non-JSON datagrams are logged and dropped; they never reach MQTT.
* The bridge never opens, closes, or actuates anything. It is pure
  data plumbing.

Usage
-----
    python mqtt_bridge.py
    python mqtt_bridge.py --mqtt-host broker.hivemq.com --listen-port 4242
    python mqtt_bridge.py --verbose
    python mqtt_bridge.py --no-peer-learning --forward-port 4243
"""

import argparse
import json
import logging
import queue
import signal
import socket
import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print(
        "ERROR: paho-mqtt is required.\n"
        "       pip install 'paho-mqtt<2.0.0'",
        file=sys.stderr,
    )
    sys.exit(1)


# ============================================================
# Topics — must match firmware/src/main.cpp and dashboard_ai/app.py
# ============================================================
TOPIC_PREFIX = "smartwater/shopak2026"
TELEMETRY_TOPIC = f"{TOPIC_PREFIX}/device/telemetry"
STATUS_TOPIC = f"{TOPIC_PREFIX}/device/status"
COMMAND_TOPIC = f"{TOPIC_PREFIX}/device/cmd"


log = logging.getLogger("mqtt_bridge")


# ============================================================
# Helpers
# ============================================================
def _safe_json_loads(data_bytes, description):
    """Decode UTF-8 and parse JSON. Return None on any failure.

    The caller is expected to log and drop the payload when None is
    returned. This avoids leaking malformed packets into MQTT.
    """
    try:
        text = data_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        log.warning("Dropping %s — UTF-8 decode error: %s", description, exc)
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Dropping %s — invalid JSON: %s", description, exc)
        return None

    if not isinstance(parsed, dict):
        log.warning("Dropping %s — payload is not a JSON object", description)
        return None

    return parsed


# ============================================================
# Bridge
# ============================================================
class MqttToUdpBridge:
    def __init__(self, args):
        self.args = args
        self._stop = threading.Event()

        self._listen_addr = (args.listen_host, args.listen_port)
        self._fallback_addr = None
        if args.forward_host and args.forward_port:
            self._fallback_addr = (args.forward_host, args.forward_port)

        # Peer address learned from the most recent UDP command.
        self._peer_lock = threading.Lock()
        self._peer_addr = None

        # Bounded command queue: drop oldest on overflow instead of
        # blocking the UDP receiver thread.
        self._cmd_queue = queue.Queue(maxsize=500)

        # --- UDP sockets ---
        self._udp_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._udp_rx.bind(self._listen_addr)
        except OSError as exc:
            log.error(
                "Cannot bind UDP %s:%d — %s",
                args.listen_host, args.listen_port, exc,
            )
            raise SystemExit(2)
        self._udp_rx.settimeout(0.5)

        self._udp_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # --- MQTT client (V1 API) ---
        self._mqtt = mqtt.Client(
            client_id=args.client_id,
            clean_session=True,
        )
        if args.mqtt_user:
            self._mqtt.username_pw_set(args.mqtt_user, args.mqtt_pass)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_disconnect = self._on_mqtt_disconnect
        self._mqtt.on_message = self._on_mqtt_message
        self._mqtt.reconnect_delay_set(min_delay=1, max_delay=30)

        # --- Counters ---
        self._rx_count = 0
        self._tx_count = 0
        self._cmd_count = 0
        self._err_count = 0
        self._start_ts = time.time()

    # --------------------------------------------------------
    # MQTT callbacks
    # --------------------------------------------------------
    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info(
                "MQTT connected to %s:%d",
                self.args.mqtt_host, self.args.mqtt_port,
            )
            client.subscribe(TELEMETRY_TOPIC, qos=0)
            client.subscribe(STATUS_TOPIC, qos=0)
        else:
            log.error("MQTT connection refused, rc=%d", rc)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        if rc != 0:
            log.warning("MQTT unexpected disconnect, rc=%d", rc)
        else:
            log.info("MQTT disconnected cleanly")

    def _on_mqtt_message(self, client, userdata, message):
        # STATUS_TOPIC carries a plain UTF-8 string ("READY", "ALARM",
        # "DISCONNECTED"), not JSON. Handle it explicitly before any
        # JSON parsing.
        if message.topic == STATUS_TOPIC:
            try:
                status_text = message.payload.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                self._err_count += 1
                log.warning("Dropping status — UTF-8 decode error: %s", exc)
                return
            self._forward_to_godot({
                "topic": "status",
                "ts": time.time(),
                "data": status_text,
            })
            return

        # TELEMETRY_TOPIC must be a JSON object.
        payload = _safe_json_loads(message.payload, f"MQTT {message.topic}")
        if payload is None:
            self._err_count += 1
            return

        self._forward_to_godot({
            "topic": "telemetry",
            "ts": time.time(),
            "data": payload,
        })

    # --------------------------------------------------------
    # UDP receiver (Godot -> bridge)
    # --------------------------------------------------------
    def _udp_receiver_loop(self):
        log.info(
            "UDP receiver listening on %s:%d",
            self._listen_addr[0], self._listen_addr[1],
        )
        while not self._stop.is_set():
            try:
                data, addr = self._udp_rx.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    break
                self._err_count += 1
                log.warning("UDP recv error: %s", exc)
                continue

            self._rx_count += 1

            if not self.args.no_peer_learning:
                with self._peer_lock:
                    if self._peer_addr != addr:
                        log.info(
                            "Learned Godot peer at %s:%d", addr[0], addr[1]
                        )
                    self._peer_addr = addr

            command = _safe_json_loads(
                data, f"UDP command from {addr[0]}:{addr[1]}"
            )
            if command is None:
                self._err_count += 1
                continue

            # Hello handshake: trigger peer learning without
            # publishing anything to the ESP32.
            if command.get("__hello") is True:
                log.debug("Godot hello from %s:%d", addr[0], addr[1])
                continue

            try:
                self._cmd_queue.put_nowait(command)
            except queue.Full:
                # Drop the oldest queued command and retry once.
                try:
                    self._cmd_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._cmd_queue.put_nowait(command)
                except queue.Full:
                    self._err_count += 1
                    log.warning("Command queue is full; dropping packet")

    # --------------------------------------------------------
    # MQTT command publisher (bridge -> MQTT)
    # --------------------------------------------------------
    def _mqtt_command_worker(self):
        while not self._stop.is_set():
            try:
                command = self._cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not self._mqtt.is_connected():
                self._err_count += 1
                log.warning("Dropping command — MQTT not connected")
                continue

            try:
                payload = json.dumps(command, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                self._err_count += 1
                log.warning("Cannot serialize command: %s", exc)
                continue

            result = self._mqtt.publish(COMMAND_TOPIC, payload, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self._cmd_count += 1
                log.info("CMD -> MQTT %s: %s", COMMAND_TOPIC, payload)
            else:
                self._err_count += 1
                log.warning("MQTT publish failed, rc=%d", result.rc)

    # --------------------------------------------------------
    # UDP sender (bridge -> Godot)
    # --------------------------------------------------------
    def _forward_to_godot(self, envelope):
        try:
            data = json.dumps(
                envelope, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._err_count += 1
            log.warning("Cannot serialize telemetry: %s", exc)
            return

        dest = None
        if not self.args.no_peer_learning:
            with self._peer_lock:
                dest = self._peer_addr
        if dest is None:
            dest = self._fallback_addr
        if dest is None:
            # No peer yet and no fallback configured. Drop silently.
            return

        try:
            self._udp_tx.sendto(data, dest)
            self._tx_count += 1
        except OSError as exc:
            self._err_count += 1
            log.warning(
                "UDP send to %s:%d failed: %s", dest[0], dest[1], exc
            )

    # --------------------------------------------------------
    # Status ticker
    # --------------------------------------------------------
    def _status_loop(self):
        while not self._stop.wait(30.0):
            uptime = time.time() - self._start_ts
            with self._peer_lock:
                peer = self._peer_addr
            log.info(
                "Status: uptime=%.0fs peer=%s rx=%d tx=%d cmd=%d err=%d mqtt=%s",
                uptime,
                f"{peer[0]}:{peer[1]}" if peer else "none",
                self._rx_count,
                self._tx_count,
                self._cmd_count,
                self._err_count,
                "up" if self._mqtt.is_connected() else "down",
            )

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------
    def run(self):
        log.info("MQTT bridge starting")
        log.info("  MQTT broker:     %s:%d",
                 self.args.mqtt_host, self.args.mqtt_port)
        log.info("  Command topic:   %s", COMMAND_TOPIC)
        log.info("  Telemetry topic: %s", TELEMETRY_TOPIC)
        log.info("  Status topic:    %s", STATUS_TOPIC)
        log.info("  UDP listen:      %s:%d",
                 self._listen_addr[0], self._listen_addr[1])

        if self._fallback_addr is not None:
            log.info("  UDP fallback:    %s:%d",
                     self._fallback_addr[0], self._fallback_addr[1])
        else:
            log.info("  UDP fallback:    disabled")

        if not self.args.no_peer_learning:
            log.info("  Peer learning:   enabled")
        else:
            log.info("  Peer learning:   disabled")

        if self._listen_addr == self._fallback_addr:
            log.warning(
                "listen == fallback. Telemetry will only be delivered "
                "after Godot sends its first command (or its hello packet)."
            )

        self._mqtt.connect_async(
            self.args.mqtt_host, self.args.mqtt_port, keepalive=30
        )
        self._mqtt.loop_start()

        threads = [
            threading.Thread(
                target=self._udp_receiver_loop,
                name="udp_rx",
                daemon=True,
            ),
            threading.Thread(
                target=self._mqtt_command_worker,
                name="mqtt_cmd",
                daemon=True,
            ),
            threading.Thread(
                target=self._status_loop,
                name="status",
                daemon=True,
            ),
        ]
        for t in threads:
            t.start()

        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("SIGINT received, shutting down")
        finally:
            self.stop()

    def stop(self):
        if self._stop.is_set():
            return
        self._stop.set()

        try:
            self._mqtt.loop_stop()
        except Exception:
            pass
        try:
            self._mqtt.disconnect()
        except Exception:
            pass
        try:
            self._udp_rx.close()
        except Exception:
            pass
        try:
            self._udp_tx.close()
        except Exception:
            pass

        log.info(
            "Bridge stopped. rx=%d tx=%d cmd=%d err=%d",
            self._rx_count, self._tx_count,
            self._cmd_count, self._err_count,
        )


# ============================================================
# CLI
# ============================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="MQTT <-> UDP bridge for the Smart Water Digital Twin.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument(
        "--mqtt-host", default="broker.hivemq.com",
        help="MQTT broker hostname or IP.",
    )
    p.add_argument(
        "--mqtt-port", type=int, default=1883,
        help="MQTT broker port.",
    )
    p.add_argument(
        "--mqtt-user", default="",
        help="MQTT username (empty = anonymous).",
    )
    p.add_argument(
        "--mqtt-pass", default="",
        help="MQTT password.",
    )
    p.add_argument(
        "--client-id", default=None,
        help="MQTT client ID. Defaults to a unique timestamp-based ID.",
    )

    p.add_argument(
        "--listen-host", default="127.0.0.1",
        help="UDP bind address for commands coming from Godot.",
    )
    p.add_argument(
        "--listen-port", type=int, default=4242,
        help="UDP bind port for commands coming from Godot.",
    )

    p.add_argument(
        "--forward-host", default="127.0.0.1",
        help=(
            "Fallback destination for telemetry until a Godot peer is "
            "learned. Set to '' together with --forward-port 0 to "
            "disable the fallback."
        ),
    )
    p.add_argument(
        "--forward-port", type=int, default=4243,
        help="Fallback destination port.",
    )

    p.add_argument(
        "--no-peer-learning", action="store_true",
        help=(
            "Always send telemetry to the fallback address and ignore "
            "the source address of incoming UDP commands."
        ),
    )

    p.add_argument(
        "--verbose", action="store_true",
        help="Enable debug-level logging.",
    )

    args = p.parse_args(argv)

    if args.client_id is None:
        args.client_id = f"smartwater_godot_bridge_{int(time.time())}"

    # Normalise "disable fallback" requests.
    if args.forward_port == 0 or args.forward_host == "":
        args.forward_host = ""
        args.forward_port = 0

    return args


def main(argv=None):
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    bridge = MqttToUdpBridge(args)

    def _sigint_handler(signum, frame):
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    bridge.run()


if __name__ == "__main__":
    main()
