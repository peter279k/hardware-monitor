"""
3e Green Gateway 感測資料記錄器 (SQLite 版,高頻取樣)
-----------------------------------------------------
用法:
    python3 3e_green_gw_logger.py <gateway_ip> [查詢間隔秒數]

    例: python3 3e_green_gw_logger.py 192.168.1.50 2
    也可用環境變數 GW_INTERVAL 設定查詢間隔

相較前一版的改動:
  - 查詢間隔可調(預設 2 秒),實際取樣頻率取決於感測器的 BLE 回報頻率
  - 記憶體內先以 (uuid, ts_ms) 去重,暫存區只放新資料
  - 改為「依時間」批次寫入(預設每 60 秒),SD 卡寫入次數與原本相當
  - 使用 requests.Session 重複使用連線,HTTP timeout 縮短為 3 秒
  - 資料庫欄位與前一版相同,可直接沿用既有資料庫
  - temperature 為 32767(無效值)時存為 NULL
  - 每分鐘輸出一次摘要,並統計每顆感測器的實際回報間隔
  - 抓取耗時超過間隔時會重新對齊排程,不會連續補抓

安裝相依套件:
    pip install requests          # sqlite3 為 Python 標準函式庫
"""

import os
import sys
import time
import signal
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


# ---------------- 設定 ----------------
DB_PATH = os.getenv('GW_DB_PATH', './sensor_data.db')   # 可改成 USB/SSD 路徑
INTERVAL = float(os.getenv('GW_INTERVAL', '2'))      # 查詢閘道器的間隔(秒)
FLUSH_SEC = float(os.getenv('GW_FLUSH_SEC', '60'))   # 多久批次寫入一次磁碟(秒)
STATUS_SEC = 60        # 多久輸出一次狀態摘要(秒)
HTTP_TIMEOUT = 3       # HTTP 逾時(秒),應小於或接近 INTERVAL
INVALID_TEMP = 32767   # 感測器以 0x7FFF 表示溫度無效
TZ = ZoneInfo('Asia/Taipei')
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

INSERT_SQL = (
    'INSERT OR IGNORE INTO readings '
    '(uuid, ts_ms, formatted_time, timestamp, current, batt, temp) '
    'VALUES (?, ?, ?, ?, ?, ?, ?)'
)


def convert_timestamp(ts_ms):
    """毫秒 timestamp 轉為台北時區的可讀字串。"""
    return datetime.fromtimestamp(ts_ms / 1000, TZ).strftime('%Y-%m-%d %H:%M:%S')


