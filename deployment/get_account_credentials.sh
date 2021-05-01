#!/bin/bash
# get_account_credentials.sh
# makes an oauth request for the user to use their google assistant on this device

cd ../
google-oauthlib-tool \
    --client-secrets ./oauth_config_credentials.json \
    --scope https://www.googleapis.com/auth/assistant-sdk-prototype \
    --scope https://www.googleapis.com/auth/gcm \
    --save \
    --headless
