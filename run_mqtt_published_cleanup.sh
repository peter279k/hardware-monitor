#!/bin/bash
# 3e Green sensor_data.db 清理(供 cron 執行)
set -euo pipefail
cd /home/localadmin/hardware-monitor
set -a; source ./edge_ai.env; set +a
exec /usr/bin/flock -n /tmp/3e_green_cleanup.lock \
     nice -n 15 ./venv/bin/python 3e_green_mqtt_published_cleanup.py
