# mod_dht.py  (MicroPython / ESP32)
import time
import dht
from machine import Pin

_pwr_pin = None
_data_pin_num = None
_log = None
_wdt = None

def init(pwr_pin_num, data_pin_num, log_fn=None, wdt=None):
    """
    Inițializează modulul DHT.
    - pwr_pin_num: pin care alimentează senzorul (ON/OFF)
    - data_pin_num: pin DATA pentru DHT11
    - log_fn: funcție log(...) din remote.py
    - wdt: watchdog (opțional) ca să feed-uim în loop
    """
    global _pwr_pin, _data_pin_num, _log, _wdt
    _pwr_pin = Pin(pwr_pin_num, Pin.OUT, value=0)
    _data_pin_num = int(data_pin_num)
    _log = log_fn
    _wdt = wdt

def _logit(msg):
    if _log:
        try:
            _log(msg)
            return
        except:
            pass
    print("DHT:", msg)

def read(samples=5, delay_s=1):
    """
    Citește DHT11 de mai multe ori și întoarce media (rotunjită).
    Return: (t, h) sau (None, None)
    """
    if _pwr_pin is None or _data_pin_num is None:
        raise RuntimeError("mod_dht.init(...) nu a fost apelat")

    _logit("DHT: power ON")
    _pwr_pin.value(1)
    time.sleep(2)

    sensor = dht.DHT11(Pin(_data_pin_num, Pin.IN))
    temps = []
    hums  = []

    for i in range(1, samples + 1):
        try:
            if _wdt:
                _wdt.feed()
            sensor.measure()
            t = sensor.temperature()
            h = sensor.humidity()
            _logit("DHT read {}/{}: T={} H={}".format(i, samples, t, h))

            if t is not None and h is not None:
                temps.append(t)
                hums.append(h)
        except Exception as e:
            _logit("DHT ERROR sample {}: {}".format(i, e))

        time.sleep(delay_s)

    # high-Z înainte de OFF (ca să nu alimentezi prin DATA)
    Pin(_data_pin_num, Pin.IN)
    _pwr_pin.value(0)
    _logit("DHT: power OFF")

    if temps and hums:
        avg_t = int(sum(temps) / len(temps) + 0.5)
        avg_h = int(sum(hums)  / len(hums)  + 0.5)
        _logit("DHT AVG: T={} H={}".format(avg_t, avg_h))
        return avg_t, avg_h

    _logit("DHT: no valid reads")
    return None, None
