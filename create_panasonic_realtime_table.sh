#!/bin/bash

if [[ $db_user == "" ]]; then
    echo "Please set db_user env variable."
    exit 1;
fi;

if [[ $db_host == "" ]]; then
    echo "Please set db_host env variable."
    exit 1;
fi;

if [[ $db_name == "" ]]; then
    echo "Please set db_name env variable."
    exit 1;
fi;

if [[ $PGPASSWORD == "" ]]; then
    echo "Please set PGPASSWORD env variable"
    exit 1;
fi;

if [[ ! -f uuid_panasonic_realtime.txt ]]; then
    echo 'uuid_panasonic_realtime.txt file is not found.'
    exit 1;
fi;

which psql > /dev/null 2>&1

if [[ $? != 0 ]]; then
    echo "psql command not found."
    exit 1;
fi;

index=0
for device_info in $(cat uuid_panasonic_realtime.txt)
do
    if [[ $(( $index % 2 )) == 0 ]]; then
        index=$((index +1 ))
        continue
    fi;

    echo $device_info
    cp 3e_green_cloud_panasonic.sql temp.sql
    sed -i "s/table_name/$device_info/g" temp.sql

    psql -U "$db_user" --host="$db_host" --dbname="$db_name" -f "temp.sql"

    if [[ $? == 0 ]]; then
        echo "Processing the $device_info is done."
    else
        echo "Processing the $device_info is failed."
    fi;

    index=$((index +1 ))
done;


rm -f temp.sql
