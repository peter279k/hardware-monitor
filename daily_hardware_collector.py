"""
VM Daily Snapshot Collector for Linux
==========================================================================
Fields:
  datetime | CPU | Memory | Disk | Network | System Log | AP Log | Security Log

(CPU/Memory/Disk/Network) using psutil
Using the systemd journalctl;If not, reading logs in the the /var/log directory.

Installation:
  pip install psutil

Execute:
  sudo python3 vm_daily_collector_linux.py
  (Reading Security/auth require root user or join systemd-journal group)
  Appending records in the vm_daily_report.csv and it can use the cron to collect metrics everyday.

Mapping Logging in the Linux:
  System Log   -> kernel / daemon / syslog
  AP Log       -> User / AP Message
  Security Log -> auth / authpriv (sshd, sudo, su, login...)
"""

import os
import csv
import json
import shutil
import psutil
import datetime
import requests
import subprocess
from requests.auth import HTTPBasicAuth


CSV_PATH = "vm_daily_report.csv"
WINDOW_HOURS = 24  # Past of 24 hours log

FIELDS = ["日期(民國)", "CPU", "記憶體", "儲存容量",
          "通信網路使用狀況", "System Log", "AP Log", "Security Log"]

# syslog facility code: 0=kern 1=user 3=daemon 4=auth 5=syslog 9=cron 10=authpriv 16~23=local0~7
_SECURITY_FACILITIES = {"4", "10"}
_SYSTEM_FACILITIES = {"0", "3", "5", "9"}
_AUTH_IDENTIFIERS = {"sshd", "sshd-session", "sudo", "su", "login",
                     "systemd-logind", "polkitd", "gdm-password"}

# systemd is not available for mapping traditional logs
_FILE_SOURCES = {
    "System Log": ["/var/log/syslog", "/var/log/messages"],
    "Security Log": ["/var/log/auth.log", "/var/log/secure"],
    "AP Log": ["/var/log/syslog", "/var/log/messages"],
}


# ------------------------------------------------------------------ Basic Utility
def roc_date(now=None):
    now = now or datetime.datetime.now()

    return f"{now.year - 1911}/{now.month:02d}/{now.day:02d}"


def collect_metrics():
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    gb = 1024 ** 3
    return {
        "CPU": f"{cpu:.1f}%",
        "記憶體": f"{mem.percent:.1f}% ({mem.used/gb:.1f}/{mem.total/gb:.1f} GB)",
        "儲存容量": f"{disk.percent:.1f}% ({disk.used/gb:.0f}/{disk.total/gb:.0f} GB)",
        "通信網路使用狀況": f"收 {net.bytes_recv/gb:.2f} GB / 送 {net.bytes_sent/gb:.2f} GB",
    }


# ------------------------------------------------------------ journalctl main path
def _load_journal(hours):
    if shutil.which("journalctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["journalctl", "--since", f"{hours} hours ago",
             "-o", "json", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    entries = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _facility(entry):
    fac = entry.get("SYSLOG_FACILITY")
    return str(fac) if fac is not None else None


def _category(entry):
    fac = _facility(entry)
    ident = entry.get("SYSLOG_IDENTIFIER", "")
    transport = entry.get("_TRANSPORT", "")
    if fac in _SECURITY_FACILITIES or ident in _AUTH_IDENTIFIERS:
        return "Security Log"
    if transport == "kernel" or fac in _SYSTEM_FACILITIES:
        return "System Log"
    return "AP Log"


def _severity(entry):
    try:
        p = int(entry.get("PRIORITY"))
    except (TypeError, ValueError):
        return None
    if p <= 3:
        return "error"
    if p == 4:
        return "warning"
    return None


def collect_logs_journal(entries):
    buckets = {k: {"total": 0, "error": 0, "warning": 0}
               for k in ("System Log", "AP Log", "Security Log")}
    for e in entries:
        cat = _category(e)
        buckets[cat]["total"] += 1
        sev = _severity(e)
        if sev:
            buckets[cat][sev] += 1
    return {k: f"共 {v['total']} (錯誤 {v['error']} / 警告 {v['warning']})"
            for k, v in buckets.items()}


# --------------------------------------------------------- Traditional fallback file path
def _count_file(path, hours):
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=hours)
    # e.g. 'Jul 30' or'Jul  3') ISO prefix (e.g. '2026-07-30')
    trad_prefix = now.strftime("%b %d").replace(" 0", "  ")  # 空格補位日
    iso_prefix = now.strftime("%Y-%m-%d")
    total = error = warning = 0
    try:
        with open(path, "r", errors="ignore") as f:
            for line in f:
                if not (line.startswith(iso_prefix) or line[:6] == trad_prefix):
                    continue
                total += 1
                low = line.lower()
                if any(w in low for w in ("error", "fail", "critical", "fatal")):
                    error += 1
                elif "warn" in low:
                    warning += 1
    except PermissionError:
        return f"無權限讀取 {path} (請用 sudo)"
    return f"共 {total} (錯誤 {error} / 警告 {warning})"


def collect_logs_files(hours):
    result = {}
    for col, candidates in _FILE_SOURCES.items():
        path = next((p for p in candidates if os.path.exists(p)), None)
        result[col] = _count_file(path, hours) if path else "N/A (找不到日誌檔)"
    return result


# ------------------------------------------------------------------------ Main Process
def collect_logs(hours=WINDOW_HOURS):
    entries = _load_journal(hours)
    if entries is not None:
        return collect_logs_journal(entries)
    return collect_logs_files(hours)


def build_row():
    row = {"日期(民國)": roc_date()}
    row.update(collect_metrics())
    row.update(collect_logs())
    return row


def append_csv(row, path=CSV_PATH):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)

def write_opensearch_log(row):
    base = os.getenv('BASE', 'https://10.41.0.227:9200')
    username = os.getenv('USER', 'admin')
    password = os.getenv('PASS', 'admin')
    AUTH = HTTPBasicAuth(username, password)
    response = requests.post(
        f'{BASE}/app-logs/_doc',
        json={'level': 'INFO', 'message': json.dumps(row)},
        auth=AUTH,
        verify=False
    )

    return response


if __name__ == "__main__":
    r = build_row()
    write_opensearch_log(r)
    append_csv(r)

    print("Collecting one metric:")
    for k in FIELDS:
        print(f"  {k:20s}: {r[k]}")
    print(f"\nWrite: {os.path.abspath(CSV_PATH)}")
