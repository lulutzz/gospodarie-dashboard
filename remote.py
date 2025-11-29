"""
============================================================
   ESP32 – Sistem Monitorizare Încăperi (Remote Script)
   Versiune: 1.0.0
   Data: 2025-01-10
   Autor: Laceanu Ionel + ChatGPT

   DESCRIERE GENERALĂ
   ---------------------------------------------------------
   Acest fișier remote.py este gândit să fie descărcat și
   executat dinamic pe un ESP32 (de exemplu, din GitHub),
   pentru mai multe camere (Cămară, Baie, Bucătărie etc.).

   Ce face:
     • Detectează automat dispozitivul prin MAC (device_id)
     • Alege numele camerei și canalul de CONFIG ThingSpeak
       în funcție de device_id
     • Citește parametrii de configurare din ThingSpeak:
         - sleep_minutes
         - alarm_temp
         - alarm_hum
         - sampling_count
         - DEBUGGING
     • Măsoară temperatura + umiditate (DHT11)
     • Trimite media pe ThingSpeak (canal de date)
     • Trimite alerte personalizate pe Telegram
     • Folosește watchdog (WDT) pentru siguranță
     • Folosește deep sleep în producție (DEBUGGING=0)

   PARAMETRI CONFIGURABILI (ThingSpeak – Channel CONFIG)
   ---------------------------------------------------------
     field1 → sleep_minutes       (1–180 minute)
     field2 → alarm_temp          (prag alertă temperatură, °C)
     field3 → alarm_hum           (prag alertă umiditate, %)
     field4 → sampling_count      (1–10 citiri pentru medie)
     field5 → DEBUGGING           (1=debug, 0=producție)

   NOTĂ:
   ---------------------------------------------------------
   • Momentan doar CĂMARĂ are device_id cunoscut.
   • Pentru BAIE și BUCĂTĂRIE se vor completa MAC-urile
     când vor fi disponibile.
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

# --- WiFi (aceleași ca în main.py) ---
SSID = "DIGI-Y4bX"
PASSWORD = "Burlusi166?"

# --- Telegram (TOKEN + CHAT_ID hard-codate) ---
BOT_TOKEN = "8532839048:AAEznUxSlaUMeNBmxZ0aFT_8vCHnlNqJ4dI"
CHAT_ID   = "1705327493"

# --- ThingSpeak: CANAL DATE (temperature + humidity) ---
DATA_BASE_URL_DEFAULT = "https://api.thingspeak.com/update"

# ================================
# Mapare DEVICE → cameră + CONFIG
# ================================
#
#   device_id = MAC-ul ESP32 în hex, ex: "EC62609C8900"
#   name      = numele camerei (folosit în mesaje)
#   config_channel = Channel ID în ThingSpeak pentru CONFIG
#   data_api_key   = WRITE API KEY pentru canalul de DATE
#
DEVICE_INFO = {
    # CĂMARĂ (Camera rece) – COMPLET FUNCȚIONALĂ
    "EC62609C8900": {
        "name": "Cămară",
        "config_channel": 1622205,
        "data_api_key": "XP7PSBXSVN3CXWKQ"
    },

    # BAIE – COMPLETĂM MAC + API KEY când senzorul va fi instalat
    "MAC_PENTRU_BAIE": {
        "name": "Baie",
        "config_channel": 3186869,
        "data_api_key": "API_KEY_PENTRU_BAIE"
    },

    # BUCĂTĂRIE – COMPLETĂM MAC + API KEY când senzorul va fi instalat
    "MAC_PENTRU_BUCATARIE": {
        "name": "Bucătărie",
        "config_channel": 1638468,
        "data_api_key": "API_KEY_PENTRU_BUCATARIE"
    }
}

# Dacă device_id nu este în listă, folosim un fallback generic
FALLBACK_CONFIG_CHANNEL = 1622205      # poți schimba la nevoie
FALLBACK_NAME           = "Dispozitiv necunoscut"
FALLBACK_API_KEY        = "XP7PSBXSVN3CXWKQ"

# --- Senzor DHT11 pe GPIO4 ---
sensor = dht.DHT11(Pin(4))

# ============================================================
#   2. SAFE MODE (dacă ții BOOT apăsat → nu rulează)
# ============================================================

boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
if boot_btn.value() == 0:
    print("=== SAFE MODE (remote.py) ===")
    print("Nu rulez logica de măsurare. Poți programa placa din Thonny.")
    while True:
        time.sleep(1)

# ============================================================
#   3. WATCHDOG (dacă se blochează → restart hardware)
# ============================================================

wdt = WDT(timeout=60000)   # 60 secunde


# ============================================================
#   4. FUNCȚII UTILE – WiFi, Config, ThingSpeak, Telegram
# ============================================================

def get_device_id():
    """
    Returnează device_id pe baza MAC-ului WiFi.
    Exemplu: 'EC62609C8900'
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    mac = ubinascii.hexlify(wlan.config('mac')).decode().upper()
    return mac


