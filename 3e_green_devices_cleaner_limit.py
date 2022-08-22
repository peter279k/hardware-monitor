import os
import sys
import glob
import datetime


archived_path = './archived/'
today_date = datetime.datetime.now()
if os.path.isdir(archived_path) is False:
    print(archived_path + ' directory is not existed. Try to create that...')
    os.mkdir(archived_path)

csv_file_paths = glob.glob(archived_path + '*.csv')
csv_file_paths.extend(glob.glob('./*.csv'))
counter = 0
for csv_file_path in csv_file_paths:
    file_date = datetime.datetime.fromtimestamp(int(csv_file_path.split('_')[1][0:-4]) / 1000)
    diff_days = (today_date - file_date).days
    if diff_days > 7:
        os.remove(csv_file_path)
        print(csv_file_path + ' is removed.')
        counter += 1
    if counter == 100:
        break

print('Cleaner has been done. (%s)' % counter)

