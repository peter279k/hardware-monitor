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
    - `pip3 install -U clickhouse-driver`
- It should have the [GW-06 gateway](https://www.3egreen.com/tw/product/gw06-03/).
- It should have the [detecting devices](https://www.3egreen.com/tw/product-category/detecting/).
- Creating the `./uuid.txt` file to define detecting device UUID.

## Run device fetcher

- Running the `python3 3e_green_devices.py` program every 1 minute with Crontab.
- Running the `python3 3e_green_devices_importer.py` program every 5 minutes with Crontab.
- Running the `python3 3e_green_devices_cleaner_by_week.py` program every `00:01:00` with Crontab.
- The above command will store the masured result with the CSV file format.

## Run data importer

## References

- https://helloacm.com/bash-script-to-monitor-the-cpu-frequency-and-temperature-on-raspberry-pi
- https://www.2daygeek.com/linux-shell-script-to-monitor-cpu-memory-swap-usage-send-email