def connect_wifi():
    """
    Conectează ESP32 la WiFi.
    Dacă nu reușește în 20s → reset soft (se reia tot).
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Conectare WiFi (remote.py)...")
        wlan.connect(SSID, PASSWORD)

        timeout = time.time() + 20
        while not wlan.isconnected():
            wdt.feed()
            time.sleep(0.3)
            if time.time() > timeout:
                print("WiFi FAIL în remote.py → reset soft")
                import machine
                machine.reset()

    print("Conectat la WiFi (remote.py):", wlan.ifconfig())


def fetch_config(config_channel_id):
    """
    Citește configurarea din ThingSpeak Channel CONFIG:
      field1 = sleep_minutes
      field2 = alarm_temp
      field3 = alarm_hum
      field4 = sampling_count
      field5 = DEBUGGING (1=debug, 0=producție)

    NOTĂ:
      - Canalul CONFIG trebuie să fie PUBLIC
        (sau se adaptează URL-ul pentru a folosi READ API KEY).
    Dacă ceva nu merge → folosește valori implicite.
    """
    cfg = {
        "sleep_minutes": 1,
        "alarm_temp": 25,
        "alarm_hum": 40,
        "sampling_count": 1,
        "DEBUGGING": 1,
    }

    try:
        url = (
            "https://api.thingspeak.com/channels/{}/feeds.json"
            "?results=1"
        ).format(config_channel_id)

        print("Citire CONFIG din canal:", config_channel_id)
        print("URL:", url)

        r = urequests.get(url)
        data = r.json()
        r.close()

        # ATENȚIE: aici înainte era bug (feeds = []); acum e corect.
        feeds = data.get("feeds", [])
        if not feeds:
            print("CONFIG gol → folosesc DEFAULT:", cfg)
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

        # Limite de siguranță
        cfg["sleep_minutes"]  = max(1, min(180, cfg["sleep_minutes"]))
        cfg["sampling_count"] = max(1, min(10,  cfg["sampling_count"]))
        cfg["DEBUGGING"]      = 1 if cfg["DEBUGGING"] >= 1 else 0

        print("CONFIG aplicat:", cfg)
        return cfg

    except Exception as e:
        print("Eroare citire CONFIG:", e)
        print("Folosesc DEFAULT:", cfg)
        return cfg


def send_to_thingspeak(temp, hum, api_key):
    """
    Trimite temperatura și umiditatea medie în canalul de DATE.
    api_key = WRITE API KEY pentru canalul de date.
    """
    try:
        url = "{}?api_key={}&field1={}&field2={}".format(
            DATA_BASE_URL_DEFAULT, api_key, temp, hum
        )
        r = urequests.get(url)
        print("Trimis ThingSpeak:", r.text)
        r.close()
    except Exception as e:
        print("Eroare trimitere ThingSpeak:", e)


def send_telegram(msg):
    """
    Trimite mesaj la Telegram folosind BOT_TOKEN + CHAT_ID.
    Folosește application/x-www-form-urlencoded, fără JSON.
    FACE URL-ENCODE la text, deci evită erorile cu caractere speciale.
    Mesajul trebuie să fie ASCII (fără emoji/diacritice).
    """

    global BOT_TOKEN, CHAT_ID

    # 1) Verificări de bază
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Telegram dezactivat (token/chat_id lipsă)")
        return

    if msg is None:
        print("❌ Mesaj None, nu trimit")
        return

    msg = str(msg)
    if msg.strip() == "":
        print("❌ Mesaj gol, nu trimit")
        return

    # 2) Funcție simplă de URL-encode (ASCII safe)
    def url_encode(s):
        res = []
        for ch in s:
            o = ord(ch)
            # litere, cifre și câteva caractere sigure
            if (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122) or ch in "-_.~":
                res.append(ch)
            elif ch == " ":
                res.append("%20")
            elif ch == "\n":
                res.append("%0A")
            else:
                # orice altceva -> %HH
                res.append("%%%02X" % o)
        return "".join(res)

    encoded_text = url_encode(msg)

    # 3) Construim corpul cererii form-urlencoded
    body = "chat_id={}&text={}".format(CHAT_ID, encoded_text)

    url = "https://api.telegram.org/bot{}/sendMessage".format(BOT_TOKEN)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    print("DBG body =", body)  # debug, să vedem exact ce trimitem

    try:
        r = urequests.post(url, data=body, headers=headers)
        print("Telegram răspuns:", r.text)
        r.close()
    except Exception as e:
        print("Eroare trimitere Telegram:", e)


# ============================================================
#   5. PROGRAM PRINCIPAL
# ============================================================

print("=== Pornire remote.py – sistem senzori încăperi ===")

# 1) Determinăm device_id și camera
device_id = get_device_id()
print("Device ID detectat:", device_id)

info = DEVICE_INFO.get(device_id, None)
if info is None:
    print("⚠ Device ID necunoscut, folosesc fallback.")
    ROOM_NAME       = FALLBACK_NAME
    CONFIG_CHANNEL  = FALLBACK_CONFIG_CHANNEL
    DATA_API_KEY    = FALLBACK_API_KEY
else:
    ROOM_NAME       = info["name"]
    CONFIG_CHANNEL  = info["config_channel"]
    DATA_API_KEY    = info["data_api_key"]

print("Camera:", ROOM_NAME)
print("CONFIG Channel:", CONFIG_CHANNEL)

# Buclă principală (în producție e "tăiată" de deep sleep)
while True:

    wdt.feed()

    # 1) WiFi + Config
    connect_wifi()
    wdt.feed()
    cfg = fetch_config(CONFIG_CHANNEL)

    sleep_minutes  = cfg["sleep_minutes"]
    alarm_temp     = cfg["alarm_temp"]
    alarm_hum      = cfg["alarm_hum"]
    sampling_count = cfg["sampling_count"]
    DEBUGGING      = cfg["DEBUGGING"]

    print("\n=== SETĂRI ACTUALE ===")
    print("Camera         :", ROOM_NAME)
    print("DEBUGGING      :", DEBUGGING)
    print("sleep_minutes  :", sleep_minutes)
    print("sampling_count :", sampling_count)
    print("alarm_temp     :", alarm_temp)
    print("alarm_hum      :", alarm_hum)
    print("======================\n")

    # 2) Sampling multiplu DHT11
    temps = []
    hums  = []

    for i in range(sampling_count):
        try:
            sensor.measure()
            t = sensor.temperature()
            h = sensor.humidity()
            temps.append(t)
            hums.append(h)
            print("Citire", i+1, "/", sampling_count, "→ T:", t, "H:", h)
        except Exception as e:
            print("Eroare DHT:", e)

        wdt.feed()

        if i < sampling_count - 1:
            time.sleep(15)  # pauză între citiri

    # 3) Media temperatură + umiditate
    if temps and hums:
        avg_temp = sum(temps) / len(temps)
        avg_hum  = sum(hums)  / len(hums)
    else:
        avg_temp = None
        avg_hum  = None

    print("Media T:", avg_temp, "Media H:", avg_hum)

    # 4) Trimite date în ThingSpeak
    if avg_temp is not None and avg_hum is not None:
        send_to_thingspeak(round(avg_temp, 1), round(avg_hum, 0), DATA_API_KEY)

    wdt.feed()

    # 5) Alerte + Telegram (personalizate pe cameră)
    if avg_temp is not None and avg_temp >= alarm_temp:
        print("ALARMĂ TEMPERATURĂ!")
        msg = (
            "ALERTA TEMPERATURA – {}\n"
            "Temperatura: {} C\n"
            "Prag: {} C"
        ).format(ROOM_NAME, avg_temp, alarm_temp)
        send_telegram(msg)

    if avg_hum is not None and avg_hum >= alarm_hum:
        print("ALARMĂ UMIDITATE!")
        msg = (
            "ALERTA UMIDITATE – {}\n"
            "Umiditate: {} %\n"
            "Prag: {} %"
        ).format(ROOM_NAME, avg_hum, alarm_hum)
        send_telegram(msg)

    # 6) DEBUG vs PRODUCȚIE
    if DEBUGGING == 1:
        # MOD DEBUG:
        #  - nu intră în deep sleep
        #  - reia ciclul după 10 secunde
        print("\n=== MOD DEBUG ACTIV ===")
        print("Nu intru în deep sleep.")
        print("Reiau ciclul în 10 secunde...\n")
        time.sleep(10)
        continue   # reia while True de la început

    else:
        # MOD PRODUCȚIE:
        #  - intră în deep sleep
        #  - se trezește după sleep_minutes și reia tot
        print("Intră în deep sleep pentru", sleep_minutes, "minute...")
        time.sleep(0.2)
        wdt.feed()
        deepsleep(sleep_minutes * 60 * 1000)
