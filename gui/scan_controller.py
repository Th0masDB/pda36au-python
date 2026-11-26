import sys
import threading
import csv
import io
from contextlib import suppress
from functools import partial
from math import isfinite
from pathlib import Path

import numpy as np


# ---------------------------------------------------------
# Hoofdmap toevoegen aan Python-pad
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from config import I2C_BUS, DAC_ADDRESS
from gui.scan_timing import (
    DEFAULT_TIMING_CODE, Acquisition, MeasurementStopped, benchmark, measure_usb, step_rows,
)

# ---------------------------------------------------------
# Simulatie of echte hardware
# ---------------------------------------------------------

SIMULATIE = "--sim" in sys.argv or "--simulatie" in sys.argv
PDA_CONNECTED = "--PDACon" in sys.argv


if SIMULATIE and not PDA_CONNECTED:
    # DAC en PDA simuleren
    from simulation import SMBus, i2c_msg, PDA36AU

elif SIMULATIE and PDA_CONNECTED:
    # DAC simuleren, echte PDA gebruiken
    from simulation import SMBus, i2c_msg
    from pda36au import PDA36AU

else:
    # Echte DAC en echte PDA
    from smbus2 import SMBus, i2c_msg
    from pda36au import PDA36AU


# ---------------------------------------------------------
# Gegevens van de huidige scan
# ---------------------------------------------------------

punten = []
scan_y_unit = None
scan_settings = None
timing = {"benchmark": [], "scan": []}
scan_background = {"value": None, "sigma": None, "y_unit": "", "settings": None,
                   "status": "Geen achtergrondscan", "points": 0}

status = "Gereed"
running = False
error = ""

lock = threading.Lock()
dac_lock = threading.Lock()
stop_event = threading.Event()
live_stop_event = threading.Event()
live_running = False
live_status = "Gereed"
live_error = ""
live_block = {"block": None, "data": [], "analog_data": [],
              "x_unit": "sample", "y_unit": "", "sequence": 0}
background = {"value": None, "sigma": None, "y_unit": "", "target": 0, "blocks": 0,
              "sum": 0.0, "samples": 0, "status": "Geen achtergrondmeting"}


def start_background(aantal_blokken=1):
    """Meet de achtergrond zelfstandig of via de al actieve live uitlezing."""
    global live_running, live_status, live_error
    aantal_blokken = int(aantal_blokken)
    if not 1 <= aantal_blokken <= 100:
        raise ValueError("Kies 1 tot 100 achtergrondblokken.")
    with lock:
        if running or (live_running and live_stop_event.is_set()):
            return False
        if background["target"]:
            return False
        background.update(target=aantal_blokken, blocks=0, sum=0.0, samples=0, mean=0.0, m2=0.0,
                          status="Achtergrond meten...")
        standalone = not live_running
        if standalone:
            live_stop_event.clear()
            live_running = True
            live_status = "PDA verbinden voor achtergrondmeting..."
            live_error = ""
    if standalone:
        threading.Thread(target=_read_live, kwargs={"background_only": True}, daemon=True).start()
    return True


def clear_background():
    with lock:
        background.update(value=None, sigma=None, y_unit="", target=0, blocks=0,
                          sum=0.0, samples=0, status="Geen achtergrondmeting")


