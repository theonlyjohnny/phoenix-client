# Phoenix Client
## Description
Phoenix client lives on every Phoenix-managed instance, and manages the software state on the instances
## Responsibilities:
 - Pull cluster/service configuration from [Phoenix Master](https://github.com/theonlyjohnny/phoenix-master)
 - Manage the state of containers as specified in configuration
 - Manage the versions of containers as specified in configuration

## Requirements
 - No Dependencies
 - Always Running

## Components
### install_phoenix.sh
#### Description
Simple shell script to configure and install phoenix_client on the instance
#### Responsibilities
 - Install dependencies (`python3`, `docker`, etc.)
 - Take configuration parameters in and configure `phoenix_client.py` with them
 - Setup phoenix_client.service
 - Start phoenix_client service

### phoenix_client.py
### Description
Looping Python script that ensures Docker containers are in the proper state
### Responsibilities
 - Get cluster from [Phoenix Master](https://github.com/theonlyjohnny/phoenix-master)
 - Get services from cluster
 - Filter cluster service list to those not running in docker container list
 - Create docker containers according to service configuration
 - Repeat
