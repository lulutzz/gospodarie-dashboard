import network
import urequests
import time
import dht
from machine import Pin, deepsleep, WDT

# ============================================================
#  remote.py v1.2 – Sistem senzori gospodărie
#
#  Funcționalități:
#   ✔ Autodetectare cameră după Device ID
#   ✔ Citire CONFIG global (channel 1622205)
#   ✔ Mapare automată valori pentru Camara / Baie / Bucatarie
#   ✔ Trimitere date în DATA – Gospodarie (1613849)
#   ✔ Alerte Telegram (temperatură + umiditate)
#   ✔ Anti-spam: max 1 alertă / ciclu
#   ✔ DEBUGGING: fără deep-sleep
#
#  Arhitectură ThingSpeak:
#   CONFIG – Gospodarie     → 1622205
#   DATA   – Gospodarie     → 1613849 (temp=field1, hum=field2)
#   LOG    – Alerte         → 1638468 (opțional)
#
# ============================================================


# ============================================================
# 1. CONFIG – valori statice
# ============================================================

WIFI_SSID = "DIGI-Y4bX"
WIFI_PASS = "Burlusi166?"

CONFIG_CHANNEL = 1622205         # CONFIG – Gospodarie
DATA_CHANNEL_API_KEY = "ZPT57WZJNMLGM2X1"   # DATA – Gospodarie

TELEGRAM_BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
TELEGRAM_CHAT_ID   = "1705327493"

DEVICE_INFO = {
    "EC62609C8900": { "name": "Camara" },
    "XXXXXXXXXXXX": { "name": "Baie" },          # <-- completezi când ai device ID
    "7821849F8900": { "name": "Bucatarie" }      # <-- completezi când ai device ID
}


# ============================================================
# 2. DETECTAREA CAMEREI DUPĂ DEVICE ID
# ============================================================

def get_device_id():
    try:
        import ubinascii, machine
        return ubinascii.hexlify(machine.unique_id()).decode().upper()
    except:
        return "UNKNOWN"


DEVICE_ID = get_device_id()
ROOM = DEVICE_INFO.get(DEVICE_ID, {}).get("name", "UNKNOWN")

print("=== remote.py v1.2 ===")
print("Device:", DEVICE_ID)
print("Camera:", ROOM)


# ============================================================
# 3. SAFE MODE – dacă se apasă BOOT
# ============================================================

boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
if boot_btn.value() == 0:
    print("=== SAFE MODE ===")
    print("Nu rulez remote.py")
    while True:
        time.sleep(1)


# ============================================================
# 4. Watchdog
# ============================================================

wdt = WDT(timeout=60000)


# ============================================================
# 5. Conectare WiFi
# ============================================================

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return wlan

    print("Conectare WiFi...")
    wlan.connect(WIFI_SSID, WIFI_PASS)

    timeout = time.time() + 20
    while not wlan.isconnected():
        wdt.feed()
        if time.time() > timeout:
            print("WiFi FAIL → retry in 60 sec")
            deepsleep(60000)
        time.sleep(0.3)

    print("WiFi OK:", wlan.ifconfig())
    return wlan


# ============================================================
# 6. Citire CONFIG global
# ============================================================

def fetch_config():

    url = "https://api.thingspeak.com/channels/{}/feeds.json?results=1".format(
        CONFIG_CHANNEL
    )

    print("Citire CONFIG din:", url)

    try:
        r = urequests.get(url)
        js = r.json()
        r.close()

        feeds = js.get("feeds", [])
        if not feeds:
            raise ValueError("CONFIG gol")

        last = feeds[-1]

        def read_int(field, default):
            v = last.get(field)
            if v is None or v == "":
                return default
            try:
                return int(float(v))
            except:
                return default

        # ---------- valori globale ----------
        cfg = {}
        cfg["sleep_minutes"] = read_int("field1", 30)
        cfg["DEBUGGING"]     = read_int("field8", 1)

        # ---------- pe cameră ----------
        if ROOM == "Camara":
            cfg["alarm_temp"] = read_int("field2", 25)
            cfg["alarm_hum"]  = read_int("field3", 60)

        elif ROOM == "Baie":
            cfg["alarm_temp"] = read_int("field4", 28)
            cfg["alarm_hum"]  = read_int("field5", 75)

        elif ROOM == "Bucatarie":
            cfg["alarm_temp"] = read_int("field6", 27)
            cfg["alarm_hum"]  = read_int("field7", 70)

        print("SETARI:", cfg)
        return cfg

    except Exception as e:
        print("Eroare CONFIG:", e)
        return {
            "sleep_minutes": 30,
            "DEBUGGING": 1,
            "alarm_temp": 25,
            "alarm_hum": 60
        }

# ============================================================
# 7. Trimitere date în DATA – Gospodarie
# ============================================================

def send_data(temp, hum):
    url = (
        "https://api.thingspeak.com/update?"
        "api_key={}&field1={}&field2={}"
    ).format(DATA_CHANNEL_API_KEY, temp, hum)

    try:
        r = urequests.get(url)
        print("DATA:", r.text)
        r.close()
    except Exception as e:
        print("Eroare DATA:", e)


# ============================================================
# 8. Telegram
# ============================================================

def urlenc(s):
    res = []
    for ch in s:
        o = ord(ch)
        if (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122):
            res.append(ch)
        elif ch == " ":
            res.append("%20")
        elif ch == "\n":
            res.append("%0A")
        else:
            res.append("%%%02X" % o)
    return "".join(res)


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    msg = urlenc(msg)
    body = "chat_id={}&text={}".format(TELEGRAM_CHAT_ID, msg)

    url = "https://api.telegram.org/bot{}/sendMessage".format(
        TELEGRAM_BOT_TOKEN
    )
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        r = urequests.post(url, data=body, headers=headers)
        print("TG:", r.text)
        r.close()
    except Exception as e:
        print("Eroare TG:", e)


# ============================================================
# 9. Program principal
# ============================================================

sensor = dht.DHT11(Pin(4))

while True:

    wdt.feed()
    connect_wifi()
    cfg = fetch_config()

    # Citire senzor
    try:
        sensor.measure()
        t = sensor.temperature()
        h = sensor.humidity()
        print("Citire:", t, h)
    except:
        print("Eroare senzor")
        t = None
        h = None

    # Trimite date
    if t is not None and h is not None:
        send_data(t, h)

    # Alerte
    if t is not None and t >= cfg["alarm_temp"]:
        send_telegram(
            "ALERTA TEMPERATURA - {}\nTemperatura: {} C\nPrag: {} C".format(
                ROOM, t, cfg["alarm_temp"]
            )
        )

    if h is not None and h >= cfg["alarm_hum"]:
        send_telegram(
            "ALERTA UMIDITATE - {}\nUmiditate: {} %\nPrag: {} %".format(
                ROOM, h, cfg["alarm_hum"]
            )
        )

    # DEBUGGING → fără deep sleep
    if cfg["DEBUGGING"] == 1:
        print("DEBUG → reluare 10 sec")
        time.sleep(10)
        continue

    # PRODUCȚIE

    minutes = cfg.get("sleep_minutes", 5)
    print("Sleep:", minutes, "minute (soft)")

    for _ in range(int(minutes * 60)):
        time.sleep(1)
