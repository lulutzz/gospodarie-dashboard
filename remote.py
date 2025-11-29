"""
============================================================
   ESP32 – Sistem Monitorizare Încăperi (Remote Script)
   Versiune: 1.1.0
   DATA: 2025-01-10
   FIX: Mesaje Telegram ASCII-only (fără caractere problematice)
============================================================
"""

import network
import ubinascii
import urequests
import time
import dht
from machine import Pin, deepsleep, WDT

# ============================================================
#   1. CONFIG STATIC – WiFi, Telegram, Device mapping
# ============================================================

SSID = "DIGI-Y4bX"
PASSWORD = "Burlusi166?"

BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
CHAT_ID   = "1705327493"

DATA_BASE_URL_DEFAULT = "https://api.thingspeak.com/update"

DEVICE_INFO = {
    "EC62609C8900": {
        "name": "Camara",        # ASCII-safe
        "config_channel": 1613849,
        "data_api_key": "ZPT57WZJNMLGM2X1"
    },

    "MAC_PENTRU_BAIE": {
        "name": "Baie",
        "config_channel": 3186869,
        "data_api_key": "API_KEY_PENTRU_BAIE"
    },

    "MAC_PENTRU_BUCATARIE": {
        "name": "Bucatarie",
        "config_channel": 1638468,
        "data_api_key": "API_KEY_PENTRU_BUCATARIE"
    }
}

FALLBACK_CONFIG_CHANNEL = 1622205
FALLBACK_NAME           = "UnknownDevice"
FALLBACK_API_KEY        = "XP7PSBXSVN3CXWKQ"

sensor = dht.DHT11(Pin(4))

# ============================================================
# SAFE MODE
# ============================================================

boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
if boot_btn.value() == 0:
    print("SAFE MODE - remote.py nu ruleaza.")
    while True:
        time.sleep(1)

# ============================================================
# WATCHDOG
# ============================================================

wdt = WDT(timeout=60000)


# ============================================================
# FUNCTII
# ============================================================

def get_device_id():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    return ubinascii.hexlify(wlan.config('mac')).decode().upper()


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Conectare WiFi...")
        wlan.connect(SSID, PASSWORD)

        timeout = time.time() + 20
        while not wlan.isconnected():
            wdt.feed()
            if time.time() > timeout:
                print("WiFi FAIL -> reset soft")
                import machine
                machine.reset()
            time.sleep(0.3)

    print("WiFi OK:", wlan.ifconfig())


def fetch_config(channel_id):
    cfg = {
        "sleep_minutes": 1,
        "alarm_temp": 25,
        "alarm_hum": 40,
        "sampling_count": 1,
        "DEBUGGING": 1,
    }

    try:
        url = "https://api.thingspeak.com/channels/{}/feeds.json?results=1".format(channel_id)

        print("Citire CONFIG din:", url)
        r = urequests.get(url)
        data = r.json()
        r.close()

        feeds = data.get("feeds", [])
        if not feeds:
            print("CONFIG gol -> default")
            return cfg

        last = feeds[-1]

        def get_int(field, default):
            v = last.get(field)
            if v is None or v == "":
                return default
            try:
                return int(float(v))
            except:
                return default

        cfg["sleep_minutes"]  = get_int("field1", cfg["sleep_minutes"])
        cfg["alarm_temp"]     = get_int("field2", cfg["alarm_temp"])
        cfg["alarm_hum"]      = get_int("field3", cfg["alarm_hum"])
        cfg["sampling_count"] = get_int("field4", cfg["sampling_count"])
        cfg["DEBUGGING"]      = get_int("field5", cfg["DEBUGGING"])

        return cfg

    except Exception as e:
        print("Eroare CONFIG:", e)
        return cfg


