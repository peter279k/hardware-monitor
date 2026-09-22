"""
邊緣 AI 電流異常偵測 (Isolation Forest)
---------------------------------------
讀取 3e_green_gw_logger.py 寫入的 sensor_data.db,以每分鐘為視窗擷取特徵,
每顆感測器各自訓練一個模型,並持續推論。
推論結果寫入獨立的 ai_results.db,不修改原本的資料庫。

用法:
    python3 edge_ai.py train [天數]    以最近 N 天資料訓練(預設 14)
    python3 edge_ai.py eval  [天數]    評估誤報率與模擬異常的偵出率
    python3 edge_ai.py infer           持續推論(每分鐘一次)
    python3 edge_ai.py infer --once    只推論一次(測試用)

安裝相依套件:
    pip install pandas scikit-learn joblib --break-system-packages
"""

import os
import sys
import time
import signal
import sqlite3
from collections import deque
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


# ---------------- 設定 ----------------
DB_PATH = os.getenv('GW_DB_PATH', './sensor_data.db')
RESULT_DB = os.getenv('AI_RESULT_DB', './ai_results.db')
MODEL_DIR = os.getenv('AI_MODEL_DIR', './models')

WINDOW_SEC = 60            # 特徵視窗長度(秒)
MIN_SAMPLES = 10           # 視窗內少於此筆數視為資料不足
ON_THRESHOLD = 0.5         # 高於此電流(A)視為運轉中
USE_TIME_FEATURES = False  # 設備有固定作息時設為 True,可偵測「不該運轉的時段在運轉」

THRESH_PCT = 0.5           # 取訓練資料分數最低的 0.5% 作為異常門檻
# 範圍檢查:補強 Isolation Forest 對「超出訓練範圍」不敏感的弱點
# both = 過高或過低都算異常,upper = 只有過高算異常
RANGE_CHECK = {'mean': 'both', 'max': 'upper', 'std': 'upper'}
RANGE_PCT = 0.5            # 以訓練資料的第 0.5 與 99.5 百分位作為正常範圍
RANGE_MARGIN = 0.2         # 範圍外再加上 20% 的餘裕
MIN_TRAIN_WINDOWS = 720    # 每顆感測器至少需要的運轉視窗數(約 12 小時)
ALARM_K, ALARM_N = 3, 5    # 最近 N 個視窗中有 K 個異常才發警報
NODATA_WARN = 5            # 連續幾個視窗資料不足就發出通訊警告
OVERCURRENT_A = None       # 硬性過電流門檻(A),例: 150;None 表示不啟用
LAG_SEC = 90               # 等待記錄器批次寫入的時間,需大於 GW_FLUSH_SEC
TZ = ZoneInfo('Asia/Taipei')
# --------------------------------------

BASE_FEATURES = ['mean', 'std', 'max', 'min', 'range',
                 'max_step', 'on_ratio', 'starts']
WIN_MS = WINDOW_SEC * 1000


def feature_cols():
    return BASE_FEATURES + (['hour_sin', 'hour_cos'] if USE_TIME_FEATURES else [])


# ============ 資料讀取與特徵擷取 ============

