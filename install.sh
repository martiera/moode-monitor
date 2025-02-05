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

# Check if python3-venv is installed
if ! dpkg -l | grep -q python3-venv; then
    echo "python3-venv is not installed. Installing it now..."
    sudo apt-get install -y python3-venv
fi

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
VENV_PATH="${REPO_PATH}/.venv"

# Check if the virtual environment already exists
if [ -d "${VENV_PATH}" ]; then
    echo "Virtual environment already exists at ${VENV_PATH}. Activating it..."
else
    # Create a virtual environment as the original user
    echo "Creating virtual environment at ${VENV_PATH}..."
    sudo -u $SUDO_USER python3 -m venv ${VENV_PATH}
fi

# Activate the virtual environment and install dependencies as the original user
sudo -u $SUDO_USER bash -c "source ${VENV_PATH}/bin/activate && pip install -r ${REPO_PATH}/requirements.txt && deactivate"

# Create the systemd service file
echo "Creating systemd service file at ${SERVICE_FILE}..."

cat > ${SERVICE_FILE} <<EOL
[Unit]
Description=Moode Monitor Service
After=network.target

[Service]
ExecStart=${VENV_PATH}/bin/python ${REPO_PATH}/moode_monitor.py
WorkingDirectory=${REPO_PATH}
Restart=always
RestartSec=5
User=root
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

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
