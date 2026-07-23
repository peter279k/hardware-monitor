"""
3e Green Gateway 感測資料記錄器 (SQLite 版)
--------------------------------------------
用法:
    python3 3e_green_gw_logger.py <gateway_ip>

相較 TinyDB 版的改動:
  - 去重改由 UNIQUE(uuid, ts_ms) + INSERT OR IGNORE 處理,
    不再需要 seen 集合,記憶體佔用固定不隨資料量成長
  - 保留原始毫秒 timestamp (ts_ms),供範圍查詢與去重使用
  - WAL 模式,可與 publisher 同時安全存取同一個資料庫
  - 支援 SIGTERM,systemctl stop 時也會先沖寫暫存資料
  - 無漂移排程,取樣週期穩定維持在 INTERVAL 秒

安裝相依套件:
    pip install requests          # sqlite3 為 Python 標準函式庫
"""

import os
import sys
import time
import signal
import sqlite3
import requests
from datetime import datetime
from zoneinfo import ZoneInfo


# ---------------- 設定 ----------------
DB_PATH = os.getenv('GW_DB_PATH', './sensor_data.db')   # 可改成 USB/SSD 路徑
INTERVAL = 60        # 取樣間隔(秒),每分鐘一筆
BATCH_SIZE = 10      # 累積幾筆才寫入磁碟(約 10 分鐘)
# --------------------------------------


SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id             INTEGER PRIMARY KEY,
    uuid           TEXT    NOT NULL,
    ts_ms          INTEGER NOT NULL,   -- gateway 原始毫秒 timestamp
    formatted_time TEXT,               -- gateway 提供的 formatedTime
    timestamp      TEXT    NOT NULL,   -- 轉換後的台北時間字串
    current        REAL,
    batt           REAL,
    temp           REAL,
    UNIQUE(uuid, ts_ms)                -- 去重的關鍵
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts_ms);
"""


def convert_timestamp(timestamp):
    """毫秒 timestamp 轉為台北時區的可讀字串。"""
    tz = ZoneInfo('Asia/Taipei')
    date_time = datetime.fromtimestamp(timestamp / 1000, tz)
    return date_time.strftime('%Y-%m-%d %H:%M:%S')


def to_float(value):
    """安全轉型,無法轉換時回傳 None(SQLite 存為 NULL)。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def init_db(path):
    """開啟資料庫並套用 schema 與 SD 卡友善的 pragma。"""
    conn = sqlite3.connect(path, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')     # 允許同時讀寫,減少 fsync
    conn.execute('PRAGMA synchronous=NORMAL')   # 以極小斷電風險換較少同步寫入
    conn.execute('PRAGMA busy_timeout=30000')   # 遇鎖時最多等 30 秒
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def fetch_readings(list_url):
    """
    從 gateway 取得資料,回傳 list of tuple(對應 INSERT 的欄位順序)。
    網路或解析失敗時回傳空 list,不讓整個記錄器中斷。
    """
    try:
        resp = requests.get(list_url, timeout=10)
        resp.raise_for_status()
        json_lists = resp.json()
    except requests.RequestException as e:
        print(f'  取得資料失敗: {e}')
        return []
    except ValueError as e:          # JSON 解析錯誤
        print(f'  回應格式錯誤: {e}')
        return []

    readings = []
    for item in json_lists:
        try:
            ts_ms = int(item['timestamp'])
            readings.append((
                item['uuid'],
                ts_ms,
                item.get('formatedTime'),
                convert_timestamp(ts_ms),
                to_float(item.get('current')),
                to_float(item.get('battery')),
                to_float(item.get('temperature')),
            ))
        except (KeyError, TypeError, ValueError) as e:
            print(f'  略過格式異常的資料: {e}')
    return readings


def flush(conn, buffer):
    """
    批次寫入。INSERT OR IGNORE 會自動略過重複的 (uuid, ts_ms),
    回傳實際新增的筆數。
    """
    if not buffer:
        return 0
    cur = conn.executemany(
        'INSERT OR IGNORE INTO readings '
        '(uuid, ts_ms, formatted_time, timestamp, current, batt, temp) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        buffer
    )
    conn.commit()
    inserted = cur.rowcount
    buffer.clear()
    return inserted


def _handle_sigterm(signum, frame):
    """讓 systemctl stop 走與 Ctrl+C 相同的收尾流程。"""
    raise KeyboardInterrupt


def main():
    if len(sys.argv) < 2:
        print('用法: python3 3e_green_gw_logger.py <gateway_ip>')
        sys.exit(1)

    list_url = f'http://{sys.argv[1]}:9100/list'
    signal.signal(signal.SIGTERM, _handle_sigterm)

    print('3e Green Gateway 感測資料記錄器啟動中 (SQLite)...')
    print(f'資料來源: {list_url}')
    print(f'資料庫  : {DB_PATH}')

    conn = init_db(DB_PATH)
    total = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    print(f'資料庫已有 {total} 筆紀錄')

    buffer = []          # 記憶體暫存區
    print('開始記錄,按 Ctrl+C 停止...\n')

    next_run = time.monotonic()
    try:
        while True:
            readings = fetch_readings(list_url)
            buffer.extend(readings)

            print(f'{datetime.now():%H:%M:%S} 取得 {len(readings)} 筆 '
                  f'(暫存 {len(buffer)}/{BATCH_SIZE})')

            # 累積到一定數量才一次寫入(減少 SD 卡寫入次數)
            if len(buffer) >= BATCH_SIZE:
                inserted = flush(conn, buffer)
                print(f'  → 已批次寫入,新增 {inserted} 筆(重複者自動略過)')

            # 無漂移排程:對齊固定週期,不受抓取耗時影響
            next_run += INTERVAL
            time.sleep(max(0, next_run - time.monotonic()))

    except KeyboardInterrupt:
        # 結束前把暫存區剩餘資料寫入,避免遺失
        if buffer:
            inserted = flush(conn, buffer)
            print(f'\n結束前寫入,新增 {inserted} 筆')
        conn.close()
        print('已安全關閉資料庫。')


if __name__ == '__main__':
    main()