def open_db(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def load_readings(conn, start_ms, end_ms, uuid=None):
    sql = ('SELECT uuid, ts_ms, current FROM readings '
           'WHERE ts_ms >= ? AND ts_ms < ? AND current IS NOT NULL')
    args = [start_ms, end_ms]
    if uuid:
        sql += ' AND uuid = ?'
        args.append(uuid)
    return pd.read_sql_query(sql, conn, params=args)


def make_features(df):
    """將原始讀值整理成每個 (uuid, 視窗) 一列的特徵表。"""
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values(['uuid', 'ts_ms']).copy()
    df['win'] = df['ts_ms'] // WIN_MS * WIN_MS
    keys = ['uuid', 'win']
    df['step'] = df.groupby(keys)['current'].diff().abs()
    df['on'] = df['current'] > ON_THRESHOLD
    prev_on = df.groupby(keys)['on'].shift()
    df['start'] = df['on'] & prev_on.eq(False)      # 由停機轉為運轉

    f = df.groupby(keys).agg(
        count=('current', 'size'),
        mean=('current', 'mean'),
        std=('current', 'std'),
        max=('current', 'max'),
        min=('current', 'min'),
        max_step=('step', 'max'),
        on_ratio=('on', 'mean'),
        starts=('start', 'sum'),
    ).reset_index()
    f[['std', 'max_step']] = f[['std', 'max_step']].fillna(0.0)
    f['range'] = f['max'] - f['min']
    f['starts'] = f['starts'].astype(int)

    local = pd.to_datetime(f['win'], unit='ms', utc=True).dt.tz_convert(TZ)
    hour = local.dt.hour + local.dt.minute / 60
    f['hour_sin'] = np.sin(2 * np.pi * hour / 24)
    f['hour_cos'] = np.cos(2 * np.pi * hour / 24)
    f['time'] = local.dt.strftime('%Y-%m-%d %H:%M')

    f['state'] = np.where(f['count'] < MIN_SAMPLES, 'no_data',
                          np.where(f['max'] <= ON_THRESHOLD, 'off', 'run'))
    return f


def load_features(days):
    end_ms = int(time.time() * 1000) // WIN_MS * WIN_MS   # 不含尚未結束的視窗
    start_ms = end_ms - days * 86400 * 1000
    conn = open_db(DB_PATH)
    df = load_readings(conn, start_ms, end_ms)
    conn.close()
    return make_features(df)


def exclude_alarms(f):
    """重新訓練時排除曾發出警報的視窗,避免模型把異常學成正常。"""
    if f.empty or not os.path.exists(RESULT_DB):
        return f
    conn = open_db(RESULT_DB)
    try:
        bad = pd.read_sql_query(
            'SELECT uuid, win_ms AS win FROM ai_results WHERE alarm = 1', conn)
    except Exception:
        bad = pd.DataFrame(columns=['uuid', 'win'])
    conn.close()
    if bad.empty:
        return f
    merged = f.merge(bad.assign(_bad=1), on=['uuid', 'win'], how='left')
    print(f'排除 {int(merged["_bad"].notna().sum())} 個曾發出警報的視窗')
    return merged[merged['_bad'].isna()].drop(columns='_bad')


# ================ 訓練 ================

def fit(run_windows):
    X = run_windows[feature_cols()].to_numpy(dtype=float)
    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)
    model = IsolationForest(n_estimators=200, random_state=42).fit(Xs)
    scores = model.score_samples(Xs)          # 分數越低越異常

    bounds = {}
    for col in RANGE_CHECK:
        lo = float(run_windows[col].quantile(RANGE_PCT / 100))
        hi = float(run_windows[col].quantile(1 - RANGE_PCT / 100))
        span = max(hi - lo, 0.05 * abs(hi), 0.1)   # 避免範圍過窄造成誤報
        bounds[col] = (lo - RANGE_MARGIN * span, hi + RANGE_MARGIN * span)

    return {
        'scaler': scaler,
        'model': model,
        'threshold': float(np.percentile(scores, THRESH_PCT)),
        'bounds': bounds,
        'features': feature_cols(),
        'n_windows': len(X),
        'on_threshold': ON_THRESHOLD,
    }


def score(bundle, rows):
    X = rows[bundle['features']].to_numpy(dtype=float)
    return bundle['model'].score_samples(bundle['scaler'].transform(X))


def detect(bundle, rows):
    """回傳 (異常分數, 是否異常, 超出範圍的特徵列表)。"""
    sc = score(bundle, rows)
    flags = sc < bundle['threshold']
    out = [[] for _ in range(len(rows))]
    for col, (lo, hi) in bundle.get('bounds', {}).items():
        v = rows[col].to_numpy(dtype=float)
        bad = v > hi
        if RANGE_CHECK.get(col) == 'both':
            bad |= v < lo
        for i in np.flatnonzero(bad):
            out[i].append(col)
    flags = flags | np.array([bool(o) for o in out])
    return sc, flags, out


def model_path(uuid):
    return os.path.join(MODEL_DIR, f'{uuid}.joblib')


def cmd_train(days):
    f = exclude_alarms(load_features(days))
    if f.empty:
        print('資料庫中沒有資料')
        return
    os.makedirs(MODEL_DIR, exist_ok=True)
    for uuid, g in f.groupby('uuid'):
        run = g[g['state'] == 'run']
        counts = g['state'].value_counts().to_dict()
        if len(run) < MIN_TRAIN_WINDOWS:
            print(f'{uuid}: 運轉視窗只有 {len(run)} 個(需 {MIN_TRAIN_WINDOWS}),'
                  f'略過。狀態分布 {counts}')
            continue
        bundle = fit(run)
        bundle.update(uuid=uuid,
                      trained_at=pd.Timestamp.now(tz=TZ).strftime('%Y-%m-%d %H:%M'),
                      data_from=g['time'].min(), data_to=g['time'].max())
        tmp = model_path(uuid) + '.tmp'
        joblib.dump(bundle, tmp)
        os.replace(tmp, model_path(uuid))     # 原子替換,推論程式不會讀到半個檔案
        lo, hi = bundle['bounds']['mean']
        print(f'{uuid}: 已訓練,運轉視窗 {len(run)} 個,門檻 {bundle["threshold"]:.4f},'
              f'資料期間 {bundle["data_from"]} ~ {bundle["data_to"]}')
        print(f'  正常平均電流範圍 {lo:.2f} ~ {hi:.2f} A')


