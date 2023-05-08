import os
import json
import datetime
import psycopg2
import requests


print('Please ensure creating the required device tables!')


device_nickname_mapping = {}
postgres_auth = {}
api_setting_path = './uuid_3egreen_panasonic_realtime_url.txt'
uuid_path = './uuid_panasonic_realtime.txt'
postgresql_auth_path = './panasonic_postgres_auth.txt'

sensor_data_dir = './panasonic_sensor_demo_data'

if os.path.isfile(postgresql_auth_path) is False:
    print('The PostgreSQL auth path is not found.')
    exit(1)

if os.path.isfile(uuid_path) is False:
    print('The uuid device file is not found.')
    exit(1)

if os.path.isfile(api_setting_path) is False:
    print('The API setting file is not found.')
    exit(1)

handler = open(postgresql_auth_path, 'r')
contents = handler.readlines()
handler.close()

if len(contents) != 5:
    print('The ' + postgresql_auth_path + ' format is invalid.')
    exit(1)

postgres_auth['db_host'] = contents[0][0:-1]
postgres_auth['db_user'] = contents[1][0:-1]
postgres_auth['db_password'] = contents[2][0:-1]
postgres_auth['db_name'] = contents[3][0:-1]
postgres_auth['db_port'] = contents[4][0:-1]

handler = open(uuid_path, 'r')
uuid_contents = handler.readlines()
handler.close()

if len(uuid_contents) % 2 != 0:
    print('The' + uuid_path + ' format is invalid.')
    exit(1)

handler = open(api_setting_path, 'r')
contents = handler.readlines()
handler.close()


if len(contents) != 1:
    print('The ' + api_setting_path + ' format is invalid.')
    exit(1)

index = 0
while index < (len(uuid_contents) - 1):
    if uuid_contents[index][-1] == '\n':
        uuid_contents[index] = uuid_contents[index][0:-1]
    if uuid_contents[index+1][-1] == '\n':
        uuid_contents[index+1] = uuid_contents[index+1][0:-1] 

    device_nickname_mapping[uuid_contents[index]] = uuid_contents[index+1]

    index += 2


realtime_api_url = contents[0][0:-1]
response = requests.get(realtime_api_url)

try:
    connection = psycopg2.connect(
        database=postgres_auth['db_name'],
        user=postgres_auth['db_user'],
        password=postgres_auth['db_password'],
        host=postgres_auth['db_host'],
        port=postgres_auth['db_port']
    )
except Exception as e:
    print(str(e))
    exit(1)

cursor = connection.cursor()

records = response.json()
sensor_sql = '''
    INSERT INTO table_name (current, temperature, watts, measured_datetime)
    VALUES(%s, %s, %s, %s)
'''
for record in records:
    table_name = device_nickname_mapping[record['address']]
    current = record['current']
    temperature = record['temperature']
    watts = record['watts']
    last_updated = record['lastUpdated']
    measured_datetime = datetime.datetime.strptime(last_updated, '%Y-%m-%d, %H:%M:%S')

    prepared_args = (
        current, temperature, watts, measured_datetime,
    )

    cursor.execute(sensor_sql, prepared_args)

try:
    connection.commit()
except Exception as e:
    print('Error happended when inserting')
    print(str(e))
    cursor.close()
    connection.close()
    exit(1)


cursor.close()
connection.close()
