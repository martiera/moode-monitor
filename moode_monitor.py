import subprocess
import re
from pathlib import Path
import glob
import time
import yaml
import paho.mqtt.client as mqtt

# Configuration file
CONFIG_PATH = 'moode_config.yaml'
config = {}

class MQTTHandler:
    def __init__(self, config):
        """Initialize MQTT client"""
        self.config = config
        
        # MQTT Configuration
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_publish = self.on_publish
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        
        # Add authentication if required
        username = config.get('mqtt_username')
        password = config.get('mqtt_password')
        if username and password:
            self.client.username_pw_set(username, password)
        
        # Connect to MQTT broker
        try:
            self.client.connect(
                config.get('mqtt_server', 'localhost'), 
                config.get('mqtt_port', 1883)
            )
            self.client.loop_start()
        except Exception as e:
            print(f"MQTT Connection Error: {e}")

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            debug_print(f"Failed to connect: {reason_code}. Will retry connection...")
        else:
            self.client.subscribe(self.config.get('command_topic'))

    def on_publish(self, client, userdata, mid, rc=None, properties=None):
        """Publish status callback (optional)"""
        pass
    
    def on_message(self, client, userdata, message):
        """Message status callback (optional)"""
        pass

    def on_subscribe(self, client, userdata, mid, reason_code_list, properties):
        if reason_code_list[0].is_failure:
            debug_print(f"Broker rejected you subscription: {reason_code_list[0]}")
        else:
            debug_print(f"Broker granted the following QoS: {reason_code_list[0].value}")

    def publish_state(self, source_topic, source, details_topic, details):
        """Publish audio state to MQTT topics"""
        try:
            # Publish source
            self.client.publish(source_topic, str(source), qos=1, retain=False)
            # Publish details
            self.client.publish(details_topic, str(details), qos=1, retain=False)
        except Exception as e:
            debug_print(f"MQTT Publish Error: {e}")

def load_config():
    """Load configuration from YAML file"""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        debug_print(f"Config file not found at {CONFIG_PATH}. Using default settings.")
        return {}
    except yaml.YAMLError as e:
        debug_print(f"Error parsing config file: {e}")
        return {}

class AudioState:
    def __init__(self):
        self.current_pid = None
        self.current_source = None
        self.current_details = None
        self.spotify_pending_song = None

    def __eq__(self, other):
        if not isinstance(other, AudioState):
            return False
        return (self.current_pid == other.current_pid and
                self.current_source == other.current_source and
                self.current_details == other.current_details)

    def __str__(self):
        if not self.current_source:
            return "No active playback"
        return f"Source: {self.current_source}\nDetails: {self.current_details}"

def get_card_status():
    """Get the audio card status from proc filesystem"""
    try:
        status_files = glob.glob('/proc/asound/card*/pcm*/sub*/status')
        for status_file in status_files:
            with open(status_file, 'r') as f:
                content = f.read()
                if 'state: RUNNING' in content:
                    pid_match = re.search(r'owner_pid\s+:\s+(\d+)', content)
                    if pid_match:
                        return pid_match.group(1)
    except Exception as e:
        print(f"Error reading card status: {e}")
    return None

def get_process_cmdline(pid):
    """Get command line for given PID"""
    try:
        with open(f'/proc/{pid}/cmdline', 'r') as f:
            return f.read()
    except Exception as e:
        return None

def get_spotify_status():
    """Get current Spotify song from librespot log with proper confirmation"""
    try:
        with open('/var/log/moode_librespot.log', 'r') as f:
            lines = f.readlines()
            pending_song = None
            playing_song = False
            
            # Read lines in reverse to get most recent events
            for line in reversed(lines):
                # Check for Load command first (confirms actual playback)
                if 'kPlayStatusPlay' in line:
                    playing_song = True
                    continue
                
                # Store potential song but don't confirm it yet
                if playing_song and 'Loading <' in line:
                    match = re.search(r'Loading <(.+?)> with', line)
                    if match and not pending_song:
                        pending_song = match.group(1)
                        return pending_song
                        
    except Exception as e:
        return None
    return None

def get_airplay_device():
    """Get AirPlay device from shairport-sync log"""
    try:
        with open('/var/log/moode_shairport-sync.log', 'r') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if 'connection from' in line:
                    match = re.search(r'\("([^"]+)"\)', line)
                    if match:
                        return match.group(1)
    except Exception as e:
        return "Unknown device"

def format_radio_name(url):
    url = url.split('/')[2].split(':')[0]
    # Replace dot with spaces
    url = url.replace('.', ' ')
    # Remove keywords = live, stream
    url = re.sub(r'\b(live|stream)\b', '', url)
    # Convert to upper case
    url = url.upper()
    return url

def format_radio_details(details):
    # With regex remove positive or negative number after last star. Examles: "LINK * 1002264", "SPOT * 100% Grandi Successi * -1"
    details = re.sub(r'\s*\*\s*[-+]?\d+$', '', details)
    return details

def get_radio_info():
    try:
        result = subprocess.run(['mpc', 'status'], capture_output=True, text=True)
        output = result.stdout.strip().split('\n')

        if len(output) < 2:
            return None, None

        if output[0].startswith('http'):
            source = format_radio_name(output[0])
            details = None
        elif len(output[0].split(':87')) > 1:
            source = output[0].split(':')[0]
            details = format_radio_details(output[0].split(':')[1].strip())
        else:
            source_result = subprocess.run(['mpc', 'current', '--format', '%file%'], capture_output=True, text=True)
            source_output = source_result.stdout.strip()
            source = format_radio_name(source_output) if source_output.startswith('http') else None
            details = format_radio_details(output[0])
        return source, details
    except Exception as e:
        debug_print(f"Error getting radio info: {e}")
        return "Music", None

def get_current_state():
    """Get the current audio state"""
    state = AudioState()
    
    pid = get_card_status()
    if not pid:
        return state

    state.current_pid = pid
    cmdline = get_process_cmdline(pid)
    if not cmdline:
        return state

    if 'librespot' in cmdline:
        state.current_source = "Spotify"
        spotify_status = get_spotify_status()
        if spotify_status:
            state.current_details = spotify_status
    elif 'shairport-sync' in cmdline:
        state.current_source = "AirPlay"
        state.current_details = get_airplay_device()
    elif 'mpd' in cmdline:
        state.current_source, state.current_details = get_radio_info()
    else:
        state.current_source = "Unknown"
        state.current_details = f"PID: {pid}"

    return state

def debug_print(message):
    global config
    if config.get('debug', False):
        print(message)

def mpc_maintenance():
    result = subprocess.run(['mpc', 'update'], capture_output=True, text=True)

def main():
    # Load configuration
    global config
    config = load_config()
    
    mpc_maintenance()
    
    # Initialize MQTT Handler
    mqtt_handler = MQTTHandler(config)
    
    print("Monitoring audio playback state...")
    previous_state = AudioState()
    
    while True:
        try:
            current_state = get_current_state()
            
            # Only print and publish if the state has changed
            if current_state != previous_state:
                print("\n" + "="*50)
                print(current_state)
                print("="*50)
                
                # Publish to MQTT if topics are configured
                if config.get('source_topic') and config.get('details_topic'):
                    mqtt_handler.publish_state(
                        config['source_topic'], 
                        current_state.current_source if current_state.current_source else "",
                        config['details_topic'], 
                        current_state.current_details if current_state.current_details else ""
                    )
                
                previous_state = current_state
            
            # Wait before checking again
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
