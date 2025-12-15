import network
import urequests
import time
import dht
from machine import Pin, deepsleep, WDT
import ujson
import ubinascii
import machine

# ===================================================================
#  remote.py v2.0 – Sistem senzori gospodărie (refăcut și curățat)
# ===================================================================

# ============================== CONFIG ==============================
SENSOR_PWR_PIN = 23   # pin care alimentează DHT11
SENSOR_DATA_PIN = 4   # pin de date

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
        "config_fields": {"alarm_temp": "field2", "alarm_hum": "field3"},
        "data_fields": {"temp": "field1", "hum":  "field2"},
    },
    "7821849F8900": {        # ID Bucătărie
        "name": "Bucatarie",
        "config_fields": {"alarm_temp": "field6", "alarm_hum":  "field7"},
        "data_fields": {"temp": "field5", "hum":  "field6"},
    },
    "XXXXXXXXXXXX": {        # exemplu pentru Baie – completezi ID real
        "name": "Baie",
        "config_fields": {"alarm_temp": "field4", "alarm_hum": "field5"},
        "data_fields": {"temp": "field3", "hum":  "field4"},
    },
}

# ====================== Helper pentru log ======================
# Dacă main.py expune publish_log, îl folosește; altfel face print
try:
    from builtins import publish_log as _log
    def log(msg):
        try:
            _log("REMOTE: " + msg)
        except Exception:
            print("REMOTE LOG:", msg)
except Exception:
    def log(msg):
        print("REMOTE LOG:", msg)

# ====================== Autodetect DEVICE ID ======================
def get_device_id():
    try:
        return ubinascii.hexlify(machine.unique_id()).decode().upper()
    except:
        return "UNKNOWN"

DEVICE_ID = get_device_id()
INFO = DEVICE_INFO.get(DEVICE_ID, {})
ROOM = INFO.get("name", "UNKNOWN")
cfg_fields = INFO.get("config_fields", {})
data_fields = INFO.get("data_fields", {})

# ====================== SAFE MODE (buton BOOT) ======================
boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
if boot_btn.value() == 0:
    log("SAFE MODE – remote.py nu rulează (buton BOOT apăsat)")
    while True:
        time.sleep(1)

# ====================== Watchdog ======================
wdt = WDT(timeout=60000)

# ====================== WiFi (folosește main) ======================
# Ne bazăm pe main.py să aibă WiFi conectat deja

# ====================== Citire CONFIG din ThingSpeak ======================
def fetch_config():
    url = "https://api.thingspeak.com/channels/{}/feeds.json?results=20".format(CONFIG_CHANNEL)
    log("Citire CONFIG din: {}".format(url))
    try:
        r = urequests.get(url)
        js = r.json()
        r.close()

        feeds = js.get("feeds", [])
        if not feeds:
            raise ValueError("CONFIG gol")

        # găsește ultima valoare nenulă pentru un câmp
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
        # valori globale
        cfg["sleep_minutes"] = last_non_empty_int("field1", 30)
        cfg["DEBUGGING"]     = last_non_empty_int("field8", 1)

        # praguri pentru camera curentă
        temp_field = cfg_fields.get("alarm_temp", "field2")
        hum_field  = cfg_fields.get("alarm_hum",  "field3")

        cfg["alarm_temp"] = last_non_empty_int(temp_field, 25)
        cfg["alarm_hum"]  = last_non_empty_int(hum_field, 60)

        log("CONFIG: {}".format(cfg))
        return cfg

    except Exception as e:
        log("Eroare CONFIG: {}".format(e))
        return {
            "sleep_minutes": 30,
            "DEBUGGING": 1,
            "alarm_temp": 25,
            "alarm_hum": 60,
        }

