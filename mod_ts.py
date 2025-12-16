# mod_ts.py
import time
import urequests
import gc

_last_update = 0

def _last_non_empty_int(feeds, field, default):
    for f in reversed(feeds):
        v = f.get(field)
        if v is None or v == "":
            continue
        try:
            return int(float(v))
        except:
            return default
    return default

def fetch_config(config_channel, cfg_fields, log):
    """
    Citește CONFIG din ThingSpeak și întoarce dict:
      sleep_minutes, DEBUGGING, alarm_temp, alarm_hum
    cfg_fields = {"alarm_temp":"field6", "alarm_hum":"field7"} etc.
    """
    url = "https://api.thingspeak.com/channels/{}/feeds.json?results=20".format(config_channel)
    log("TS CONFIG fetch: {}".format(url))

    gc.collect()
    r = None
    try:
        r = urequests.get(url)
        js = r.json()
        feeds = js.get("feeds", [])
        if not feeds:
            raise ValueError("CONFIG gol")

        cfg = {}
        cfg["sleep_minutes"] = _last_non_empty_int(feeds, "field1", 30)
        cfg["DEBUGGING"]     = _last_non_empty_int(feeds, "field8", 1)

        tf = cfg_fields.get("alarm_temp", "field2")
        hf = cfg_fields.get("alarm_hum",  "field3")
        cfg["alarm_temp"] = _last_non_empty_int(feeds, tf, 25)
        cfg["alarm_hum"]  = _last_non_empty_int(feeds, hf, 60)

        log("TS CONFIG OK: {}".format(cfg))
        return cfg

    except Exception as e:
        log("TS CONFIG ERROR: {}".format(e))
        return {"sleep_minutes": 30, "DEBUGGING": 1, "alarm_temp": 25, "alarm_hum": 60}

    finally:
        try:
            if r: r.close()
        except:
            pass


def send_data(api_key, data_fields, temp, hum, wdt, log):
    """
    Trimite în DATA channel, cu throttling + retry.
    data_fields = {"temp":"field5","hum":"field6"} etc.
    """
    global _last_update

    f_temp = data_fields.get("temp", "field1")
    f_hum  = data_fields.get("hum",  "field2")

    url = "https://api.thingspeak.com/update?api_key={}&{}={}&{}={}".format(
        api_key, f_temp, temp, f_hum, hum
    )
    log("TS DATA url: {}".format(url))

    for attempt in range(1, 4):
        now = time.time()
        diff = now - _last_update
        if diff < 16:
            wait_s = int(16 - diff)
            log("TS throttle: wait {}s (try {}/3)".format(wait_s, attempt))
            for _ in range(wait_s):
                time.sleep(1)
                wdt.feed()

        gc.collect()
        r = None
        try:
            r = urequests.get(url)
            resp = (r.text or "").strip()
            _last_update = time.time()
            log("TS RESP (try {}/3): {}".format(attempt, resp))
            if resp != "0":
                return True
        except Exception as e:
            log("TS ERROR (try {}/3): {}".format(attempt, e))
        finally:
            try:
                if r: r.close()
            except:
                pass

    return False
