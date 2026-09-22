"""
3e Green Gateway → RabbitMQ (MQTT) Publisher  (SQLite)
----------------------------------------------------------
Steps:
  1. Read unpublished sensor data (id > watermark) within WINDOW_DAYS days
  2. Using the MQTT QoS 1 to publish the RabbitMQ (it should enable the rabbitmq_mqtt plugin)
  3. After broker PUBACK is confirmed, advance the watermark (max published id)
     in a small state file. Records are NOT deleted here, because the edge AI
     needs recent raw data for training and inference.
  4. Failed publishing records stay unpublished, and it will retry at next time.
  5. Deletion is handled by 3e_green_cleanup.py, which only removes records
     that are both older than RAW_KEEP_DAYS and already published.

State file:
  GW_PUB_STATE (default: publisher_state.json next to the database)
  {"last_published_id": 12345, "updated_at": "..."}

Install required dependencies:
    pip install paho-mqtt
"""

import os
import sys
import ssl
import json
import time
import sqlite3
import logging
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta


# ==================== Configuration ====================
DB_PATH = os.getenv('GW_DB_PATH', '/home/pi/green-gateway/sensor_data.db')
STATE_PATH = os.getenv('GW_PUB_STATE',
                       os.path.join(os.path.dirname(os.path.abspath(DB_PATH)),
                                    'publisher_state.json'))

# RabbitMQ over MQTT plugin
MQTT_HOST = os.getenv('MQTT_HOST', '127.0.0.1')
MQTT_USER = os.getenv('MQTT_USER', 'guest')
MQTT_PASS = os.getenv('MQTT_PASS', 'guest')
MQTT_CLIENT_ID = os.getenv('MQTT_CLIENT_ID', 'green-gw-publisher')

# ── TLS Setting──────────────────────────────
# Enable TLS and using secure connection (RabbitMQ MQTT TLS listener default port is 8883)
MQTT_TLS_ENABLED = os.getenv('MQTT_TLS_ENABLED', 'true').lower() == 'true'

# If the MQTT_PORT is not specified, using the TLS port number is 8883 / 1883 by default
MQTT_PORT = int(os.getenv('MQTT_PORT', '8883' if MQTT_TLS_ENABLED else '1883'))

# CA is used to verify broker certificate (TLS will be required when enabling this setting)
MQTT_CA_CERT = os.getenv('MQTT_CA_CERT', '')

# Client certificate and key pass: Using two-way verification only
MQTT_CLIENT_CERT = os.getenv('MQTT_CLIENT_CERT', '')
MQTT_CLIENT_KEY = os.getenv('MQTT_CLIENT_KEY', '')
MQTT_CLIENT_KEY_PASS = os.getenv('MQTT_CLIENT_KEY_PASS', '') or None

# Ignore certificate and hostname verification (Test only and do not use on the production)
MQTT_TLS_INSECURE = os.getenv('MQTT_TLS_INSECURE', 'false').lower() == 'true'

TOPIC_PREFIX = os.getenv('MQTT_TOPIC_PREFIX', '/3e_green_sensor')

WINDOW_DAYS = 14           # Only publish records within N days; older unpublished ones are skipped
MAX_BATCH = 5000           # Batch max value
PUBLISH_TIMEOUT = 10       # Wait publishing timeout

LOG_PATH = os.getenv('GW_LOG_PATH', '/home/pi/green-gateway/mqtt_publisher.log')
# ==============================================


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('mqtt-publisher')


def open_db(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


# ---------------- Watermark (publish progress) ----------------

def load_watermark():
    """Return the max published id; 0 if the state file does not exist."""
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return int(json.load(f).get('last_published_id', 0))
    except FileNotFoundError:
        return 0
    except (ValueError, OSError) as e:
        log.error(f'Cannot read state file {STATE_PATH}: {e}. Stopped to avoid re-publishing.')
        sys.exit(1)


def save_watermark(last_id):
    """Atomic write: write a temp file then replace, so a crash never leaves a broken file."""
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'last_published_id': int(last_id),
                   'updated_at': datetime.now().isoformat(timespec='seconds')}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


def check_watermark(conn, watermark):
    """
    SQLite reuses row ids only when the newest rows are deleted
    (e.g. the table became empty). If max(id) < watermark, ids restarted,
    so every current row is unpublished: reset the watermark to 0.
    """
    max_id = conn.execute('SELECT MAX(id) FROM readings').fetchone()[0] or 0
    if max_id < watermark:
        log.warning(f'max(id)={max_id} < watermark={watermark}: row ids restarted, reset watermark to 0')
        save_watermark(0)
        return 0
    return watermark


# ---------------- MQTT ----------------

def build_client():
    """Creating the MQTT client, be compatible with paho-mqtt 1.x and 2.x。"""
    try:
        client = mqtt.Client(                            # paho-mqtt 2.x
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=MQTT_CLIENT_ID,
        )
    except AttributeError:
        client = mqtt.Client(client_id=MQTT_CLIENT_ID)   # paho-mqtt 1.x

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    if MQTT_TLS_ENABLED:
        _configure_tls(client)

    return client


