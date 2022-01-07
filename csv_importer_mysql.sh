#!/bin/bash

green_color='\e[0;32m'
red_color='\e[0;31m'
yellow_color='\e[0;33m'
rest_color='\e[0m'

echo -e $green_color"Data importer has been started!"$rest_color
echo -e $yellow_color"Checking mysql command is available"$rest_color
echo ""

which mysql 2>&1 > /dev/null

if [[ $? != 0 ]]; then
    echo -e $red_color"mysql command not found"$rest_color
    exit 1;
fi;

if [[ ! -f ./mysql_auth.txt ]]; then
    echo -e $red_color"Please specify ./mysql_auth.txt file to create MySQL DB authentication"$rest_color
    exit 1;
fi;

mysql_auth=$(cat ./mysql_auth.txt)

ip_address=$(echo $mysql_auth | awk '{print $1}')
user_name=$(echo $mysql_auth | awk '{print $2}')
port_number=$(echo $mysql_auth | awk '{print $3}')
password=$(echo $mysql_auth | awk '{print $4}')
db_name=$(echo $mysql_auth | awk '{print $5}')
table_name=$(echo $mysql_auth | awk '{print $6}')

for csv_file_name in $(ls ./converted/*.csv)
do
    insert_cmd="INSERT INTO $db_name.$table_name FORMAT CSV $(cat $csv_file_name)"
    mysql -h "$ip_address" -u "$user_name" -P "$port_number" -p"$password" -e "$insert_cmd"
    if [[ $? != 0 ]]; then
        echo -e $red_color"The $csv_file_name is failed to import."$rest_color
    fi;
done;

echo -e $green_color"Data importer has been done!"$rest_color
