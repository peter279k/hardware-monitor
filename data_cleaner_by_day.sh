#!/bin/bash

green_color='\e[0;32m'
red_color='\e[0;31m'
yellow_color='\e[0;33m'
rest_color='\e[0m'

echo -e $green_color"TEXT file cleaner progress has been started!"$rest_color
echo ""

yesterday_date="$(date --utc --date="yesterday" +%F)"
rm -f $(ls | grep "$yesterday_date")

echo -e $green_color"TEXT file cleaner progress has been done!"$rest_color
