# ============================================================
#  remote.py  –  v2.0
#  Sistem multi-dispozitiv: Cămară, Baie, Bucătărie
#  Suport: ThingSpeak CONFIG + DATA + LOG + Telegram
# ============================================================

import network
import urequests
import time
import dht
from machine import Pin, deepsleep, WDT
import ubinascii, machine

# ------------------------------------------------------------
#   CONFIG ThingSpeak (cele 3 canale centrale)
# ------------------------------------------------------------

CHANNEL_DATA_ID   = 1613849     # DATA – Gospodarie
CHANNEL_CONFIG_ID = 1622205     # CONFIG – Gospodarie
CHANNEL_LOG_ID    = 1638468     # LOG – Alerte

LOG_WRITE_KEY     = "XP7PSBXSVN3CXWKQ"

# ------------------------------------------------------------
#   Telegram
# ------------------------------------------------------------
BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
CHAT_ID   = "1705327493"

# ------------------------------------------------------------
#   Rețea WiFi
# ------------------------------------------------------------
SSID = "DIGI-Y4bX"
PASSWORD = "Burlusi166?"

# ------------------------------------------------------------
# Identificare dispozitiv (Device ID)
# ------------------------------------------------------------
device_id = ubinascii.hexlify(machine.unique_id()).decode()


# ------------------------------------------------------------
#  Mapping camere + field-uri
# ------------------------------------------------------------

DEVICE_INFO = {
    "EC62609C8900": {        # CĂMARĂ
        "name": "Camara",
        "temp_field": 1,
        "hum_field": 2
    },

    "7821849f8900": {        # BUCĂTĂRIE
        "name": "Bucatarie",
        "temp_field": 5,
        "hum_field": 6
    },

    # Când ai device-ul pentru baie îl adaugi astfel:
    # "XXXXXXXXXXXX": {
    #     "name": "Baie",
    #     "temp_field": 3,
    #     "hum_field": 4
    # }
}


# ------------------------------------------------------------
#  SAFE MODE (dacă ții BOOT apăsat)
# ------------------------------------------------------------
boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
if boot_btn.value() == 0:
    print("=== SAFE MODE === (BOOT apăsat)")
    while True:
        time.sleep(1)


# ------------------------------------------------------------
#  WATCHDOG
# ------------------------------------------------------------
wdt = WDT(timeout=60000)


# ------------------------------------------------------------
#  WiFi
# ------------------------------------------------------------
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
                print("WiFi FAIL → retry după deep sleep")
                deepsleep(30000)
            time.sleep(0.3)

    print("WiFi OK:", wlan.ifconfig())


# ------------------------------------------------------------
#  Citește CONFIG global
# ------------------------------------------------------------
def fetch_config():
    url = (
        "https://api.thingspeak.com/channels/{}/feeds.json?results=1"
        .format(CHANNEL_CONFIG_ID)
    )
    print("Citire CONFIG din:", url)

    try:
        r = urequests.get(url)
        j = r.json()
        r.close()

        feeds = j.get("feeds", [])
        if not feeds:
            raise Exception("Fără feed-uri")

        f = feeds[-1]

        def val(field, default):
            try:
                v = f.get(field, "")
                if v is None or v == "":
                    return default
                return int(float(v))
            except:
                return default

        cfg = {
            "sleep_minutes": val("field1", 30),
            "camara_temp":   val("field2", 25),
            "camara_hum":    val("field3", 60),
            "baie_temp":     val("field4", 28),
            "baie_hum":      val("field5", 75),
            "buc_temp":      val("field6", 27),
            "buc_hum":       val("field7", 70),
            "DEBUGGING":     val("field8", 0)
        }

        print("SETARI:", cfg)
        return cfg

    except Exception as e:
        print("Eroare CONFIG:", e)
        return {
            "sleep_minutes": 30,
            "camara_temp": 25,
            "camara_hum": 60,
            "baie_temp": 28,
            "baie_hum": 75,
            "buc_temp": 27,
            "buc_hum": 70,
            "DEBUGGING": 0
        }


