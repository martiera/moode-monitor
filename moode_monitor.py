import subprocess
import sys
import re
from pathlib import Path
import glob
import time
import yaml
import logging
import logging.handlers
import paho.mqtt.client as mqtt
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from collections import deque
from datetime import datetime, timedelta

# Configuration file
CONFIG_PATH = 'moode_config.yaml'
config = {}

class LogCache:
    """Cache for log file reads to reduce I/O operations"""
    def __init__(self, max_age_seconds=5):
        self.cache = {}
        self.max_age = timedelta(seconds=max_age_seconds)
    
    def get(self, key):
        """Get cached value if not expired"""
        if key in self.cache:
            entry, timestamp = self.cache[key]
            if datetime.now() - timestamp < self.max_age:
                return entry
            del self.cache[key]
        return None
    
    def set(self, key, value):
        """Cache a value with current timestamp"""
        self.cache[key] = (value, datetime.now())

class LogWatcher(FileSystemEventHandler):
    """Watch for changes in log files and trigger updates"""
    WATCHED_FILES = [
        '/var/log/moode_librespot.log',
        '/var/log/moode_shairport-sync.log'
    ]
    
    def __init__(self, callback):
        self.callback = callback
        
    def on_modified(self, event):
        for watched in self.WATCHED_FILES:
            if event.src_path == watched:
                self.callback()

