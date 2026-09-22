"""
sensor_data.db 原始資料清理
---------------------------
分批刪除「超過 RAW_KEEP_DAYS 天」且「已上傳到雲端」的原始讀值。
上傳進度由 MQTT publisher 寫入的狀態檔(publisher_state.json)取得,
尚未上傳的資料一律保留,避免雲端斷線期間的資料遺失。
原本 sensor_data.db 的資料表結構不會被修改。

用法:
    python3 3e_green_cleanup.py             執行清理
    python3 3e_green_cleanup.py --dry-run   只顯示會刪除多少筆,不做任何修改

注意:保存天數必須大於 edge_ai.py 的訓練天數。
"""

import os
import sys
import json
import time
import sqlite3


# ---------------- 設定 ----------------
DB_PATH = os.getenv('GW_DB_PATH', './sensor_data.db')
STATE_PATH = os.getenv('GW_PUB_STATE',
                       os.path.join(os.path.dirname(os.path.abspath(DB_PATH)),
                                    'publisher_state.json'))
RAW_KEEP_DAYS = float(os.getenv('RAW_KEEP_DAYS', '30'))   # 原始資料保留天數
MIN_KEEP_DAYS = 15         # 安全下限,避免誤設導致訓練資料不足
DELETE_BATCH = 5000        # 每批刪除筆數
# --------------------------------------


def fmt(ms):
    """毫秒 timestamp 轉為台北時間(台灣無日光節約時間,固定 +8 小時)。"""
    return time.strftime('%Y-%m-%d %H:%M', time.gmtime(ms / 1000 + 8 * 3600))


def load_watermark():
    """讀取 publisher 已確認上傳的最大 id;讀不到時回傳 None(不刪除任何資料)。"""
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            state = json.load(f)
        return int(state['last_published_id']), state.get('updated_at')
    except FileNotFoundError:
        print(f'找不到上傳進度檔 {STATE_PATH},為避免刪除尚未上傳的資料,停止刪除')
    except (ValueError, KeyError, OSError) as e:
        print(f'上傳進度檔讀取失敗({e}),為避免刪除尚未上傳的資料,停止刪除')
    return None, None


def main():
    dry_run = '--dry-run' in sys.argv
    if RAW_KEEP_DAYS < MIN_KEEP_DAYS:
        print(f'RAW_KEEP_DAYS={RAW_KEEP_DAYS:g} 小於安全下限 {MIN_KEEP_DAYS} 天,'
              f'為避免訓練資料不足,停止執行')
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')

    raw_min = conn.execute('SELECT MIN(ts_ms) FROM readings').fetchone()[0]
    if raw_min is None:
        print('sensor_data.db 沒有資料')
        return

    watermark, updated_at = load_watermark()
    if watermark is None:
        sys.exit(1)

    cutoff = int((time.time() - RAW_KEEP_DAYS * 86400) * 1000)
    n_total = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    n_old = conn.execute('SELECT COUNT(*) FROM readings WHERE ts_ms < ?',
                         (cutoff,)).fetchone()[0]
    n_delete = conn.execute('SELECT COUNT(*) FROM readings WHERE ts_ms < ? AND id <= ?',
                            (cutoff, watermark)).fetchone()[0]

    print(f'原始資料最早時間 : {fmt(raw_min)}')
    print(f'上傳進度         : id <= {watermark}(更新於 {updated_at})')
    print(f'刪除早於         : {fmt(cutoff)}(保留 {RAW_KEEP_DAYS:g} 天)')
    print(f'將刪除           : {n_delete} / {n_total} 筆')
    if n_old > n_delete:
        print(f'⚠ 有 {n_old - n_delete} 筆超過保存期限但尚未上傳,已保留。'
              f'請確認 publisher 與雲端連線是否正常')

    if dry_run:
        print('\n--dry-run:未做任何修改')
        return

    # 分批刪除,每批提交一次,避免長時間鎖住記錄器
    deleted, t0 = 0, time.monotonic()
    while True:
        cur = conn.execute(
            'DELETE FROM readings WHERE id IN '
            '(SELECT id FROM readings WHERE ts_ms < ? AND id <= ? LIMIT ?)',
            (cutoff, watermark, DELETE_BATCH))
        conn.commit()
        if cur.rowcount <= 0:
            break
        deleted += cur.rowcount
        time.sleep(0.2)          # 讓記錄器有機會寫入
    print(f'已刪除 {deleted} 筆({time.monotonic() - t0:.1f} 秒)')
    conn.close()


if __name__ == '__main__':
    main()
