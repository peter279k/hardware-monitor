#!/bin/bash

echo "Monitoring CPU temperature and frequency is starting..."
echo "-------------------------------------------------------"

cpu_temp=$(head -n 1 /sys/class/thermal/thermal_zone0/temp)
cpu_temp=$(echo "scale=2; $cpu_temp / 1000" | bc -l)

cpu_frequency=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq)
cpu_frequency=$(echo "scale=2; $cpu_frequency / 1000" | bc -l)

echo "Monitoring Memory and swap usage is starting..."
echo "-------------------------------------------------------"

smem=$(free | grep Mem | awk '{print $3/$2*100}')
smem=$(echo "scale=2; $smem / 1.0" | bc -l)

check_swap=$(free | grep Swap | awk '{print $2}')
sswap=0

if [[ $check_swap != 0 ]]; then
    sswap=$(free | grep Swap | awk '{print $3/$2*100}')
    sswap=$(echo "scale=2; $sswap / 1.0" | bc -l)
fi;

echo "Monitoring disk usage is starting..."
echo "-------------------------------------------------------"

disk_usage=$(df -lh / | tail -n 1 | awk '{print $5}' | sed -e "s/%//g")
file_name="$(date --iso-8601=seconds).txt"

echo "$cpu_temp" >> $file_name
echo "$cpu_frequency" >> $file_name
echo "$smem" >> $file_name
echo "$sswap" >> $file_name
echo "$disk_usage" >> $file_name
