import network
import urequests
import time
import dht
from machine import Pin, deepsleep, WDT
import ujson

# ------------------------------------------------------------
#  ÎNCEARCĂM SĂ IMPORTĂM MQTT
# ------------------------------------------------------------
try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None

# ============================================================
#  remote.py v1.4 – Sistem senzori gospodărie + MQTT log
# ============================================================

SENSOR_PWR_PIN = 23   # pin care alimentează DHT11
SENSOR_DATA_PIN = 4   # pin de date

pwr_pin = Pin(SENSOR_PWR_PIN, Pin.OUT, value=0)  # senzor OPRIT inițial
data_pin = Pin(SENSOR_DATA_PIN, Pin.IN)

WIFI_SSID = "DIGI-Y4bX"
WIFI_PASS = "Burlusi166?"

CONFIG_CHANNEL = 1622205               # CONFIG – Gospodarie
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

# ------------------------------------------------------------
#  ID dispozitiv
# ------------------------------------------------------------
def get_device_id():
    try:
        import ubinascii, machine
        return ubinascii.hexlify(machine.unique_id()).decode().upper()
    except:
        return "UNKNOWN"

DEVICE_ID = get_device_id()
ROOM = DEVICE_INFO.get(DEVICE_ID, {}).get("name", "UNKNOWN")
INFO = DEVICE_INFO.get(DEVICE_ID, {})

print("=== remote.py v1.4 ===")
print("Device:", DEVICE_ID)
print("Camera:", ROOM)
print("INFO:", INFO)

# ================= MQTT LOG =====================

MQTT_ENABLED   = True          # dacă vrei, poți pune False temporar
MQTT_HOST      = "c72cc38c1f184d0199cc4daa938bac6f.s1.eu.hivemq.cloud"
MQTT_PORT      = 8883          # MQTT TLS (nu WebSocket)
MQTT_USER      = "burlusi"
MQTT_PASS      = "Burlusi166?"
MQTT_CLIENT_ID = b"esp32_" + DEVICE_ID.encode()
MQTT_BASE_TOPIC = b"home/" + DEVICE_ID.encode() + b"/"

mqtt_client = None

def mqtt_connect():
    """
    Conectare la HiveMQ Cloud, trimitem 'online' + mesaj de boot.
    Apelăm doar după ce avem WiFi.
    """
    global mqtt_client
    if not MQTT_ENABLED or MQTTClient is None:
        print("MQTT dezactivat sau librărie umqtt.simple lipsă.")
        return None
    try:
        c = MQTTClient(
            MQTT_CLIENT_ID,
            MQTT_HOST,
            port=MQTT_PORT,
            user=MQTT_USER,
            password=MQTT_PASS,
            ssl=True
        )
        c.connect()
        print("MQTT conectat.")
        mqtt_client = c
        try:
            c.publish(MQTT_BASE_TOPIC + b"status", b"online")
            c.publish(MQTT_BASE_TOPIC + b"log",
                      b"boot v1.4, room=" + ROOM.encode())
        except:
            pass
        return c
    except Exception as e:
        print("Eroare conectare MQTT:", e)
        mqtt_client = None
        return None

def mqtt_log(msg):
    """
    Trimite o linie de log la topicul home/<ID>/log.
    Nu aruncă excepții critice – doar dezactivează MQTT dacă ceva nu merge.
    """
    global mqtt_client
    if not MQTT_ENABLED or MQTTClient is None:
        return

    if isinstance(msg, str):
        payload = msg.encode()
    else:
        payload = bytes(str(msg), "utf-8")

    try:
        if mqtt_client is None:
            mqtt_client = mqtt_connect()
        if mqtt_client:
            mqtt_client.publish(MQTT_BASE_TOPIC + b"log", payload)
    except Exception as e:
        print("MQTT log error:", e)
        mqtt_client = None   # forțăm reconectarea la următorul ciclu

# ============================================================
# DAILY SUMMARY (Telegram)
# ============================================================

SUMMARY_FILE = "daily_summary.json"

daily_state = {
    "day_index": None,
    "min_t": None,
    "max_t": None,
    "min_h": None,
    "max_h": None,
    "alerts": 0,
}

