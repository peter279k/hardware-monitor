#!/bin/bash

echo "Converting text to CSV format has been started!"

if [[ ! -d "$PWD/converted/" ]]; then
    mkdir "$PWD/converted/"
fi;

ls *.txt > /dev/null 2>&1

if [[ $? != 0 ]]; then
    echo "There're no text files on the $PWD directory."
    exit 1;
fi;


for text_file_name in $(ls *.txt)
do
    measured_contents=$(cat $text_file_name)
    measured_contents=$(echo $measured_contents | sed -e "s/ /,/g")
    measured_8601_datetime=$(echo $measured_contents | awk '{split($1,a,"."); print a[1]}')
    measured_3339_datetime=$(date --date="$measured_8601_datetime" --rfc-3339=seconds)
    measured_contents="$measured_contents,$measured_3339_datetime"
    echo "$measured_contents"
    break;
done;

echo "Converting text to CSV format has been done!"
