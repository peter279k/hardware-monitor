import os
import sys
import glob
from clickhouse_driver import Client


archived_path = './archived'
if os.path.isdir(archived_path) is False:
    os.mkdir(archived_path)
    print(archived_path + ' has been created.')


mysql_auth_path = './mysql_auth.txt'
if os.path.isfile(mysql_auth_path) is False:
    print(mysql_auth_path + ' is not existed. Exited.')
    sys.exit(1)

file_handler = open(mysql_auth_path, 'r')
file_contents = file_handler.readlines()
file_handler.close()

db_ip_address = file_contents[0][0:-1]
db_username = file_contents[1][0:-1]
db_password = file_contents[3][0:-1]
database_name = file_contents[4][0:-1]

measured_files = glob.glob('./*_*.csv')
client = Client(host=db_ip_address, user=db_username, password=db_password, database=database_name)

for measured_file in measured_files:
    file_handler = open(measured_file, 'r')
    csv_string = file_handler.readlines()[0][0:-1]
    file_handler.close()
    table_name = measured_file.split('_')[0][2:]
    sql = 'INSERT INTO %s.%s FORMAT CSV %s' % (database_name, table_name, csv_string)
    client.execute(sql)

    print('Insert %s has been done.' % (csv_string))

    os.rename(measured_file, './archived/' + measured_file)

    print('Move %s to archived folder' % (measured_file))
