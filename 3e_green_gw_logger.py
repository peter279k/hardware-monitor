import os
import sys
import time
import requests
from tinydb import TinyDB
from datetime import datetime
from zoneinfo import ZoneInfo


# ---------------- 設定 ----------------
UUID_PATH = './uuid.txt'                    # 目標裝置清單
DB_PATH = './sensor_data.json'              # TinyDB 資料檔(可改成 USB/SSD 路徑)
LIST_URL = os.getenv('LIST_URL', '')        # gateway 資料來源
INTERVAL = 60                               # 取樣間隔(秒),每分鐘一筆
BATCH_SIZE = 10                             # 累積幾筆才寫入磁碟(約 10 分鐘)
# --------------------------------------


def convert_timestamp(timestamp):
    tz = ZoneInfo("Asia/Taipei")
    date_time = datetime.fromtimestamp(timestamp / 1000, tz)
    return date_time.strftime('%Y-%m-%d %H:%M:%S')

def fetch_readings():
    """
    從 gateway 抓取資料,篩選出目標 uuid 的讀數,回傳 list of dict。
    網路或解析失敗時回傳空 list,不讓整個記錄器中斷。
    """
    try:
        resp = requests.get(LIST_URL, timeout=10)
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
        readings.append({
            'uuid': item['uuid'],
            'current': float(item['current']),
            'batt': item['battery'],
            'temp': item['temperature'],
            'formatted_time': item['formatedTime'],
            'timestamp': convert_timestamp(item['tiemstamp']),
        })
    return readings


def main():
    print('3e Green Gateway 感測資料記錄器啟動中...')

    db = TinyDB(DB_PATH)

    # 載入資料庫中既有的 (uuid, time) 組合,重啟後也不會重複寫入
    seen = {(r['uuid'], r['time']) for r in db.all()}
    print(f'資料庫已有 {len(seen)} 筆紀錄')

    buffer = []          # 記憶體暫存區
    print('開始記錄,按 Ctrl+C 停止...\n')

    try:
        while True:
            readings = fetch_readings()

            new_count = 0
            for r in readings:
                key = (r['uuid'], r['time'])
                if key in seen:
                    continue                 # 同一裝置、同一時間戳,已記錄過
                seen.add(key)
                buffer.append(r)
                new_count += 1

            print(f"{datetime.now():%H:%M:%S} 取得 {len(readings)} 筆,"
                  f"新增 {new_count} 筆 (暫存 {len(buffer)}/{BATCH_SIZE})")

            # 累積到一定數量才一次寫入(減少 SD 卡寫入次數)
            if len(buffer) >= BATCH_SIZE:
                db.insert_multiple(buffer)
                print(f'  → 已批次寫入 {len(buffer)} 筆')
                buffer.clear()

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        # 結束前把暫存區剩餘資料寫入,避免遺失
        if buffer:
            db.insert_multiple(buffer)
            print(f'\n結束前寫入剩餘 {len(buffer)} 筆')
        db.close()
        print('已安全關閉資料庫。')


if __name__ == '__main__':
    main()
