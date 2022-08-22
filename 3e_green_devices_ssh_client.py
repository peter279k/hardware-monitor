import os
import sys
import glob
from scp import SCPClient
from paramiko import SSHClient
from paramiko.client import AutoAddPolicy
from paramiko.ssh_exception import SSHException


ssh_auth_path = './ssh_auth.txt'
if os.path.isfile(ssh_auth_path) is False:
    print(ssh_auth_path + ' is not existed. Exited.')
    sys.exit(1)

file_handler = open(ssh_auth_path, 'r')
file_contents = file_handler.readlines()
file_handler.close()

ssh_host_address = file_contents[0][0:-1]
ssh_username = file_contents[1][0:-1]
ssh_port = file_contents[2][0:-1]
ssh_password = file_contents[3][0:-1]
remote_path = file_contents[4][0:-1]

measured_files = glob.glob('./*_*.csv')
ssh = SSHClient()
ssh.set_missing_host_key_policy(AutoAddPolicy)
try:
    ssh.connect(
        hostname=ssh_host_address,
        port=ssh_port,
        username=ssh_username,
        password=ssh_password,
    )
except SSHException as e:
    print(e)
    print('Connect the SSH server is failed. (Maybe the Internet is not connected.)')
    sys.exit(1)


scp = SCPClient(ssh.get_transport())
for measured_file in measured_files:
    scp.put(measured_file, recursive=True, remote_path=remote_path)
    print('Insert the %s has been done.' % (measured_file))

    os.rename(measured_file, './archived/' + measured_file)

    print('Move the %s to archived folder' % (measured_file))


scp.close()
ssh.close()
