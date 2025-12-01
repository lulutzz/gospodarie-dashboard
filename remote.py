import network, urequests, time, machine, ubinascii, ssl
from umqtt.simple import MQTTClient
import dht

VERSION = "3.0"

# ============================================================
# 1) CONFIG MQTT CLOUD (HiveMQ)
# ============================================================
MQTT_HOST = "c72cc38c1f184d0199cc4daa938bac6f.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "burlusi"
MQTT_PASS = "Burlusi166?"

# Topicuri standard
MQTT_BASE = b"/gosp/"
MQTT_CMD  = b"/cmd/"
MQTT_STATUS = b"/status"
MQTT_LOG = b"/log"
MQTT_PING = b"/ping"

# ============================================================
# 2) CONFIG WiFi
# ============================================================
SSID = "DIGI-Y4bX"
PASSWORD = "Burlusi166?"

# ============================================================
# 3) ThingSpeak CONFIG (global)
# ============================================================
CONFIG_CHANNEL = 1622205
CONFIG_READ_KEY = "0AE98QNTESXHZF8L"

# ============================================================
# 4) DATA ThingSpeak (un singur canal)
# ============================================================
DATA_CHANNEL_APIKEY = "ZPT57WZJNMLGM2X1"
DATA_BASE_URL = "https://api.thingspeak.com/update"

# ============================================================
# 5) Telegram Alerts
# ============================================================
BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
CHAT_ID   = "1705327493"

# ============================================================
# 6) Detectare device via MAC
# ============================================================
def get_device_id():
    return ubinascii.hexlify(network.WLAN().config('mac')).decode().upper()

DEVICE_ID = get_device_id()

# ============================================================
# 7) MAPĂ CAMERE
# ============================================================
CAMERE = {
    "EC62609C8900": "Camara",
    "7821849F8900": "Bucatarie"
}

CAMERA = CAMERE.get(DEVICE_ID, "UNKNOWN")

# Senzor
sensor = dht.DHT11(machine.Pin(4))


# ============================================================
# Send Telegram
# ============================================================
def send_telegram(msg):
    try:
        url = "https://api.telegram.org/bot{}/sendMessage".format(BOT_TOKEN)
        body = "chat_id={}&text={}".format(CHAT_ID, msg.replace(" ", "%20").replace("\n", "%0A"))
        headers={"Content-Type":"application/x-www-form-urlencoded"}
        r = urequests.post(url, data=body, headers=headers)
        print("TG:", r.text)
        r.close()
    except:
        print("Eroare TG")


# ============================================================
# Conectare WiFi
# ============================================================
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return

    print("WiFi...")
    wlan.connect(SSID, PASSWORD)

    t0 = time.time()
    while not wlan.isconnected():
        if time.time() - t0 > 20:
            machine.reset()
        time.sleep(0.3)

    print("WiFi OK:", wlan.ifconfig())


# ============================================================
# Citește CONFIG din ThingSpeak
# ============================================================
def fetch_config():
    url = "https://api.thingspeak.com/channels/{}/feeds.json?api_key={}&results=1".format(
        CONFIG_CHANNEL, CONFIG_READ_KEY
    )
    try:
        r = urequests.get(url)
        data = r.json()
        r.close()

        feeds = data.get("feeds", [])
        if not feeds:
            return default_config()

        last = feeds[-1]

        return {
            "sleep_minutes": int(float(last.get("field1", 30) or 30)),
            "alarm_temp":   int(float(last.get("field2", 30) or 30)),
            "alarm_hum":    int(float(last.get("field3", 80) or 80)),
            "DEBUGGING":    int(float(last.get("field4", 0) or 0))
        }

    except Exception as e:
        print("CONFIG ERR:", e)
        return default_config()


def default_config():
    return {
        "sleep_minutes": 30,
        "alarm_temp": 30,
        "alarm_hum": 80,
        "DEBUGGING": 0
    }


# ============================================================
# MQTT – callback comandă
# ============================================================
def mqtt_callback(topic, msg):
    topic = topic.decode()
    msg = msg.decode()

    print("MQTT CMD:", topic, msg)

    # restart
    if topic.endswith("/cmd/reboot"):
        send_telegram("Reboot: {}".format(CAMERA))
        time.sleep(1)
        machine.reset()

    # ping
    if topic.endswith("/cmd/ping"):
        publish_status("Ping OK from {}".format(CAMERA))

    # force run
    if topic.endswith("/cmd/run"):
        publish_status("Force run executat")
        machine.reset()


# ============================================================
# MQTT Client
# ============================================================
MQTT_CLIENT = None

def mqtt_connect():
    global MQTT_CLIENT

    client_id = b"ESP32_" + DEVICE_ID.encode()

    MQTT_CLIENT = MQTTClient(
        client_id=client_id,
        server=MQTT_HOST,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASS,
        ssl=True,
        ssl_params={"server_hostname": MQTT_HOST}
    )

    MQTT_CLIENT.set_callback(mqtt_callback)
    MQTT_CLIENT.connect()

    # Subscribe device-specific
    MQTT_CLIENT.subscribe(MQTT_BASE + DEVICE_ID.encode() + MQTT_CMD + b"#")

    # Subscribe global
    MQTT_CLIENT.subscribe(MQTT_BASE + b"all" + MQTT_CMD + b"#")

    publish_status("Device online (v{})".format(VERSION))

    print("MQTT conectat.")


def publish_status(msg):
    try:
        MQTT_CLIENT.publish(
            MQTT_BASE + DEVICE_ID.encode() + MQTT_STATUS,
            msg
        )
    except:
        pass


def publish_log(msg):
    try:
        MQTT_CLIENT.publish(
            MQTT_BASE + DEVICE_ID.encode() + MQTT_LOG,
            msg
        )
    except:
        pass


# ============================================================
# PROGRAM PRINCIPAL
# ============================================================
print("=== remote.py v{} ===".format(VERSION))
print("Device:", DEVICE_ID)
print("Camera:", CAMERA)

connect_wifi()
mqtt_connect()

# CONFIG
cfg = fetch_config()

sleep_minutes = cfg["sleep_minutes"]
alarm_temp    = cfg["alarm_temp"]
alarm_hum     = cfg["alarm_hum"]
DEBUGGING     = cfg["DEBUGGING"]

print("CFG:", cfg)

# Citire senzor
sensor.measure()
temp = sensor.temperature()
hum  = sensor.humidity()
print("Citire:", temp, hum)

# Trimite date ThingSpeak
try:
    url = "{}?api_key={}&field1={}&field2={}".format(
        DATA_BASE_URL, DATA_CHANNEL_APIKEY, temp, hum
    )
    r = urequests.get(url)
    print("DATA:", r.text)
    r.close()
except:
    print("TS ERR")

# Alarme
if temp >= alarm_temp:
    send_telegram("ALERTA TEMP {}: {}C (prag {}C)".format(CAMERA, temp, alarm_temp))

if hum >= alarm_hum:
    send_telegram("ALERTA HUM {}: {}% (prag {}%)".format(CAMERA, hum, alarm_hum))

publish_log("RUN OK T={} H={}".format(temp, hum))

# DEBUG
if DEBUGGING == 1:
    print("DEBUG ACTIVE – nu dorm")
    while True:
        MQTT_CLIENT.check_msg()
        time.sleep(0.5)

# DEEP SLEEP
print("Sleep:", sleep_minutes, "min")
time.sleep(1)
machine.deepsleep(sleep_minutes * 60 * 1000)
