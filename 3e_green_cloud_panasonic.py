import os
import json
import requests


api_setting_path = './uuid_3egreen_panasonic_url.txt'
uuid_path = './uuid_3egreen_panasonic.txt'
headers = {'Content-Type': 'application/json'}

date_range = '2023-04-18_2023-04-27'

sensor_data_dir = './panasonic_sensor_demo_data'

if os.path.isdir(sensor_data_dir) is False:
    os.mkdir(sensor_data_dir)

if os.path.isfile(api_setting_path) is False:
    print('The API setting file is not found.')
    exit(1)

if os.path.isfile(uuid_path) is False:
    print('The UUID file is not found.')
    exit(1)

handler = open(api_setting_path, 'r')
contents = handler.readlines()
handler.close()

if len(contents) != 1:
    print('The ' + api_setting_path + ' format is invalid.')
    exit(1)

history_api_url = contents[0][0:-1]

handler = open(uuid_path, 'r')
contents = handler.readlines()
handler.close()

for uuid_mac_address in contents:
    try:
        post_data = {
            'macaddress': uuid_mac_address[0:-1],
            'date': date_range,
        }
        post_data = json.dumps(post_data)
        response = requests.post(history_api_url, data=post_data, headers=headers, timeout=10)

    except Exception as e:
        print(str(e))
        continue


    sensor_path = sensor_data_dir + '/' + uuid_mac_address[0:-1] + '_' + date_range
    handler = open(sensor_path, 'w')
    handler.write(response.text)
    handler.close()

    print(sensor_path + ' is saved!')
