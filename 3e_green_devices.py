import os
import sys
import json
import requests
from datetime import datetime


def convert_timestamp(timestamp):
    date_time = datetime.fromtimestamp(timestamp / 1000)

    return date_time.strftime('%Y-%m-%d %H:%M:%S')


uuid_path = './uuid.txt'
if os.path.isfile(uuid_path) is False:
    print(uuid_path + ' is not existed.')
    sys.exit(1)

handler = open(uuid_path, 'r')
file_contents = handler.readlines()

index = 0
for file_content in file_contents:
    file_contents[index] = file_content[0:-1]
    index += 1

handler.close()

json_lists = requests.get('http://127.0.0.1:9100/list')
json_lists = json_lists.json()

output_csv = './%s_%s.csv'
for json_list in json_lists:
    if json_list['uuid'] in file_contents:
        csv_file = output_csv % (json_list['uuid'], json_list['Time'])
        if os.path.isfile(csv_file) is True:
            print(csv_file + ' is existed. Skipped.')
            continue
        handler = open(csv_file, 'w')
        string = str(json_list['Current']) + ',' + str(json_list['Batt']) + ',' + str(json_list['Temp']) + ',' + str(convert_timestamp(json_list['Time'])) + '\n'
        handler.write(string)
        handler.close()
        print(csv_file + ' has been stored.')
