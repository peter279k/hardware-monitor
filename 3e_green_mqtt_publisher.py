"""
3e Green Gateway → RabbitMQ (MQTT) Publisher  (SQLite)
----------------------------------------------------------
Steps:
  1. Read sensor data from SQLite for 14 days
  2. Using the MQTT QoS 1 to publish the RabbitMQ(it should enable the rabbitmq_mqtt plugin)
  3. Only remove broker PUBACK confirmed records
  4. Failed publishing records will store in the database, and it will retry at next time.

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

WINDOW_DAYS = 14           # Reading range: N days
MAX_BATCH = 5000           # Batch max value
PUBLISH_TIMEOUT = 10       # Wait publishing timeout

# Outdated data
PURGE_STALE = os.getenv('GW_PURGE_STALE', 'false').lower() == 'true'

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

    # Checkinh specified certiciate path is existed, retrieve clear error
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


def cutoff_ms():
    return int((datetime.now() - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)


def handle_stale(conn, cutoff):
    stale = conn.execute(
        'SELECT COUNT(*) FROM readings WHERE ts_ms < ?', (cutoff,)
    ).fetchone()[0]

    if not stale:
        return

    if PURGE_STALE:
        conn.execute('DELETE FROM readings WHERE ts_ms < ?', (cutoff,))
        conn.commit()
        log.info(f'Clean {stale} rows the exceed {WINDOW_DAYS} sensor data')
    else:
        log.warning(
            f'It  had {stale} rows that exceed {WINDOW_DAYS} out of range;'
            f'If it needs to be truncated. Please configure the GW_PURGE_STALE=true'
        )


def load_pending(conn, cutoff):
    return conn.execute(
        'SELECT id, uuid, ts_ms, formatted_time, timestamp, current, batt, temp FROM readings WHERE ts_ms >= ? ORDER BY ts_ms LIMIT ?',
        (cutoff, MAX_BATCH)
    ).fetchall()


def publish_rows(client, rows):
    """
    Using QoS 1 to publish message and wait for the broker response
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


def delete_published(conn, ids):
    CHUNK = 500
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        placeholders = ','.join('?' * len(chunk))
        conn.execute(f'DELETE FROM readings WHERE id IN ({placeholders})', chunk)
    conn.commit()


def main():
    start = time.time()
    log.info('=' * 50)
    log.info('MQTT Publisher has been launched (SQLite)')

    if not os.path.isfile(DB_PATH):
        log.error(f'Cannot find the SQLite DB path: {DB_PATH}')
        sys.exit(1)

    conn = open_db(DB_PATH)

    try:
        cutoff = cutoff_ms()
        handle_stale(conn, cutoff)

        rows = load_pending(conn, cutoff)
        if not rows:
            log.info('No publish data. Stopped.')
            return

        log.info(f'Publish {len(rows)} rows')

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
            delete_published(conn, published_ids)
            log.info(f'Published and delete {len(published_ids)} rows')

        failed = len(rows) - len(published_ids)
        if failed:
            log.warning(f'{failed} rows are not published. Keep data in DB. Retry this at next time')

        remaining = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
        log.info(f'Data remain: {remaining} rows')

    finally:
        conn.close()
        log.info(f'Executed has been done. Elaspe {time.time() - start:.1f} seconds')


if __name__ == '__main__':
    main()