# ================ 評估 ================

def k_of_n(flags):
    hist, out = deque(maxlen=ALARM_N), []
    for x in flags:
        hist.append(bool(x))
        out.append(sum(hist) >= ALARM_K)
    return np.array(out)


def cmd_eval(days):
    """
    依時間切分:前 80% 訓練、後 20% 測試。
    正常資料上的異常比例 ≈ 誤報率;再以模擬異常檢查模型是否抓得到。
    """
    f = exclude_alarms(load_features(days))
    for uuid, g in f.groupby('uuid'):
        run = g[g['state'] == 'run'].sort_values('win')
        if len(run) < MIN_TRAIN_WINDOWS:
            print(f'{uuid}: 運轉視窗不足({len(run)} 個),無法評估')
            continue
        cut = int(len(run) * 0.8)
        train, test = run.iloc[:cut], run.iloc[cut:]
        b = fit(train)
        _, flags, _ = detect(b, test)
        alarms = k_of_n(flags)
        hours = len(test) * WINDOW_SEC / 3600

        n_alarm = int(alarms.sum())
        freq = f'約每 {hours / n_alarm:.1f} 小時一次' if n_alarm else '無誤報'
        print(f'\n{uuid}  訓練 {len(train)} 個視窗,測試 {len(test)} 個視窗(約 {hours:.1f} 小時運轉)')
        print(f'  正常資料被判異常: {flags.mean():.2%}   '
              f'觸發警報: {n_alarm} 個視窗({freq})')

        cases = {
            '負載增加 30%': lambda d: d.assign(
                **{c: d[c] * 1.3 for c in ['mean', 'max', 'min', 'range', 'std', 'max_step']}),
            '電流不穩(波動 ×3)': lambda d: d.assign(
                std=d['std'] * 3, max_step=d['max_step'] * 3, range=d['range'] * 3),
            '負載下降 30%': lambda d: d.assign(
                **{c: d[c] * 0.7 for c in ['mean', 'max', 'min', 'range', 'std', 'max_step']}),
        }
        for name, fn in cases.items():
            rate = detect(b, fn(test.copy()))[1].mean()
            print(f'  模擬異常「{name}」偵出率: {rate:.1%}')


# ================ 推論 ================

RESULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_results (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT    NOT NULL,
    win_ms     INTEGER NOT NULL,   -- 視窗起始時間(毫秒)
    time       TEXT    NOT NULL,   -- 台北時間
    state      TEXT    NOT NULL,   -- run / off / no_data
    count      INTEGER,
    mean       REAL,
    max        REAL,
    std        REAL,
    score      REAL,               -- 異常分數,越低越異常
    threshold  REAL,
    is_anomaly INTEGER,            -- 此視窗是否低於門檻
    alarm      INTEGER,            -- 是否觸發警報(K of N 或過電流)
    reason     TEXT,
    UNIQUE(uuid, win_ms)
);
CREATE INDEX IF NOT EXISTS idx_ai_time ON ai_results(win_ms);
"""


class ModelStore:
    """載入模型,並在重新訓練後自動換上新模型,推論程式不必重啟。"""

    def __init__(self):
        self.models, self.mtimes = {}, {}

    def refresh(self):
        if not os.path.isdir(MODEL_DIR):
            return
        for name in os.listdir(MODEL_DIR):
            if not name.endswith('.joblib'):
                continue
            path = os.path.join(MODEL_DIR, name)
            mtime = os.path.getmtime(path)
            uuid = name[:-7]
            if self.mtimes.get(uuid) != mtime:
                self.models[uuid] = joblib.load(path)
                self.mtimes[uuid] = mtime
                print(f'載入模型 {uuid}(訓練於 {self.models[uuid].get("trained_at")})')


def local_time(win_ms):
    return pd.Timestamp(win_ms, unit='ms', tz='UTC').tz_convert(TZ).strftime('%Y-%m-%d %H:%M')


def infer_once(conn, rconn, store, last_win, history, nodata):
    store.refresh()
    now_ms = int(time.time() * 1000)
    cutoff = (now_ms - LAG_SEC * 1000) // WIN_MS * WIN_MS   # 早於此的視窗才算完整
    rows = []

    for uuid, b in store.models.items():
        start = max(last_win.get(uuid, cutoff - 10 * WIN_MS) + WIN_MS,
                    cutoff - 60 * WIN_MS)                   # 最多補算 1 小時
        if start >= cutoff:
            continue
        f = make_features(load_readings(conn, start, cutoff, uuid))
        f = f.set_index('win') if not f.empty else f
        hist = history.setdefault(uuid, deque(maxlen=ALARM_N))

        for win in range(start, cutoff, WIN_MS):
            r = f.loc[win] if (not f.empty and win in f.index) else None
            state = r['state'] if r is not None else 'no_data'
            sc = is_anom = None
            reasons, out_cols = [], []

            if state == 'run':
                s_arr, f_arr, o_arr = detect(b, r.to_frame().T)
                sc, is_anom, out_cols = float(s_arr[0]), int(f_arr[0]), o_arr[0]
            if state == 'no_data':
                nodata[uuid] = nodata.get(uuid, 0) + 1
                if nodata[uuid] == NODATA_WARN:
                    print(f'⚠ {uuid} 已連續 {NODATA_WARN} 分鐘資料不足,請檢查感測器或閘道器')
            else:
                nodata[uuid] = 0
                hist.append(bool(is_anom))                 # 停機視為正常
                if sum(hist) >= ALARM_K:
                    reasons.append('ai')
                    if out_cols:
                        reasons.append('out_of_range:' + '/'.join(out_cols))
            if OVERCURRENT_A is not None and r is not None and r['max'] > OVERCURRENT_A:
                reasons.append('overcurrent')

            alarm = int(bool(reasons))
            if alarm:
                detail = f"平均 {r['mean']:.2f} A,最大 {r['max']:.2f} A" if r is not None else ''
                print(f'🚨 {local_time(win)} {uuid} 警報({",".join(reasons)}) {detail}')
                # TODO: 在此加入 LINE / Email / MQTT 通知

            rows.append((uuid, win, local_time(win), state,
                         int(r['count']) if r is not None else 0,
                         float(r['mean']) if r is not None else None,
                         float(r['max']) if r is not None else None,
                         float(r['std']) if r is not None else None,
                         sc, b['threshold'], is_anom, alarm,
                         ','.join(reasons) or None))
        last_win[uuid] = cutoff - WIN_MS

    if rows:
        rconn.executemany(
            'INSERT OR IGNORE INTO ai_results (uuid, win_ms, time, state, count, mean, '
            'max, std, score, threshold, is_anomaly, alarm, reason) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        rconn.commit()
    return rows


def _handle_sigterm(signum, frame):
    raise KeyboardInterrupt


def cmd_infer(once=False):
    signal.signal(signal.SIGTERM, _handle_sigterm)
    conn = open_db(DB_PATH)
    rconn = open_db(RESULT_DB)
    rconn.execute('PRAGMA journal_mode=WAL')
    rconn.executescript(RESULT_SCHEMA)

    store = ModelStore()
    store.refresh()
    if not store.models:
        print(f'{MODEL_DIR} 中沒有模型,請先執行 train')
        return
    last_win = dict(rconn.execute('SELECT uuid, MAX(win_ms) FROM ai_results GROUP BY uuid'))
    history, nodata = {}, {}
    print(f'開始推論,共 {len(store.models)} 個模型,每 {WINDOW_SEC} 秒一次...')

    try:
        while True:
            rows = infer_once(conn, rconn, store, last_win, history, nodata)
            if rows:
                n_run = sum(1 for r in rows if r[3] == 'run')
                n_anom = sum(1 for r in rows if r[10])
                print(f'{time.strftime("%H:%M:%S")} 處理 {len(rows)} 個視窗'
                      f'(運轉 {n_run},異常 {n_anom})')
            if once:
                break
            time.sleep(WINDOW_SEC - time.time() % WINDOW_SEC + 5)   # 對齊整分鐘
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        rconn.close()
        print('推論已停止。')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'train':
        cmd_train(int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    elif cmd == 'eval':
        cmd_eval(int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    elif cmd == 'infer':
        cmd_infer(once='--once' in sys.argv)
    else:
        print(__doc__)
