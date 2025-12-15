# remote.py v2.1 (clean + logs)
import time
import network
import urequests
import dht
import ubinascii
import machine
from machine import Pin, WDT, deepsleep
import gc
THINGSPEAK_BASE = "http://api.thingspeak.com"  # IMPORTANT: fără TLS ca să nu mai pice cu -17040

# ===================== LOG (merge în Dashboard prin main.py) =====================
def log(msg):
    s = "REMOTE: " + str(msg)
    try:
        import __main__
        __main__.publish_log(s)     # asta e funcția din main.py (queue -> MQTT)
    except:
        print(s)                   # fallback în Thonny
log("logger OK (remote -> dashboard)")


# ============================== CONFIG ==============================
SENSOR_PWR_PIN  = 23
SENSOR_DATA_PIN = 4

WIFI_SSID = "DIGI-Y4bX"
WIFI_PASS = "Burlusi166?"

CONFIG_CHANNEL      = 1622205
DATA_CHANNEL_API_KEY = "ZPT57WZJNMLGM2X1"
last_ts_update = 0

TELEGRAM_BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
TELEGRAM_CHAT_ID   = "1705327493"

DEVICE_INFO = {
    "EC62609C8900": {
        "name": "Camara",
        "config_fields": {"alarm_temp": "field2", "alarm_hum": "field3"},
        "data_fields":   {"temp": "field1", "hum":  "field2"},
    },
    "7821849F8900": {
        "name": "Bucatarie",
        "config_fields": {"alarm_temp": "field6", "alarm_hum": "field7"},
        "data_fields":   {"temp": "field5", "hum":  "field6"},
    },
    "XXXXXXXXXXXX": {
        "name": "Baie",
        "config_fields": {"alarm_temp": "field4", "alarm_hum": "field5"},
        "data_fields":   {"temp": "field3", "hum":  "field4"},
    },
}

# ===================== HW init =====================
pwr_pin  = Pin(SENSOR_PWR_PIN, Pin.OUT, value=0)
data_pin = Pin(SENSOR_DATA_PIN, Pin.IN)

wdt = WDT(timeout=60000)

def get_device_id():
    try:
        return ubinascii.hexlify(machine.unique_id()).decode().upper()
    except:
        return "UNKNOWN"

DEVICE_ID   = get_device_id()
INFO        = DEVICE_INFO.get(DEVICE_ID, {})
ROOM        = INFO.get("name", "UNKNOWN")
cfg_fields  = INFO.get("config_fields", {})
data_fields = INFO.get("data_fields", {})

# ===================== SAFE MODE (buton BOOT) =====================
boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
if boot_btn.value() == 0:
    log("SAFE MODE – BOOT apăsat, remote.py NU rulează")
    while True:
        time.sleep(1)

# ===================== WiFi (dacă a picat) =====================
def ensure_wifi(max_attempts=3, wait_s=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        try:
            ip = wlan.ifconfig()[0]
        except:
            ip = "?"
        log("WiFi OK (deja): {}".format(ip))
        return True

    for attempt in range(1, max_attempts + 1):
        log("WiFi connect attempt {}/{}".format(attempt, max_attempts))
        try:
            try:
                wlan.disconnect()
            except:
                pass
            wlan.connect(WIFI_SSID, WIFI_PASS)

            t0 = time.time()
            while not wlan.isconnected() and (time.time() - t0) < wait_s:
                wdt.feed()
                time.sleep(0.3)

            if wlan.isconnected():
                ip = wlan.ifconfig()[0]
                log("WiFi OK: {}".format(ip))
                return True

            log("WiFi FAIL attempt {}".format(attempt))
        except Exception as e:
            log("WiFi EXC attempt {}: {}".format(attempt, e))

        time.sleep(1)

    return False

# ===================== ThingSpeak CONFIG =====================
def fetch_config():
    url = THINGSPEAK_BASE + "/channels/{}/feeds.json?results=20".format(CONFIG_CHANNEL)
    log("CONFIG fetch: {}".format(url))

    # defaults (dacă pică netul)
    cfg = {
        "sleep_minutes": 30,
        "DEBUGGING": 1,
        "alarm_temp": 25,
        "alarm_hum": 60,
    }

    try:
        r = urequests.get(url)
        js = r.json()
        r.close()

        feeds = js.get("feeds", [])
        if not feeds:
            raise ValueError("CONFIG gol")

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

        cfg["sleep_minutes"] = last_non_empty_int("field1", cfg["sleep_minutes"])
        cfg["DEBUGGING"]     = last_non_empty_int("field8", cfg["DEBUGGING"])

        temp_field = cfg_fields.get("alarm_temp", "field2")
        hum_field  = cfg_fields.get("alarm_hum",  "field3")

        cfg["alarm_temp"] = last_non_empty_int(temp_field, cfg["alarm_temp"])
        cfg["alarm_hum"]  = last_non_empty_int(hum_field,  cfg["alarm_hum"])

        log("CONFIG OK: {}".format(cfg))
        return cfg

    except Exception as e:
        log("CONFIG ERROR: {}".format(e))
        return cfg

# ===================== ThingSpeak DATA =====================
def send_data(temp, hum):
    global last_ts_update

    f_temp = data_fields.get("temp", "field1")
    f_hum  = data_fields.get("hum",  "field2")

    # throttle ThingSpeak ~15s între UPDATE-uri
    now = time.time()
    if now - last_ts_update < 16:
        wait_s = int(16 - (now - last_ts_update))
        log("TS throttle: wait {}s".format(wait_s))
        for _ in range(wait_s):
            time.sleep(1)
            wdt.feed()

    url = THINGSPEAK_BASE + "/update"
    body = "api_key={}&{}={}&{}={}".format(DATA_CHANNEL_API_KEY, f_temp, temp, f_hum, hum)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    for attempt in range(3):
        r = None
        try:
            gc.collect()
            log("TS POST (try {}/3): {}={} {}={}".format(attempt+1, f_temp, temp, f_hum, hum))
            r = urequests.post(url, data=body, headers=headers)
            resp = r.text.strip()
            last_ts_update = time.time()
            log("TS RESP: {}".format(resp))
            if resp != "0":
                return True
        except Exception as e:
            log("TS ERROR (try {}/3): {}".format(attempt+1, repr(e)))
            time.sleep(2)
        finally:
            try:
                if r:
                    r.close()
            except:
                pass
            gc.collect()

    return False

# ===================== Telegram =====================
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
        return False

    body = "chat_id={}&text={}".format(TELEGRAM_CHAT_ID, urlenc(msg))
    url  = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_BOT_TOKEN)

    try:
        r = urequests.post(url, data=body, headers={"Content-Type":"application/x-www-form-urlencoded"})
        r.close()
        log("TG OK")
        return True
    except Exception as e:
        log("TG ERROR: {}".format(e))
        return False