def _configure_tls(client):
    """
    Configuring the TLS that are from env:
      - MQTT_CA_CERT      : Broker CA
      - MQTT_CLIENT_CERT  : Client certificate (mTLS. option)
      - MQTT_CLIENT_KEY   : Client passkey (mTLS, option)
    """
    if bool(MQTT_CLIENT_CERT) != bool(MQTT_CLIENT_KEY):
        log.error('MQTT_CLIENT_CERT 與 MQTT_CLIENT_KEY will be empty or provided at the same time.')
        sys.exit(1)

    # Checking specified certificate path is existed, retrieve clear error
    for label, path in (
        ('MQTT_CA_CERT', MQTT_CA_CERT),
        ('MQTT_CLIENT_CERT', MQTT_CLIENT_CERT),
        ('MQTT_CLIENT_KEY', MQTT_CLIENT_KEY),
    ):
        if path and not os.path.isfile(path):
            log.error(f'{label} specified file is not existed: {path}')
            sys.exit(1)

    if not MQTT_CA_CERT and not MQTT_TLS_INSECURE:
        log.warning(
            'MQTT_CA_CERT will use systemd CA to verify broker by default;'
            'If the broker use self-signed cert, please configure MQTT_CA_CERT to map current CA'
        )

    try:
        client.tls_set(
            ca_certs=MQTT_CA_CERT or None,
            certfile=MQTT_CLIENT_CERT or None,
            keyfile=MQTT_CLIENT_KEY or None,
            keyfile_password=MQTT_CLIENT_KEY_PASS,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
    except (ssl.SSLError, FileNotFoundError, ValueError) as e:
        log.error(f'TLS config is failed: {e}')
        sys.exit(1)

    if MQTT_TLS_INSECURE:
        client.tls_insecure_set(True)
        log.warning('MQTT_TLS_INSECURE=true: Disabling hostname and cert verification. DO NOT USE Prod')

    mode = 'mTLS(two-way)' if MQTT_CLIENT_CERT else 'TLS(one-way broker)'
    log.info(f'TLS is enabled, the mode is: {mode}')


# ---------------- Data ----------------

def cutoff_ms():
    return int((datetime.now() - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)


def report_stale(conn, watermark, cutoff):
    """Unpublished records older than WINDOW_DAYS are skipped (e.g. after a long cloud outage)."""
    stale = conn.execute(
        'SELECT COUNT(*) FROM readings WHERE id > ? AND ts_ms < ?', (watermark, cutoff)
    ).fetchone()[0]
    if stale:
        log.warning(f'{stale} unpublished rows are older than {WINDOW_DAYS} days and will be skipped')


def load_pending(conn, watermark, cutoff):
    """Ordered by id, so the published rows always form a contiguous prefix."""
    return conn.execute(
        'SELECT id, uuid, ts_ms, formatted_time, timestamp, current, batt, temp '
        'FROM readings WHERE id > ? AND ts_ms >= ? ORDER BY id LIMIT ?',
        (watermark, cutoff, MAX_BATCH)
    ).fetchall()


def publish_rows(client, rows):
    """
    Using QoS 1 to publish message and wait for the broker response.
    Stops at the first failure, so the returned ids are a contiguous prefix of rows.
    """
    published_ids = []

    for row in rows:
        record = dict(row)
        record.pop('id', None)

        topic = f"{TOPIC_PREFIX}"
        payload = json.dumps(record, ensure_ascii=False)

        try:
            info = client.publish(topic, payload, qos=1)
            info.wait_for_publish(timeout=PUBLISH_TIMEOUT)

            if info.is_published():
                published_ids.append(row['id'])
            else:
                log.error(f"id={row['id']} is not received. Stopped. Keep and retry.")
                break
        except (ValueError, RuntimeError) as e:
            log.error(f"id={row['id']} is failed to publish. {e}, Stopped. Keep and retry.")
            break

    return published_ids


def main():
    start = time.time()
    log.info('=' * 50)
    log.info('MQTT Publisher has been launched (SQLite, watermark mode)')

    if not os.path.isfile(DB_PATH):
        log.error(f'Cannot find the SQLite DB path: {DB_PATH}')
        sys.exit(1)

    conn = open_db(DB_PATH)

    try:
        cutoff = cutoff_ms()
        watermark = check_watermark(conn, load_watermark())
        report_stale(conn, watermark, cutoff)

        rows = load_pending(conn, watermark, cutoff)
        if not rows:
            log.info(f'No publish data (watermark id={watermark}). Stopped.')
            return

        log.info(f'Publish {len(rows)} rows (from id={rows[0]["id"]})')

        client = build_client()
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        except (OSError, ssl.SSLError) as e:
            proto = 'mqtts' if MQTT_TLS_ENABLED else 'mqtt'
            log.error(f'Cannot connect the broker {proto}://{MQTT_HOST}:{MQTT_PORT} — {e}')
            log.error('Data has been keeped. Retry this at the next time.')
            sys.exit(1)

        client.loop_start()

        try:
            published_ids = publish_rows(client, rows)
        finally:
            client.loop_stop()
            client.disconnect()

        if published_ids:
            save_watermark(published_ids[-1])
            log.info(f'Published {len(published_ids)} rows, watermark -> id={published_ids[-1]}')

        failed = len(rows) - len(published_ids)
        if failed:
            log.warning(f'{failed} rows are not published. Keep data in DB. Retry this at next time')

        pending = conn.execute(
            'SELECT COUNT(*) FROM readings WHERE id > ? AND ts_ms >= ?',
            (published_ids[-1] if published_ids else watermark, cutoff)
        ).fetchone()[0]
        log.info(f'Pending to publish: {pending} rows')

    finally:
        conn.close()
        log.info(f'Executed has been done. Elaspe {time.time() - start:.1f} seconds')


if __name__ == '__main__':
    main()
