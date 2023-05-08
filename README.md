# Hardware Monitor for Linux-like operating system

## Usage

- Download the `monitor.sh`.
- Running the `chmod 755 ./monitor.sh` command to make this Bash script executable.
- Running the `./monitor.sh` or setting Cron job with specific time to execute this Bash script.
- Done.

## Data converter

- Download the `csv_data_converter.sh` to convert all text to CSV files in current directory.
- Download the `csv_data_converter_by_day.sh` to convert yesterday text to CSV files in current directory.
- All of the above Bash scripts will let converted files be saved as `converted` directory.

## Data importer with mysql command

- WIP.
- Create the ``
- Download the `` to let all CSV files be imported

## Data cleaner

- Download the `data_cleaner_by_day.sh` to remove yesterday text file in current directory.

## 3e Green Devices

- Before running the Python programs, it should run following commands to ensure required Python modules are installed:
    - `sudo apt-get update`
    - `sudo apt-get install python3-requests`
    - `pip3 install -U -r requirements.txt`
- It should have the [GW-06 gateway](https://www.3egreen.com/product/gw06-03/).
- It should have the [detecting devices](https://www.3egreen.com/product-category/detecting/).
- Creating the `./uuid.txt` file to define detecting device UUID.

## Running the device fetcher

- Running the `python3 3e_green_devices.py` program every 1 minute with Crontab.
- The above command will store the masured result with the CSV file format.

## Running the device batch cleaner

- Running the `./batch_clean.sh` to clean the outdated data. And it can save the hardware sources.
- If the hardware sources are good enough, it can run the `python3 3e_green_devices_cleaner_by_week.py` program every `00:01:00` with Crontab.

## Running the data importer (ClickHouse client)

- `mysql_auth.txt` setting setup. It can refer the example `mysql_auth.txt.example` file.
- Running the `python3 3e_green_devices_importer.py` program every 5 minutes with Crontab.

## Running the data importer (SSH client)

- `ssh_auth.txt` setting setup. It can refer the example `ssh_auth.txt.example` file.
- Running the `python3 3e_green_devices_ssh_client.py` program every 5 minutes with Crontab.

## Fake Panasonic Demo

- Running the `create_panasonic_realtime_table.sh` shell script.
- Editing the `3e_green_cloud_panasonic_realtime.service.example` to be ``3e_green_cloud_panasonic_realtime.service` fil and setup the background service.
- Running the `3e_green_cloud_panasonic_realtime.py` Python program as the daemon program.

## References

- https://helloacm.com/bash-script-to-monitor-the-cpu-frequency-and-temperature-on-raspberry-pi
- https://www.2daygeek.com/linux-shell-script-to-monitor-cpu-memory-swap-usage-send-email