class MQTTHandler:
    def __init__(self, config):
        """Initialize MQTT client"""
        self.config = config
        self.connected = False
        
        # MQTT Configuration
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_publish = self.on_publish
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.on_disconnect = self.on_disconnect
        
        # Add authentication if required
        username = config.get('mqtt_username')
        password = config.get('mqtt_password')
        if username and password:
            self.client.username_pw_set(username, password)
        
        self.handle_connection()
    
    def handle_connection(self):
        # When not connected, try to reconnect
        if not self.connected:
            try:
                self.client.connect(
                    config.get('mqtt_server', 'localhost'), 
                    config.get('mqtt_port', 1883)
                )
                self.client.loop_start()
                logging.debug("MQTT Connection Established")
            except Exception as e:
                logging.warning(f"MQTT Connection Error: {e}")

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Connection callback to verify successful connection"""
        if reason_code.is_failure:
            self.connected = False
            logging.warning(f"Failed to connect: {reason_code}. Will retry connection...")
        else:
            self.connected = True
            self.client.subscribe(self.config.get('command_topic'))

    def on_disconnect(self, client, userdata, rc, properties=None):
        """Disconnection callback"""
        logging.warning("Disconnected from MQTT broker")
        self.connected = False

    def on_publish(self, client, userdata, mid, rc=None, properties=None):
        """Publish status callback (optional)"""
        pass
    
    def on_message(self, client, userdata, message):
        """Message status callback (optional)"""
        pass

    def on_subscribe(self, client, userdata, mid, reason_code_list, properties):
        """Subscribe status callback"""
        if reason_code_list[0].is_failure:
            logging.warning(f"Broker rejected you subscription: {reason_code_list[0]}")
        else:
            logging.debug(f"Broker granted the following QoS: {reason_code_list[0].value}")

    def publish_state(self, source_topic, source, details_topic, details):
        """Publish audio state to MQTT topics"""
        try:
            # Publish source
            self.client.publish(source_topic, str(source), qos=1, retain=False)
            # Publish details
            self.client.publish(details_topic, str(details), qos=1, retain=False)
        except Exception as e:
            logging.error(f"MQTT Publish Error: {e}")

class AudioState:
    """Class to track and compare audio playback states"""
    def __init__(self):
        self.current_pid = None
        self.current_source = None
        self.current_details = None
        self.last_update = datetime.now()

    def needs_refresh(self, min_interval=2):
        """Check if state needs to be refreshed based on time interval"""
        return (datetime.now() - self.last_update).total_seconds() >= min_interval

    def __eq__(self, other):
        """Compare two audio states for equality"""
        if not isinstance(other, AudioState):
            return False
        return (self.current_pid == other.current_pid and
                self.current_source == other.current_source and
                self.current_details == other.current_details)

    def __str__(self):
        """String representation of the audio state"""
        if not self.current_source:
            return "No active playback"
        return f"Source: {self.current_source} Details: {self.current_details}"

def load_config():
    """Load configuration from YAML file"""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logging.debug(f"Config file not found at {CONFIG_PATH}. Using default settings.")
        return {}
    except yaml.YAMLError as e:
        logging.error(f"Error parsing config file: {e}")
        return {}

def setup_logging(config):
    """Setup logging configuration"""
    log_file = '/var/log/moode_monitor.log'
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if config.get('debug', False) else logging.INFO)
    
    # Remove any existing handlers
    root_logger.handlers = []
    
    # Add new handler with filter
    handler = logging.handlers.WatchedFileHandler(log_file)
    handler.setFormatter(formatter)
    
    # Add filter to exclude watchdog events
    class WatchdogFilter(logging.Filter):
        def filter(self, record):
            # Filter out watchdog's "in-event" messages
            return not record.getMessage().startswith('in-event')
    
    handler.addFilter(WatchdogFilter())
    root_logger.addHandler(handler)

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
        logging.error(f"Error reading card status: {e}")
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
    global config
    cached = log_cache.get('spotify_status')
    if cached:
        return cached

    try:
        with open('/var/log/moode_librespot.log', 'r') as f:
            lines = deque(f, maxlen=config.get('max_lines_librespot', 100))  # Read only last 100 lines
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
                        result = match.group(1)
                        log_cache.set('spotify_status', result)
                        return result
                        
    except Exception as e:
        return None
    return None

def get_airplay_device():
    """Get AirPlay device from shairport-sync log"""
    cached = log_cache.get('airplay_device')
    if cached:
        return cached

    try:
        with open('/var/log/moode_shairport-sync.log', 'r') as f:
            lines = deque(f, maxlen=config.get('max_lines_airplay', 30))  # Read only last 30 lines
            for line in reversed(lines):
                if 'connection from' in line:
                    match = re.search(r'\("([^"]+)"\)', line)
                    if match:
                        result = match.group(1)
                        log_cache.set('airplay_device', result)
                        return result
    except Exception as e:
        return "Unknown device"

def format_radio_name(url):
    """Format radio station URL into display name"""
    # Remove port number if present
    url = url.split('/')[2].split(':')[0]
    # Remove domain extensions
    url = re.sub(r'\.\w{2,3}$', '', url)
    # Replace dot with spaces
    url = url.replace('.', ' ')
    # Remove keywords = live, stream
    url = re.sub(r'\b(live|stream)\b', '', url)
    # Convert to upper case
    url = url.upper()
    return url

def format_radio_details(details):
    """Format radio details string"""
    # With regex remove positive or negative number after last star
    # Examples: "LINK * 1002264", "SPOT * 100% Grandi Successi * -1"
    details = re.sub(r'\s*\*\s*[-+]?\d+$', '', details)
    return details

def get_radio_info():
    """Get current radio station info"""
    try:
        # First check if MPD is actually playing
        status_result = subprocess.run(['mpc', 'status'], capture_output=True, text=True, timeout=10)
        status_output = status_result.stdout.strip().split('\n')
        
        # Check if it's actually playing
        if len(status_output) > 1 and '[playing]' not in status_output[1]:
            return None, None  # Not playing, return None to indicate no active playback
            
        # Only proceed with getting details if actually playing
        if len(status_output) < 2:
            return None, None

        if status_output[0].startswith('http'):
            source = format_radio_name(status_output[0])
            details = None
        elif len(status_output[0].split(':')) > 1:
            source = status_output[0].split(':')[0]
            details = format_radio_details(status_output[0].split(':')[1].strip())
        else:
            source_result = subprocess.run(['mpc', 'current', '--format', '%file%'], 
                                        capture_output=True, text=True)
            source_output = source_result.stdout.strip()
            source = format_radio_name(source_output) if source_output.startswith('http') else None
            details = format_radio_details(status_output[0])
            
        # Only cache if we have valid data
        if source and source.strip():
            log_cache.set('radio_info', (source, details))
        return source, details
    except subprocess.TimeoutExpired:
        logging.debug("GET_RADIO_INFO timed out")
    except Exception as e:
        logging.debug(f"Error getting radio info: {e}")
        return None, None

def mpc_maintenance():
    """Perform maintenance tasks for MPD"""
    try:
        result = subprocess.run(['mpc', 'update'], capture_output=True, text=True, timeout=10)
        mpc_result = result.stdout.replace('\n', '').strip()
        logging.debug(f"MPD Update: {mpc_result}")
    except subprocess.TimeoutExpired:
        logging.debug("MPC_MAINTENANCE timed out")
    except Exception as e:
        logging.debug(f"MPC_MAINTENANCE error: {e}")

def get_current_state():
    """Get the current audio state"""
    state = AudioState()
    
    pid = get_card_status()
    if not pid:
        # Clear cache when no playback is detected
        if hasattr(log_cache, 'cache'):
            log_cache.cache.clear()
        return state

    state.current_pid = pid
    cmdline = get_process_cmdline(pid)
    if not cmdline:
        return state

    if 'librespot' in cmdline:
        state.current_source = "Spotify"
        state.current_details = get_spotify_status()
    elif 'shairport-sync' in cmdline:
        state.current_source = "AirPlay"
        state.current_details = get_airplay_device()
    elif 'mpd' in cmdline:
        state.current_source, state.current_details = get_radio_info()
        # If radio info returns None, this is truly no playback
        if not state.current_source:
            state.current_source = None
            state.current_details = None
    else:
        state.current_source = "Unknown"
        state.current_details = f"PID: {pid}"

    state.last_update = datetime.now()
    return state

def wait_for_mpd():
    """Wait for MPD to be fully operational"""
    logging.debug("Waiting for MPD to be ready...")
    max_retries = 30
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            result = subprocess.run(['mpc', 'status'], 
                                 capture_output=True, 
                                 text=True, 
                                 timeout=10)
            if result.returncode == 0:
                logging.debug("MPD is ready")
                return True
        except Exception as e:
            logging.debug(f"MPD not ready: {e}")
        
        retry_count += 1
        time.sleep(1)
    
    return False

def main():
    """Main program loop"""
    # Load configuration
    global config
    config = load_config()

    # Setup logging
    setup_logging(config)
    
    logging.debug("Configuration loaded")
    
    
    # Initialize cache and watchdog
    global log_cache
    log_cache = LogCache(max_age_seconds=config.get('log_cache_max_age', 5))
    observer = Observer()
    handler = LogWatcher(lambda: log_cache.cache.clear())
    # Watch specific log files instead of whole directory
    for log_file in LogWatcher.WATCHED_FILES:
        if Path(log_file).exists():
            observer.schedule(handler, Path(log_file).parent, recursive=False)
    observer.start()
    logging.debug("Log watcher started")
    
    # Wait for MPD to be ready
    mpc_maintenance()
    if not wait_for_mpd():
        logging.warning("Failed to detect working MPD")
    
    # Initialize MQTT Handler
    mqtt_handler = MQTTHandler(config)
    
    logging.info("Starting to monitor audio playback state")
    previous_state = AudioState()
    
    while True:
        try:
            if previous_state.needs_refresh():
                current_state = get_current_state()
                
                # Only print and publish if the state has changed
                if current_state != previous_state:
                    # Introduce a short delay to debounce state changes
                    time.sleep(0.5)
                    rechecked_state = get_current_state()
                    
                    # Confirm the state change after the delay
                    if rechecked_state != previous_state:
                        logging.debug("="*50)
                        logging.debug(rechecked_state)
                        logging.debug("="*50)
                        
                        # Publish to MQTT if topics are configured
                        if config.get('source_topic') and config.get('details_topic'):
                            mqtt_handler.handle_connection()
                            mqtt_handler.publish_state(
                                config['source_topic'], 
                                rechecked_state.current_source if rechecked_state.current_source else "",
                                config['details_topic'], 
                                rechecked_state.current_details if rechecked_state.current_details else ""
                            )
                        
                        previous_state = rechecked_state
            
            # Wait before checking again
            time.sleep(1)
            
        except KeyboardInterrupt:
            logging.info("Monitoring stopped.")
            observer.stop()
            break
        except Exception as e:
            logging.error("Error: {e}")
            time.sleep(1)
    
    observer.join()

if __name__ == "__main__":
    main()