def send_to_thingspeak(temp, hum, api_key):
    try:
        url = "{}?api_key={}&field1={}&field2={}".format(DATA_BASE_URL_DEFAULT, api_key, temp, hum)
        r = urequests.get(url)
        print("TSK:", r.text)
        r.close()
    except Exception as e:
        print("TSK error:", e)


def send_telegram(msg):

    global BOT_TOKEN, CHAT_ID

    if not msg or msg.strip() == "":
        print("Mesaj gol -> skip")
        return

    def url_encode(s):
        out = []
        for ch in s:
            o = ord(ch)
            if (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122) or ch in "-_.~":
                out.append(ch)
            elif ch == " ":
                out.append("%20")
            elif ch == "\n":
                out.append("%0A")
            else:
                out.append("%%%02X" % o)
        return "".join(out)

    encoded = url_encode(msg)
    body = "chat_id={}&text={}".format(CHAT_ID, encoded)

    url = "https://api.telegram.org/bot{}/sendMessage".format(BOT_TOKEN)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    print("DBG body =", body)

    try:
        r = urequests.post(url, data=body, headers=headers)
        print("Telegram:", r.text)
        r.close()
    except Exception as e:
        print("Telegram error:", e)



# ============================================================
# PROGRAM PRINCIPAL
# ============================================================

print("=== remote.py v1.1 ===")

device_id = get_device_id()
print("Device:", device_id)

info = DEVICE_INFO.get(device_id, None)

if info:
    ROOM_NAME      = info["name"]
    CONFIG_CHANNEL = info["config_channel"]
    DATA_API_KEY   = info["data_api_key"]
else:
    ROOM_NAME      = FALLBACK_NAME
    CONFIG_CHANNEL = FALLBACK_CONFIG_CHANNEL
    DATA_API_KEY   = FALLBACK_API_KEY

print("Camera:", ROOM_NAME)
print("CONFIG:", CONFIG_CHANNEL)

while True:

    wdt.feed()

    connect_wifi()
    cfg = fetch_config(CONFIG_CHANNEL)

    sleep_minutes  = cfg["sleep_minutes"]
    alarm_temp     = cfg["alarm_temp"]
    alarm_hum      = cfg["alarm_hum"]
    sampling_count = cfg["sampling_count"]
    DEBUGGING      = cfg["DEBUGGING"]

    print("SETARI:", cfg)

    temps = []
    hums  = []

    for i in range(sampling_count):
        try:
            sensor.measure()
            t = sensor.temperature()
            h = sensor.humidity()
            temps.append(t)
            hums.append(h)
            print("Citire", i+1, "T:", t, "H:", h)
        except Exception as e:
            print("Eroare DHT:", e)

        if i < sampling_count - 1:
            time.sleep(15)

    if temps and hums:
        avg_temp = sum(temps) / len(temps)
        avg_hum  = sum(hums)  / len(hums)
    else:
        avg_temp = None
        avg_hum  = None

    print("Media:", avg_temp, avg_hum)

    if avg_temp is not None:
        send_to_thingspeak(round(avg_temp, 1), round(avg_hum, 0), DATA_API_KEY)

    # --- Alerte ASCII ---
    if avg_temp is not None and avg_temp >= alarm_temp:
        msg = (
            "ALERTA TEMPERATURA - {room}\n"
            "Temperatura: {t} C\n"
            "Prag: {p} C"
        ).format(room=ROOM_NAME, t=avg_temp, p=alarm_temp)
        send_telegram(msg)

    if avg_hum is not None and avg_hum >= alarm_hum:
        msg = (
            "ALERTA UMIDITATE - {room}\n"
            "Umiditate: {h} %\n"
            "Prag: {p} %"
        ).format(room=ROOM_NAME, h=avg_hum, p=alarm_hum)
        send_telegram(msg)

    if DEBUGGING == 1:
        print("DEBUG – reluare in 10 sec")
        time.sleep(10)
        continue

    print("Deep sleep pentru", sleep_minutes)
    deepsleep(sleep_minutes * 60 * 1000)
