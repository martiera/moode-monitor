import subprocess
import re
from pathlib import Path
import glob
import time
import yaml
import paho.mqtt.client as mqtt

# Configuration file
CONFIG_PATH = 'moode_config.yaml'

class MQTTHandler:
    def __init__(self, config):
        """Initialize MQTT client"""
        self.config = config
        
        # MQTT Configuration
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_publish = self.on_publish
        
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

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """Connection callback"""
        try:
            # Extract the actual return code as an integer
            #rc_value = int(rc)
            rc_value = rc.value if hasattr(rc, 'value') else rc
            
            connection_messages = {
                0: "Connected to MQTT Broker successfully",
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorized"
            }
            
            message = connection_messages.get(rc_value, f"Unknown connection error: {rc_value}")
            print(message)
            
            self.connected = (rc_value == 0)
            return self.connected
        except Exception as e:
            print(f"Error in on_connect handler: {e}")
            return False

    def on_publish(self, client, userdata, mid, rc=None, properties=None):
        """Publish status callback (optional)"""
        pass

    def publish_state(self, source_topic, source, details_topic, details):
        """Publish audio state to MQTT topics"""
        try:
            # Publish source
            self.client.publish(source_topic, str(source), qos=1, retain=True)
            
            # Publish details
            self.client.publish(details_topic, str(details), qos=1, retain=True)
        except Exception as e:
            print(f"MQTT Publish Error: {e}")

def load_config():
    """Load configuration from YAML file"""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Config file not found at {CONFIG_PATH}. Using default settings.")
        return {}
    except yaml.YAMLError as e:
        print(f"Error parsing config file: {e}")
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
        return "Unknown song"
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

def get_radio_info():
    """Get radio stream information"""
    # Implement based on how Moode stores radio stream info
    return "Radio stream info not implemented"

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
        state.current_source = "Radio"
        state.current_details = get_radio_info()
    else:
        state.current_source = "Unknown"
        state.current_details = f"PID: {pid}"

    return state

def main():
    # Load configuration
    config = load_config()
    
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
                        current_state.current_source,
                        config['details_topic'], 
                        current_state.current_details
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