# ====================== Trimitere date către ThingSpeak DATA ======================
def send_data(temp, hum):
    global last_ts_update

    f_temp = data_fields.get("temp", "field1")
    f_hum  = data_fields.get("hum",  "field2")

    url = ("https://api.thingspeak.com/update?api_key={}&{}={}&{}={}"
          ).format(DATA_CHANNEL_API_KEY, f_temp, temp, f_hum, hum)
    log("TS URL: {}".format(url))

    for attempt in range(3):
        now = time.time()
        if now - last_ts_update < 16:
            wait_s = int(16 - (now - last_ts_update))
            log("TS prea devreme, aștept {}s (încercarea {})".format(wait_s, attempt+1))
            for _ in range(wait_s):
                time.sleep(1)
                wdt.feed()

        try:
            r = urequests.get(url)
            resp = r.text.strip()
            r.close()
            last_ts_update = time.time()

            log("TS răspuns (încercarea {}): {}".format(attempt+1, resp))
            if resp != "0":
                return True
        except Exception as e:
            log("TS Eroare DATA (încercarea {}): {}".format(attempt+1, e))
    log("TS: 3 încercări fără succes")
    return False

# ====================== Telegram helper ======================
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
    body = "chat_id={}&text={}".format(TELEGRAM_CHAT_ID, urlenc(msg))
    url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_BOT_TOKEN)
    try:
        r = urequests.post(url, data=body, headers={"Content-Type":"application/x-www-form-urlencoded"})
        r.close()
        log("Telegram OK")
    except Exception as e:
        log("Eroare Telegram: {}".format(e))

# ====================== Citire DHT11 mediată ======================
def read_dht(samples=5, delay_s=1):
    pwr_pin = Pin(SENSOR_PWR_PIN, Pin.OUT)
    pwr_pin.value(1)
    time.sleep(2)  # stabilizare

    sensor = dht.DHT11(Pin(SENSOR_DATA_PIN))
    temps, hums = [], []
    for i in range(samples):
        try:
            sensor.measure()
            t = sensor.temperature()
            h = sensor.humidity()
            log("Citire[{}]: T={} H={}".format(i+1, t, h))
            temps.append(t)
            hums.append(h)
        except Exception as e:
            log("Eroare senzor (proba {}): {}".format(i+1, e))
        time.sleep(delay_s)

    pwr_pin.value(0)
    Pin(SENSOR_DATA_PIN, Pin.IN)

    if temps and hums:
        avg_t = int(sum(temps)/len(temps) + 0.5)
        avg_h = int(sum(hums)/len(hums) + 0.5)
        log("Medie DHT11: T={} H={}".format(avg_t, avg_h))
        return avg_t, avg_h
    else:
        log("Nicio citire validă DHT11")
        return None, None

# ====================== Funcția principală ======================
def main():
    log("=== remote.py start === ID={} ROOM={}".format(DEVICE_ID, ROOM))
    cfg = fetch_config()
    t, h = read_dht()

    if t is not None and h is not None:
        log("SENZOR: T={} H={}".format(t, h))
        ok_ts = send_data(t, h)
        log("TS trimis ok?" + str(ok_ts))
        # Alerte
        if t >= cfg["alarm_temp"]:
            send_telegram("ALERTA TEMPERATURA - {}\nT={}C\nPrag={}C".format(ROOM, t, cfg["alarm_temp"]))
        if h >= cfg["alarm_hum"]:
            send_telegram("ALERTA UMIDITATE - {}\nUmiditate={} %\nPrag={} %".format(ROOM, h, cfg["alarm_hum"]))
    else:
        log("Citiri DHT invalide, nu trimit TS")

    # DEBUG → pauză scurtă
    if cfg.get("DEBUGGING") == 1:
        log("DEBUG mode – reiau în 10 sec")
        time.sleep(10)
        return

    # Sleep profund
    minutes = cfg.get("sleep_minutes", 5)
    log("Deep sleep {} minute".format(minutes))
    deepsleep(minutes * 60 * 1000)

# La import, rulează un ciclu
main()