def load_daily_state():
    global daily_state
    try:
        with open(SUMMARY_FILE, "r") as f:
            daily_state = ujson.loads(f.read())
        print("Daily state încărcat:", daily_state)
    except Exception as e:
        print("Nu am putut citi daily_state, folosesc valori default:", e)
        daily_state = {
            "day_index": None,
            "min_t": None,
            "max_t": None,
            "min_h": None,
            "max_h": None,
            "alerts": 0,
        }

def save_daily_state():
    try:
        with open(SUMMARY_FILE, "w") as f:
            f.write(ujson.dumps(daily_state))
    except Exception as e:
        print("Eroare salvare daily_state:", e)

def get_day_index():
    # număr de zile de la epoch – ne ajunge ca să detectăm schimbarea zilei
    return int(time.time() // 86400)

def send_daily_summary():
    if daily_state["min_t"] is None:
        return
    msg = (
        "Raport zilnic - {}\n"
        "Temp min: {} C\nTemp max: {} C\n"
        "Umiditate min: {} %\nUmiditate max: {} %\n"
        "Alerte trimise: {}"
    ).format(
        ROOM,
        daily_state["min_t"], daily_state["max_t"],
        daily_state["min_h"], daily_state["max_h"],
        daily_state["alerts"],
    )
    send_telegram(msg)
    mqtt_log("Raport zilnic trimis.")

def update_daily_stats(t, h, alerts_this_cycle):
    """
    Actualizează min/max pe zi + nr. de alerte.
    Când se schimbă ziua, trimite raport și resetează.
    """
    global daily_state
    day_idx = get_day_index()

    # prima oară: inițializăm
    if daily_state["day_index"] is None:
        daily_state["day_index"] = day_idx
        daily_state["min_t"] = t
        daily_state["max_t"] = t
        daily_state["min_h"] = h
        daily_state["max_h"] = h
        daily_state["alerts"] = alerts_this_cycle
        save_daily_state()
        return

    # zi nouă → trimitem raport pentru ziua trecută + reset
    if day_idx != daily_state["day_index"]:
        send_daily_summary()
        daily_state["day_index"] = day_idx
        daily_state["min_t"] = t
        daily_state["max_t"] = t
        daily_state["min_h"] = h
        daily_state["max_h"] = h
        daily_state["alerts"] = alerts_this_cycle
        save_daily_state()
        return

    # aceeași zi → doar actualizăm
    if t is not None:
        if daily_state["min_t"] is None or t < daily_state["min_t"]:
            daily_state["min_t"] = t
        if daily_state["max_t"] is None or t > daily_state["max_t"]:
            daily_state["max_t"] = t

    if h is not None:
        if daily_state["min_h"] is None or h < daily_state["min_h"]:
            daily_state["min_h"] = h
        if daily_state["max_h"] is None or h > daily_state["max_h"]:
            daily_state["max_h"] = h

    daily_state["alerts"] += alerts_this_cycle
    save_daily_state()

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
            mqtt_log("WiFi FAIL – intru în deep sleep 60s")
            deepsleep(60000)
        time.sleep(0.3)

    cfg = wlan.ifconfig()
    print("WiFi OK:", cfg)
    mqtt_log("WiFi OK: " + str(cfg))
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

        # valori globale
        cfg["sleep_minutes"] = last_non_empty_int("field1", 30)
        cfg["DEBUGGING"]     = last_non_empty_int("field8", 1)

        # praguri specifice device-ului
        info = INFO or {}
        cfg_fields = info.get("config_fields", {})
        print("cfg_fields pentru", ROOM, ":", cfg_fields)

        temp_field = cfg_fields.get("alarm_temp", "field2")  # ex: 'field6'
        hum_field  = cfg_fields.get("alarm_hum",  "field3")  # ex: 'field7'

        cfg["alarm_temp"] = last_non_empty_int(temp_field, 25)
        cfg["alarm_hum"]  = last_non_empty_int(hum_field, 60)

        print("SETARI:", cfg)
        mqtt_log("Config: " + str(cfg))
        return cfg

    except Exception as e:
        print("Eroare CONFIG:", e)
        mqtt_log("Eroare CONFIG: " + str(e))
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
    mqtt_log("TS URL: " + base_url)

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
            mqtt_log("TS resp ({}): {}".format(attempt + 1, resp))

            if resp != "0":
                return True

            print("TS[{}]: răspuns 0 (nu a acceptat). Reîncerc...".format(ROOM))

        except Exception as e:
            print("TS[{}]: Eroare DATA (încercarea {}): {}".format(
                ROOM, attempt + 1, e
            ))
            mqtt_log("TS eroare ({}): {}".format(attempt + 1, e))

    print("TS[{}]: am renunțat după 3 încercări fără succes.".format(ROOM))
    mqtt_log("TS: 3 încercări fără succes.")
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
        mqtt_log("Eroare TG: " + str(e))

# ============================================================
# 9. Citire DHT cu medie pe mai multe probe
# ============================================================

def read_dht(samples=5, delay_s=1):
    """
    Citește DHT11 de 'samples' ori și întoarce media, cu alimentare on/off.
    """
    # 1. Pornește alimentarea senzorului
    pwr_pin.value(1)
    time.sleep(2)  # DHT11 are nevoie de ~1–2s să se stabilizeze

    sensor = dht.DHT11(Pin(SENSOR_DATA_PIN))

    temps = []
    hums  = []

    for i in range(samples):
        try:
            sensor.measure()
            t = sensor.temperature()
            h = sensor.humidity()
            print("Citire[{}]: T={} H={}".format(i+1, t, h))

            if t is not None and h is not None:
                temps.append(t)
                hums.append(h)
        except Exception as e:
            print("Eroare senzor (proba {}): {}".format(i+1, e))

        time.sleep(delay_s)

    # 3. Oprește alimentarea senzorului
    pwr_pin.value(0)
    Pin(SENSOR_DATA_PIN, Pin.IN)

    # 4. Calculăm media, dacă avem măcar o citire validă
    if temps and hums:
        avg_t = int(sum(temps) / len(temps) + 0.5)
        avg_h = int(sum(hums)  / len(hums)  + 0.5)
        print("Medie DHT11: T={} H={}".format(avg_t, avg_h))
        mqtt_log("DHT medie: T={} H={}".format(avg_t, avg_h))
        return avg_t, avg_h
    else:
        print("Nicio citire DHT11 validă.")
        mqtt_log("DHT: nicio citire validă.")
        return None, None

# ============================================================
# 10. Pornim – citim daily_state din fișier
# ============================================================

load_daily_state()

# ============================================================
# 11. Program principal
# ============================================================

while True:

    wdt.feed()
    connect_wifi()

    # la fiecare boot/ciclu încercăm să avem client MQTT
    if MQTT_ENABLED and MQTTClient is not None and (mqtt_client is None):
        mqtt_connect()

    cfg = fetch_config()
    t, h = read_dht()

    alerts_this_cycle = 0   # câte alerte trimitem în ciclul curent

    if t is not None and h is not None:
        print("SENZOR:", ROOM, "T=", t, "H=", h)
        mqtt_log("Senzor: T={} H={}".format(t, h))

        ok_ts = send_data(t, h)
        print("TS trimis pentru", ROOM, "ok?", ok_ts)

        # Alerte temperatură / umiditate
        if t >= cfg["alarm_temp"]:
            send_telegram(
                "ALERTA TEMPERATURA - {}\nTemperatura: {} C\nPrag: {} C".format(
                    ROOM, t, cfg["alarm_temp"]
                )
            )
            mqtt_log("ALERTA TEMP: T={}".format(t))
            alerts_this_cycle += 1

        if h >= cfg["alarm_hum"]:
            send_telegram(
                "ALERTA UMIDITATE - {}\nUmiditate: {} %\nPrag: {} %".format(
                    ROOM, h, cfg["alarm_hum"]
                )
            )
            mqtt_log("ALERTA HUM: H={}".format(h))
            alerts_this_cycle += 1

        # actualizăm statisticile zilnice (min/max + nr. alerte)
        update_daily_stats(t, h, alerts_this_cycle)
    else:
        print("Nu am citire DHT validă, sar peste update_daily_stats")
        mqtt_log("Citire DHT invalidă în acest ciclu.")

    # DEBUGGING → fără deep sleep, doar pauză scurtă
    if cfg["DEBUGGING"] == 1:
        print("DEBUG → reluare 10 sec (soft)")
        mqtt_log("DEBUG loop, fără deep sleep.")
        for _ in range(30):
            time.sleep(1)
            wdt.feed()
        continue

    # PRODUCȚIE – folosim deep sleep
    minutes = cfg.get("sleep_minutes", 5)
    print("Sleep:", minutes, "minute (deep sleep)")
    mqtt_log("Deep sleep {} minute".format(minutes))
    deepsleep(int(minutes * 60 * 1000))
