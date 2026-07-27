#!/bin/bash

set -a
source ./3e_green_mqtt.env

set +a
exec venv/bin/python3 3e_green_mqtt_publisher.py