# ------------------------------------------------------------
# Scriere valori în canalul DATA
# ------------------------------------------------------------
def send_to_data(temp, hum, f_temp, f_hum):
    url = (
        "https://api.thingspeak.com/update?"
        "api_key=ZPT57WZJNMLGM2X1"     # cheia ta WRITE pentru DATA
        "&field{}={}&field{}={}"
        .format(f_temp, temp, f_hum, hum)
    )
    try:
        r = urequests.get(url)
        print("DATA:", r.text)
        r.close()
    except Exception as e:
        print("Eroare DATA:", e)


# ------------------------------------------------------------
# Scriere alerte în canalul LOG
# ------------------------------------------------------------
def log_alert(camera, tip, valoare, prag):
    url = (
        "https://api.thingspeak.com/update?"
        "api_key={}"
        "&field1={}"
        "&field2={}"
        "&field3={}"
        "&field4={}"
        "&field5={}"
        "&field6={}"
    ).format(
        LOG_WRITE_KEY,
        device_id,
        camera,
        tip,
        valoare,
        prag,
        time.time()
    )
    try:
        urequests.get(url)
        print("LOG scris.")
    except:
        print("Eroare LOG.")


# ------------------------------------------------------------
#  Telegram (ASCII safe)
# ------------------------------------------------------------
def send_telegram(msg):
    def urlencode(s):
        out = []
        for c in s:
            o = ord(c)
            if (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122) or c in "-_.~":
                out.append(c)
            elif c == " ":
                out.append("%20")
            elif c == "\n":
                out.append("%0A")
            else:
                out.append("%%%02X" % o)
        return "".join(out)

    body = "chat_id={}&text={}".format(CHAT_ID, urlencode(msg))
    url = "https://api.telegram.org/bot{}/sendMessage".format(BOT_TOKEN)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        r = urequests.post(url, data=body, headers=headers)
        print("Telegram:", r.text)
        r.close()
    except Exception as e:
        print("Telegram E:", e)


# ------------------------------------------------------------
#  MAIN
# ------------------------------------------------------------
print("=== remote.py v2.0 ===")
print("Device ID:", device_id)

info = DEVICE_INFO.get(device_id)
if not info:
    print("⚠ Dispozitiv NE-înregistrat în DEVICE_INFO!")
    print("Adaugă device-ul în GitHub.")
    while True:
        time.sleep(1)

camera = info["name"]
f_temp = info["temp_field"]
f_hum  = info["hum_field"]

print("Camera:", camera)

while True:
    wdt.feed()

    connect_wifi()
    cfg = fetch_config()

    # Selectarea pragurilor după cameră
    if camera == "Camara":
        alarm_temp = cfg["camara_temp"]
        alarm_hum  = cfg["camara_hum"]
    elif camera == "Baie":
        alarm_temp = cfg["baie_temp"]
        alarm_hum  = cfg["baie_hum"]
    else:
        alarm_temp = cfg["buc_temp"]
        alarm_hum  = cfg["buc_hum"]

    DEBUG = cfg["DEBUGGING"]

    # citire senzor
    sensor = dht.DHT11(Pin(4))
    sensor.measure()
    t = sensor.temperature()
    h = sensor.humidity()

    print("Citire:", t, h)

    send_to_data(t, h, f_temp, f_hum)

    # alerte
    if t >= alarm_temp:
        msg = "ALERTA TEMP – {}: {}C (prag {}C)".format(camera, t, alarm_temp)
        send_telegram(msg)
        log_alert(camera, "TEMP", t, alarm_temp)

    if h >= alarm_hum:
        msg = "ALERTA UMID – {}: {}% (prag {}%)".format(camera, h, alarm_hum)
        send_telegram(msg)
        log_alert(camera, "HUM", h, alarm_hum)

    if DEBUG == 1:
        print("DEBUG – reluare în 10 sec")
        time.sleep(10)
        continue

    print("Deep sleep {} min".format(cfg["sleep_minutes"]))
    deepsleep(cfg["sleep_minutes"] * 60000)
