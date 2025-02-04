#!/bin/bash

# Ensure the script is run with root privileges
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python3 is not installed. Please install it and try again."
    exit 1
fi

# Install python dependencies
pip3 install -r requirements.txt

# Check if systemd is available
if ! pidof systemd &> /dev/null; then
    echo "Systemd is not running. This script requires systemd."
    exit 1
fi

# Get the directory where the script is located
REPO_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="moode_monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_FILE="/var/log/${SERVICE_NAME}.log"

# Create the systemd service file
echo "Creating systemd service file at ${SERVICE_FILE}..."

cat > ${SERVICE_FILE} <<EOL
[Unit]
Description=Moode Monitor Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 ${REPO_PATH}/moode_monitor.py >> ${LOG_FILE} 2>&1
WorkingDirectory=${REPO_PATH}
Restart=always
RestartSec=5
User=$(whoami)

[Install]
WantedBy=multi-user.target
EOL

if [ $? -ne 0 ]; then
    echo "Failed to create systemd service file."
    exit 1
fi

# Reload systemd to apply the new service
echo "Reloading systemd daemon..."
systemctl daemon-reload

if [ $? -ne 0 ]; then
    echo "Failed to reload systemd daemon."
    exit 1
fi

# Enable the service to start on boot
echo "Enabling ${SERVICE_NAME} service..."
systemctl enable ${SERVICE_NAME}

if [ $? -ne 0 ]; then
    echo "Failed to enable ${SERVICE_NAME} service."
    exit 1
fi

# Start the service
echo "Starting ${SERVICE_NAME} service..."
systemctl start ${SERVICE_NAME}

if [ $? -ne 0 ]; then
    echo "Failed to start ${SERVICE_NAME} service."
    exit 1
fi

# Check the status of the service
systemctl status ${SERVICE_NAME} --no-pager

echo "Service ${SERVICE_NAME} has been set up and started successfully."
