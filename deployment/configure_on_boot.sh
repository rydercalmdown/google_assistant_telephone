#!/bin/bash
# configure_on_boot.sh
# Configures the script to run on boot


sudo sed -i.bak '$ i\cd /home/pi/google_assistant_telephone && make run &' /etc/rc.local

echo "New File: ================"
cat /etc/rc.local

echo "Backup /etc/rc.local: ==============="
cat /etc/rc.local.bak