def to_float(value):
    """安全轉型,無法轉換時回傳 None(SQLite 存為 NULL)。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scaled(value, scale, default_scale=0.01):
    """原始值乘上倍率;任一值無效時回傳 None,而非丟出例外。"""
    v = to_float(value)
    s = to_float(scale) if scale is not None else default_scale
    if v is None or s is None:
        return None
    return v * s


def init_db(path):
    """開啟資料庫並套用 schema 與 SD 卡友善的 pragma。"""
    conn = sqlite3.connect(path, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')     # 允許同時讀寫,減少 fsync
    conn.execute('PRAGMA synchronous=NORMAL')   # 以極小斷電風險換較少同步寫入
    conn.execute('PRAGMA busy_timeout=30000')   # 遇鎖時最多等 30 秒
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_last_ts(conn):
    """讀取每顆感測器在資料庫中的最新 ts_ms,重啟後可延續去重與間隔統計。"""
    return dict(conn.execute('SELECT uuid, MAX(ts_ms) FROM readings GROUP BY uuid'))


def fetch_readings(session, list_url):
    """
    從 gateway 取得資料,回傳 list of tuple(對應 INSERT_SQL 的欄位順序)。
    網路或解析失敗時回傳 None,讓呼叫端可區分「失敗」與「沒有資料」。
    """
    try:
        resp = session.get(list_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        json_lists = resp.json()
    except requests.RequestException as e:
        print(f'  取得資料失敗: {e}')
        return None
    except ValueError as e:          # JSON 解析錯誤
        print(f'  回應格式錯誤: {e}')
        return None

    readings = []
    for item in json_lists:
        try:
            ts_ms = int(item['timestamp'])
            temp = to_float(item.get('temperature'))
            if temp == INVALID_TEMP:
                temp = None
            readings.append((
                item['uuid'],
                ts_ms,
                item.get('formatedTime'),
                convert_timestamp(ts_ms),
                scaled(item.get('current'), item.get('scale')),
                to_float(item.get('battery')),
                temp,
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
    cur = conn.executemany(INSERT_SQL, buffer)
    conn.commit()
    inserted = cur.rowcount
    buffer.clear()
    return inserted


def print_status(stats, gaps, polls, fails):
    """輸出這段期間的摘要與每顆感測器的實際回報間隔。"""
    print(f'{datetime.now(TZ):%H:%M:%S} 查詢 {polls} 次,失敗 {fails} 次')
    for uuid in sorted(stats):
        g = gaps.get(uuid, [])
        gap_txt = (f'回報間隔中位數 {statistics.median(g):.1f} 秒,'
                   f'最長 {max(g):.1f} 秒' if g else '回報間隔: 資料不足')
        print(f'  {uuid}: 新資料 {stats[uuid]} 筆,{gap_txt}')


def _handle_sigterm(signum, frame):
    """讓 systemctl stop 走與 Ctrl+C 相同的收尾流程。"""
    raise KeyboardInterrupt


def main():
    global INTERVAL
    if len(sys.argv) < 2:
        print('用法: python3 3e_green_gw_logger.py <gateway_ip> [查詢間隔秒數]')
        sys.exit(1)
    if len(sys.argv) >= 3:
        INTERVAL = float(sys.argv[2])

    list_url = f'http://{sys.argv[1]}:9100/list'
    signal.signal(signal.SIGTERM, _handle_sigterm)

    print('3e Green Gateway 感測資料記錄器啟動中 (SQLite,高頻取樣)...')
    print(f'資料來源: {list_url}')
    print(f'資料庫  : {DB_PATH}')
    print(f'查詢間隔: {INTERVAL} 秒,批次寫入: 每 {FLUSH_SEC:.0f} 秒')

    conn = init_db(DB_PATH)
    total = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    print(f'資料庫已有 {total} 筆紀錄')

    session = requests.Session()
    last_ts = load_last_ts(conn)     # uuid -> 最後一筆 ts_ms
    buffer = []                      # 只放新資料的暫存區
    stats = defaultdict(int)         # 本期各感測器新資料筆數
    gaps = defaultdict(list)         # 本期各感測器回報間隔(秒)
    polls = fails = 0

    print('開始記錄,按 Ctrl+C 停止...\n')

    now = time.monotonic()
    next_run, next_flush, next_status = now, now + FLUSH_SEC, now + STATUS_SEC
    try:
        while True:
            readings = fetch_readings(session, list_url)
            polls += 1
            if readings is None:
                fails += 1
                readings = []

            for r in readings:
                uuid, ts_ms = r[0], r[1]
                prev = last_ts.get(uuid)
                if prev is not None and ts_ms <= prev:
                    continue                     # 重複或較舊的資料,略過
                if prev is not None:
                    gaps[uuid].append((ts_ms - prev) / 1000)
                last_ts[uuid] = ts_ms
                buffer.append(r)
                stats[uuid] += 1

            now = time.monotonic()
            if now >= next_flush:
                inserted = flush(conn, buffer)
                print(f'  → 已批次寫入,新增 {inserted} 筆')
                next_flush += FLUSH_SEC

            if now >= next_status:
                print_status(stats, gaps, polls, fails)
                stats.clear(); gaps.clear()
                polls = fails = 0
                next_status += STATUS_SEC

            # 無漂移排程;若抓取耗時已超過間隔,重新對齊而不連續補抓
            next_run += INTERVAL
            delay = next_run - time.monotonic()
            if delay < 0:
                next_run = time.monotonic()
                delay = 0
            time.sleep(delay)

    except KeyboardInterrupt:
        # 結束前把暫存區剩餘資料寫入,避免遺失
        if buffer:
            inserted = flush(conn, buffer)
            print(f'\n結束前寫入,新增 {inserted} 筆')
        conn.close()
        print('已安全關閉資料庫。')


if __name__ == '__main__':
    main()
