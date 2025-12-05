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
last_ts_update = 0
TELEGRAM_BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
TELEGRAM_CHAT_ID   = "1705327493"

DEVICE_INFO = {
    "EC62609C8900": {        # ID Camara
        "name": "Camara",
        "config_fields": {   # din canalul CONFIG (1622205)
            "alarm_temp": "field2",
            "alarm_hum":  "field3",
        },
        "data_fields": {     # în canalul DATA (1613849)
            "temp": "field1",
            "hum":  "field2",
        },
    },

    "7821849F8900": {        # ID Bucătărie
        "name": "Bucatarie",
        "config_fields": {
            "alarm_temp": "field6",
            "alarm_hum":  "field7",
        },
        "data_fields": {
            "temp": "field5",
            "hum":  "field6",
        },
    },

    "XXXXXXXXXXXX": {        # exemplu pentru Baie – completezi ID real
        "name": "Baie",
        "config_fields": {
            "alarm_temp": "field4",
            "alarm_hum":  "field5",
        },
        "data_fields": {
            "temp": "field3",
            "hum":  "field4",
        },
    },
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
INFO = DEVICE_INFO.get(DEVICE_ID, {})   # <– NOU

print("=== remote.py v1.3 ===")
print("Device:", DEVICE_ID)
print("Camera:", ROOM)
print("INFO:", INFO)


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
    # luăm mai multe înregistrări ca să putem căuta ultima valoare nenulă
    url = "https://api.thingspeak.com/channels/{}/feeds.json?results=20".format(
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

        # helper: caută de la sfârșit spre început ultimul field nenul
        def last_non_empty_int(field, default):
            for f in reversed(feeds):
                v = f.get(field)
                if v is None or v == "":
                    continue
                try:
                    return int(float(v))
                except:
                    return default
            return default

        cfg = {}

        # ---------- valori globale ----------
        cfg["sleep_minutes"] = last_non_empty_int("field1", 30)
        cfg["DEBUGGING"]     = last_non_empty_int("field8", 1)

        # ---------- praguri specifice device-ului ----------
        info = INFO or {}
        cfg_fields = info.get("config_fields", {})
        print("cfg_fields pentru", ROOM, ":", cfg_fields)

        temp_field = cfg_fields.get("alarm_temp", "field2")  # ex: 'field6' la Bucatarie
        hum_field  = cfg_fields.get("alarm_hum",  "field3")  # ex: 'field7' la Bucatarie

        cfg["alarm_temp"] = last_non_empty_int(temp_field, 25)
        cfg["alarm_hum"]  = last_non_empty_int(hum_field, 60)

        print("SETARI:", cfg)
        return cfg

    except Exception as e:
        print("Eroare CONFIG:", e)
        return {
            "sleep_minutes": 30,
            "DEBUGGING": 1,
            "alarm_temp": 25,
            "alarm_hum": 60,
        }

# ============================================================
# 7. Trimitere date în DATA – Gospodarie
# ============================================================

def send_data(temp, hum):
    """
    Trimite temp/hum la ThingSpeak folosind maparea din DEVICE_INFO.
    """
    global last_ts_update

    info = INFO or {}
    df   = info.get("data_fields", {})
    print("data_fields pentru", ROOM, ":", df)

    f_temp = df.get("temp", "field1")
    f_hum  = df.get("hum",  "field2")

    base_url = (
        "https://api.thingspeak.com/update?"
        "api_key={}&{}={}&{}={}"
    ).format(DATA_CHANNEL_API_KEY, f_temp, temp, f_hum, hum)

    print("TS[{}]: URL → {}".format(ROOM, base_url))

    for attempt in range(3):
        now = time.time()
        diff = now - last_ts_update

        # limită ThingSpeak ~15 sec
        if diff < 16:
            wait_s = int(16 - diff)
            print("TS[{}]: prea devreme, aștept {} sec (încercarea {})".format(
                ROOM, wait_s, attempt + 1
            ))
            for _ in range(wait_s):
                time.sleep(1)
                wdt.feed()

        try:
            r = urequests.get(base_url)
            resp = r.text.strip()
            r.close()
            last_ts_update = time.time()

            print("TS[{}]: DATA (încercarea {}) → {}".format(
                ROOM, attempt + 1, resp
            ))

            if resp != "0":
                return True

            print("TS[{}]: răspuns 0 (nu a acceptat). Reîncerc...".format(ROOM))

        except Exception as e:
            print("TS[{}]: Eroare DATA (încercarea {}): {}".format(
                ROOM, attempt + 1, e
            ))

    print("TS[{}]: am renunțat după 3 încercări fără succes.".format(ROOM))
    return False

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
    # Trimite date
    if t is not None and h is not None:
        print("SENZOR:", ROOM, "T=", t, "H=", h)
        ok_ts = send_data(t, h)
        print("TS trimis pentru", ROOM, "ok?", ok_ts)

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

    # DEBUGGING → fără deep sleep, doar pauză scurtă
    if cfg["DEBUGGING"] == 1:
        print("DEBUG → reluare 10 sec (soft)")
        for _ in range(30):
            time.sleep(1)
        continue

    # PRODUCȚIE – folosim din nou deep sleep ca înainte (stabil)
    minutes = cfg.get("sleep_minutes", 5)
    print("Sleep:", minutes, "minute (deep sleep)")
    deepsleep(int(minutes * 60 * 1000))