# ===================== DHT11 (medie) =====================
def read_dht(samples=5, delay_s=1):
    log("DHT: power ON")
    pwr_pin.value(1)
    time.sleep(2)

    sensor = dht.DHT11(Pin(SENSOR_DATA_PIN, Pin.IN))
    temps = []
    hums  = []

    for i in range(1, samples + 1):
        try:
            wdt.feed()
            sensor.measure()
            t = sensor.temperature()
            h = sensor.humidity()
            log("DHT read {}/{}: T={} H={}".format(i, samples, t, h))

            if t is not None and h is not None:
                temps.append(t)
                hums.append(h)
        except Exception as e:
            log("DHT ERROR sample {}: {}".format(i, e))

        time.sleep(delay_s)

    # IMPORTANT: data high-Z înainte de OFF ca să nu alimentezi prin DATA
    Pin(SENSOR_DATA_PIN, Pin.IN)
    pwr_pin.value(0)
    log("DHT: power OFF")

    if temps and hums:
        avg_t = int(sum(temps) / len(temps) + 0.5)
        avg_h = int(sum(hums)  / len(hums)  + 0.5)
        log("DHT AVG: T={} H={}".format(avg_t, avg_h))
        return avg_t, avg_h

    log("DHT: no valid reads")
    return None, None

# ===================== LOOP principal (nu iese) =====================
log("start | ID={} | ROOM={} | cfg_fields={} | data_fields={}".format(
    DEVICE_ID, ROOM, cfg_fields, data_fields
))

while True:
    try:
        wdt.feed()
        log("cycle: begin")

        if not ensure_wifi():
            log("cycle: no wifi -> sleep 10s")
            time.sleep(10)
            continue

        cfg = fetch_config()
        t, h = read_dht()

        if t is None or h is None:
            log("cycle: DHT invalid -> skip TS/alerts")
        else:
            log("cycle: sensor OK T={} H={}".format(t, h))
            ok_ts = send_data(t, h)
            log("cycle: TS ok={}".format(ok_ts))

            # anti-spam: max 1 alertă / ciclu (temp are prioritate)
            if t >= cfg["alarm_temp"]:
                send_telegram("ALERTA TEMPERATURA - {}\nT={}C\nPrag={}C".format(ROOM, t, cfg["alarm_temp"]))
            elif h >= cfg["alarm_hum"]:
                send_telegram("ALERTA UMIDITATE - {}\nH={} %\nPrag={} %".format(ROOM, h, cfg["alarm_hum"]))

        if cfg.get("DEBUGGING", 1) == 1:
            log("cycle: DEBUG=1 -> wait 10s")
            time.sleep(10)
            continue

        minutes = int(cfg.get("sleep_minutes", 5))
        log("cycle: deep sleep {} min".format(minutes))
        deepsleep(minutes * 60 * 1000)

    except Exception as e:
        log("CRASH: {}".format(e))
        time.sleep(5)

