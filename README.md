# Moode Monitor

Moode Monitor is a Python-based service that monitors audio playback on a [Moode Audio Player](https://moodeaudio.org/) and publishes the current audio state to an MQTT broker. This allows integration with home automation systems like Home Assistant to display the current audio source and details (e.g., song title).

## Features

- Monitors audio playback state on Moode Audio.
- Publishes audio state to MQTT topics.
- Supports Spotify, AirPlay, and MPD (Music Player Daemon).
- Configurable via a YAML configuration file.
- Automatically restarts on failure.


## Requirements
- Python 3
- Systemd (for running as a daemon)
- Moode Audio Release 8.3.9 (32-bit Bullseye)

## Installation

1. Install Git if you do not have it:
```bash
sudo apt-get install git -y
```

2. Clone the repository:
```bash
git clone https://github.com/martiera/moode-monitor.git
cd moode-monitor
```

3. Edit the moode_config.yaml file to configure your MQTT broker and topics:
```yaml
# MQTT Broker Configuration
mqtt_server: 192.168.0.100
mqtt_port: 1883
mqtt_username: username
mqtt_password: password

# MQTT Topics
source_topic: moode/audio/source
details_topic: moode/audio/details
command_topic: moode/audio/command

# Optional additional configuration
debug: false
```

4. Run the install.sh script to set up the service:
```bash
sudo ./install.sh
```

## Usage

The service will automatically start and monitor the audio playback state. It will publish the current audio source and details to the configured MQTT topics. The service logs its output to `/var/log/moode_monitor.log`.


## MQTT Topics

- `moode/audio/source`: The current audio source (e.g., Spotify, AirPlay, Radio).
- `moode/audio/details`: The current audio details (e.g., song title, artist).

## Home Assistant Integration

You can use the MQTT topics in Home Assistant to display the current audio source and details. Here is an example configuration:
```yaml
mqtt:
  - sensor:
    - name: "Moode Audio Source"
      state_topic: "moode/audio/source"
    - name: "Moode Audio Details"
      state_topic: "moode/audio/details"
```

## Development and Testing

This project was developed and tested on Moode Audio Release 8.3.9 (32-bit Bullseye).

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request on GitHub.

## Support

If you encounter any issues or have questions, please open an issue on GitHub.

## Copyright

© 2025 Martins Ierags