def publish_live_block(block, *, update_scan_unit=False):
    """Bewaar het nieuwste meetblok voor de volgende grafiekupdate."""
    global scan_y_unit

    unit = getattr(block, "y_unit", "gesimuleerd")
    with lock:
        live_block.update(block=block, data=None, analog_data=None,
                          x_unit=getattr(block, "x_unit", "sample"),
                          y_unit=unit,
                          sequence=live_block["sequence"] + 1)
        if update_scan_unit:
            scan_y_unit = unit
        if background["target"]:
            if background["blocks"] and unit != background["measurement_unit"]:
                background.update(target=0, status="Achtergrondmeting afgebroken: eenheid gewijzigd")
                return
            values = np.asarray(block.detector)
            finite = values[np.isfinite(values)]
            # Combineer blokstatistieken stabiel, zonder alle samples te bewaren.
            if finite.size:
                block_mean = float(np.mean(finite))
                delta = block_mean - background["mean"]
                count = background["samples"] + finite.size
                background["m2"] += (float(np.sum((finite - block_mean) ** 2))
                                     + delta ** 2 * background["samples"] * finite.size / count)
                background["mean"] += delta * finite.size / count
            background["measurement_unit"] = unit
            background["sum"] += float(np.sum(finite))
            background["samples"] += int(finite.size)
            background["blocks"] += 1
            background["status"] = f'Achtergrond meten: {background["blocks"]}/{background["target"]} blokken'
            if background["blocks"] >= background["target"]:
                if background["samples"]:
                    background["value"] = background["sum"] / background["samples"]
                    background["sigma"] = (float(np.sqrt(max(0.0, background["m2"])
                                                          / (background["samples"] - 1)))
                                           if background["samples"] > 1 else None)
                    background["y_unit"] = unit
                    background["status"] = f'Achtergrond gemeten ({background["blocks"]} blokken)'
                else:
                    background["status"] = "Achtergrondmeting mislukt: geen geldige samples"
                background["target"] = 0


def start_live():
    """Start PDA-uitlezing zonder de DAC te openen of te wijzigen."""
    global live_running, live_status, live_error
    with lock:
        if running or live_running:
            return False
        live_stop_event.clear()
        live_running = True
        live_status = "PDA verbinden..."
        live_error = ""
    threading.Thread(target=_read_live, daemon=True).start()
    return True


def _read_live(background_only=False):
    global live_running, live_status, live_error
    try:
        with PDA36AU() as pda:
            pda.start_continuous(free_run=True)
            try:
                with lock:
                    live_status = "Achtergrond meten" if background_only else "Live meten"
                while not live_stop_event.is_set():
                    with lock:
                        measuring_background = bool(background["target"])
                        if background_only and not measuring_background:
                            live_stop_event.set()
                            break
                    block = pda.read_block(timeout=1.0, latest=not measuring_background)
                    if live_stop_event.is_set():
                        break
                    publish_live_block(block)
                    if not measuring_background:
                        live_stop_event.wait(0.05)
            finally:
                pda.stop_continuous()
        with lock:
            live_status = "Gestopt"
    except Exception as exc:
        with lock:
            live_error = f"{type(exc).__name__}: {exc}"
            live_status = "Fout tijdens live meting"
    finally:
        with lock:
            live_running = False
            if background["target"]:
                background.update(target=0, status="Achtergrondmeting afgebroken")


def stop_live():
    live_stop_event.set()


def _format_live_samples(values, x):
    values = np.asarray(values)
    if x is None:
        x = np.arange(len(values), dtype=float)

    length = min(len(x), len(values))
    values = values[:length]
    samples = np.column_stack((x[:length], values)).astype(float, copy=False).tolist()
    for index in np.flatnonzero(~np.isfinite(values)):
        samples[index][1] = None
    return samples


def get_live_data():
    with lock:
        snapshot = {**live_block, "running": live_running, "scanning": running,
                    "status": status if running else live_status,
                    "error": live_error,
                    "background": {key: background[key] for key in
                                   ("value", "sigma", "y_unit", "target", "status")}}

    block = snapshot.pop("block")
    if snapshot["data"] is None:
        x = getattr(block, "x", None)
        samples = {"data": _format_live_samples(block.detector, x),
                   "analog_data": _format_live_samples(block.analog, x)}
        snapshot.update(samples)
        with lock:
            if live_block["sequence"] == snapshot["sequence"]:
                live_block.update(samples)
    return snapshot


# ---------------------------------------------------------
# DAC instellen
# ---------------------------------------------------------

