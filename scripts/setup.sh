#!/bin/bash

# First we will check to make sure your docker-machine 'default' is running
echo "Starting default docker machine..."
docker-machine start default > /dev/null 2>&1
if [ "$?" != "0" ]; then
  echo "Error starting the 'default' docker machine, please make sure it has been created"
  exit 1
fi

# Next we will set up our environment
echo "Initializing environment..."
eval "$(docker-machine env default)"

# Spin up system using docker compose
docker-compose up
