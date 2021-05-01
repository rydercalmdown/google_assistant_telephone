#!/bin/bash
# install.sh

cd ../
sudo apt-get update
sudo apt-get install -y \
        python3-pip \
        libffi-dev \
        libssl-dev \
        portaudio19-dev
python3 -m pip install virtualenv
python3 -m virtualenv -p python3 env
. env/bin/activate

# https://github.com/googlesamples/assistant-sdk-python/issues/267
pip install --upgrade pip
pip install --upgrade --no-binary :all: grpcio

pip install -r src/requirements.txt

# googlesamples-assistant-pushtotalk --project-id $PROJECT_ID --device-model-id $DEVICE_ID