def set_dac(bus, value):
    """Zet de DAC op een waarde tussen 0 en 4095."""

    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(
        DAC_ADDRESS,
        [byte1, byte2],
    )

    bus.i2c_rdwr(msg)


def set_manual_dac(value):
    """Schrijf een DAC-waarde buiten een scan, ook tijdens live uitlezen."""
    if not isinstance(value, (int, float)) or not 0 <= value <= 4095 or value != int(value):
        raise ValueError("Voer een gehele DAC-waarde van 0 tot 4095 in.")
    code = int(value)

    with dac_lock:
        with lock:
            if running:
                raise RuntimeError("Stop eerst de scan om de DAC handmatig in te stellen.")
        try:
            with SMBus() as bus:
                bus.open(I2C_BUS)
                set_dac(bus, code)
        except OSError as exc:
            raise RuntimeError("DAC niet bereikbaar. Controleer de I²C-bus en aansluiting.") from exc
    return code


# ---------------------------------------------------------
# Cavity scan
# ---------------------------------------------------------

def measure_timing(reference_code=DEFAULT_TIMING_CODE):
    scan_cavity(timing_code=reference_code, timing_only=True)


def scan_cavity(
    step=50,
    delay=0,
    aantal_blokken=1,
    achtergrond=False,
    *,
    timing_code=DEFAULT_TIMING_CODE,
    timing_only=False,
):
    """Meet de DAC-feedback en voer daarna zo nodig de cavity-scan uit."""

    global status, running, error, scan_y_unit, scan_settings

    settings = (int(step), float(delay), int(aantal_blokken))
    if (not 1 <= step <= 4095 or not isfinite(delay) or delay < 0
            or not 1 <= aantal_blokken <= 100 or not 1 <= timing_code <= 4095):
        raise ValueError("Ongeldige scan- of timinginstellingen.")
    step, delay, aantal_blokken = settings
    timing_code = int(timing_code)
    background_points = []
    measurements = []

    # Laat een handmatige DAC-instelling voltooien voordat een scan start.
    with dac_lock, lock:
        if running or live_running:
            return

        timing["benchmark"] = []
        if not timing_only:
            timing["scan"] = []
            if achtergrond:
                scan_background.update(status="Achtergrond scannen...", points=0)
            else:
                punten.clear()
                scan_y_unit = None
                scan_settings = settings
        status = "Timing meten..."
        stop_event.clear()
        running = True
        error = ""

    try:
        if SIMULATIE and PDA_CONNECTED:
            raise RuntimeError("Gebruik --sim voor een gesimuleerde scan. Voor een echte scan moet kanaal 2 het DAC-signaal meten.")
        with SMBus() as bus:
            bus.open(I2C_BUS)
            write_dac = partial(set_dac, bus)
            try:
                with PDA36AU() as pda:
                    gain = pda.get_gain_db()
                    usb_ms = measure_usb(pda, stop_event)
                    write_dac(0)
                    pda.start_continuous(free_run=True)
                    try:
                        capture = Acquisition(
                            pda, write_dac, stop_event, partial(
                                publish_live_block, update_scan_unit=not timing_only and not achtergrond))
                        calibration, rows = benchmark(capture, timing_code, usb_ms)
                        with lock:
                            timing["benchmark"] = rows
                        if not timing_only:
                            minimum_step = calibration.minimum_step
                            if step < minimum_step:
                                raise ValueError(f"De gemeten ruis op kanaal 2 vereist een DAC-stap van minstens "
                                                 f"{minimum_step} codes. Ingesteld: {step}.")
                            with lock:
                                status = "Achtergrond scannen..." if achtergrond else "Scannen..."
                            previous_code = timing_code
                            feedback = calibration.zero + timing_code * calibration.per_code
                            samples = aantal_blokken * calibration.block_size
                            for code in range(0, 4096, step):
                                target = feedback + (code - previous_code) * calibration.per_code
                                point = capture.step(calibration, code, feedback, target, samples, delay)
                                capture.check_stop()
                                measurements.append(point)
                                feedback, previous_code = point.feedback, code
                                with lock:
                                    if achtergrond:
                                        background_points.append(point.detector)
                                        scan_background["points"] = len(background_points)
                                    else:
                                        punten.append([float(code), point.detector, point.feedback])
                    finally:
                        pda.stop_continuous()
            except BaseException:
                with suppress(OSError):
                    write_dac(0)
                raise
            else:
                write_dac(0)

        with lock:
            if stop_event.is_set():
                raise MeasurementStopped
            if timing_only:
                status = "Timingtest klaar"
            elif achtergrond:
                values = np.asarray(background_points)
                if values.size >= 2:
                    scan_background.update(value=float(np.mean(values)),
                                           sigma=float(np.std(values, ddof=1)),
                                           y_unit=live_block["y_unit"], settings=settings,
                                           status="Achtergrondscan klaar")
                else:
                    scan_background["status"] = "Achtergrondscan mislukt: minder dan twee geldige punten"
                status = scan_background["status"]
            else:
                status = f"Scan klaar - gain {gain} dB"
    except MeasurementStopped:
        with lock:
            status = "Timingtest gestopt" if timing_only else "Scan gestopt"
    except Exception as exc:
        with lock:
            error = f"{type(exc).__name__}: {exc}"
            status = "Fout tijdens timingtest" if timing_only else "Fout tijdens scan"
    finally:
        scan_rows = step_rows(measurements) if measurements else []
        with lock:
            if not timing_only:
                timing["scan"] = scan_rows
            if achtergrond and (stop_event.is_set() or error):
                scan_background["status"] = "Achtergrondscan afgebroken; vorige referentie behouden"
            stop_event.clear()
            running = False


