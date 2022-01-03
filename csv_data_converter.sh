#!/bin/bash

green_color='\e[0;32m'
red_color='\e[0;31m'
yellow_color='\e[0;33m'
rest_color='\e[0m'

echo -e $green_color"Converting text to CSV format has been started!"$rest_color

verbose=$1

if [[ $verbose == "--debug" ]]; then
    echo -e $yellow_color"Enabling the debug mode."$rest_color
    echo ""
fi;

if [[ ! -d "$PWD/converted/" ]]; then
    mkdir "$PWD/converted/"
fi;

ls *.txt > /dev/null 2>&1

if [[ $? != 0 ]]; then
    echo -e $red_color"There're no text files on the $PWD directory."$rest_color
    exit 1;
fi;


for text_file_name in $(ls *.txt)
do
    measured_contents=$(cat $text_file_name)
    measured_contents=$(echo $measured_contents | sed -e "s/ /,/g")
    measured_8601_datetime=$(echo $text_file_name | awk '{split($1,a,"."); print a[1]}')
    measured_3339_datetime=$(date --date="$measured_8601_datetime" --rfc-3339=seconds --utc)
    measured_3339_datetime=$(echo $measured_3339_datetime | sed -e "s/+00:00//g")
    measured_contents="$measured_contents,$measured_3339_datetime"
    if [[ $verbose == "--debug" ]]; then
        echo "$measured_contents"
    fi;
    echo "$measured_contents" > "$PWD/converted/$measured_8601_datetime.csv"
done;

echo ""
echo -e $yellow_color"All converted files will be saved in $PWD/converted directory!"$rest_color
echo -e $green_color"Converting text to CSV format has been done!"$rest_color