# ---------------------------------------------------------
# Functies voor de GUI
# ---------------------------------------------------------

def clear_scan_background():
    with lock:
        if running:
            return False
        scan_background.update(value=None, sigma=None, y_unit="", settings=None,
                               status="Geen achtergrondscan", points=0)
    return True


def get_status():
    """
    Geef alleen kleine statusinformatie terug.

    De meetpunten worden hier NIET gekopieerd.
    Daardoor blijft dit snel.
    """

    with lock:

        return {
            "status": status,
            "running": running,
            "live_running": live_running,
            "error": error,
            "aantal_punten": len(punten),
            "y_unit": scan_y_unit,
            "background": dict(scan_background),
            "settings": scan_settings,
            "timing": dict(timing),
            "background_compatible": scan_settings is None or scan_settings == scan_background["settings"],
        }


def get_points(*, include_feedback=False):
    """
    Geef alle meetpunten terug.

    Deze functie gebruiken we alleen wanneer
    de grafiek echt bijgewerkt moet worden.
    """

    with lock:

        return list(punten) if include_feedback else [point[:2] for point in punten]


def export_scan_csv():
    """Exporteer een consistente momentopname, ook bij een lopende scan."""
    with lock:
        rows = [list(point) for point in punten]
        unit = scan_y_unit or ""
        baseline = (scan_background["value"]
                    if scan_background["y_unit"] == unit
                    and scan_background["settings"] == scan_settings else None)
        sigma = scan_background["sigma"] if baseline is not None else None
        lower = baseline - 2 * sigma if sigma is not None else ""
        upper = baseline + 2 * sigma if sigma is not None else ""
    if not rows:
        raise ValueError("Er zijn nog geen scanmeetpunten om te exporteren.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["dac_waarde", "detectorsignaal", "eenheid", "baseline",
                     "sigma", "baseline_min_2sigma", "baseline_plus_2sigma", "dac_feedback"])
    for dac, signal, feedback in rows:
        writer.writerow([dac, signal if np.isfinite(signal) else "", unit,
                         baseline if baseline is not None else "",
                         sigma if sigma is not None else "", lower, upper, feedback])
    return output.getvalue().encode("utf-8-sig")


def stop_scan():
    """Vraag de scan om te stoppen."""

    stop_event.set()
