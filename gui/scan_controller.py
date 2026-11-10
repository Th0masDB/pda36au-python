import sys
import time
import threading
import csv
import io
from pathlib import Path

import numpy as np


# ---------------------------------------------------------
# Hoofdmap toevoegen aan Python-pad
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from config import I2C_BUS, DAC_ADDRESS

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
scan_background = {"value": None, "sigma": None, "y_unit": "", "settings": None,
                   "status": "Geen achtergrondscan", "points": 0}

status = "Gereed"
running = False
error = ""

lock = threading.Lock()
stop_event = threading.Event()
live_stop_event = threading.Event()
live_running = False
live_status = "Gereed"
live_error = ""
live_block = {"data": [], "x_unit": "sample", "y_unit": "", "sequence": 0}
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


def publish_live_block(block):
    """Bewaar alleen het nieuwste blok; geheugengebruik blijft begrensd."""
    values = np.asarray(block.detector)
    x = getattr(block, "x", np.arange(len(values)))
    data = [[float(t), float(v) if np.isfinite(v) else None]
            for t, v in zip(x, values)]
    with lock:
        live_block.update(data=data, x_unit=getattr(block, "x_unit", "sample"),
                          y_unit=getattr(block, "y_unit", "gesimuleerd"),
                          sequence=live_block["sequence"] + 1)
        if background["target"]:
            unit = live_block["y_unit"]
            if background["blocks"] and unit != background["measurement_unit"]:
                background.update(target=0, status="Achtergrondmeting afgebroken: eenheid gewijzigd")
                return
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
            pda.start_continuous()
            try:
                with lock:
                    live_status = "Achtergrond meten" if background_only else "Live meten"
                while not live_stop_event.is_set():
                    if background_only:
                        with lock:
                            if not background["target"]:
                                live_stop_event.set()
                                break
                    publish_live_block(pda.read_block())
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


def get_live_data():
    with lock:
        return {**live_block, "running": live_running, "scanning": running,
                "status": "Scannen (live PDA-data)" if running else live_status,
                "error": live_error,
                "background": {key: background[key] for key in
                               ("value", "sigma", "y_unit", "target", "status")}}


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


# ---------------------------------------------------------
# PDA meten
# ---------------------------------------------------------

def meet_gemiddelde(pda, aantal_blokken, update_scan_unit=True):
    """Lees PDA-blokken en bereken het gemiddelde."""
    global scan_y_unit

    waarden = []

    for _ in range(aantal_blokken):

        if stop_event.is_set():
            break

        block = pda.read_block()
        publish_live_block(block)
        with lock:
            if update_scan_unit:
                scan_y_unit = getattr(block, "y_unit", "gesimuleerd")

        waarden.extend(
            block.detector
        )

    if not waarden:
        return float("nan")

    return float(
        np.mean(waarden)
    )


# ---------------------------------------------------------
# Cavity scan
# ---------------------------------------------------------

def scan_cavity(
    step=50,
    delay=0.001,
    aantal_blokken=1,
    achtergrond=False,
):
    """Voer één volledige cavity scan uit."""

    global status
    global running
    global error
    global scan_y_unit, scan_settings

    settings = (int(step), float(delay), int(aantal_blokken))
    if not 1 <= step <= 4095 or delay < 0 or not 1 <= aantal_blokken <= 100:
        raise ValueError("Ongeldige scaninstellingen")
    background_points = []

    # Voorkom dat twee scans tegelijk starten
    with lock:

        if running or live_running:
            return

        if not achtergrond:
            punten.clear()
            scan_y_unit = None
            scan_settings = settings
        else:
            scan_background.update(status="Achtergrond scannen...", points=0)

        status = "Achtergrond scannen..." if achtergrond else "Scannen..."
        running = True
        error = ""

    stop_event.clear()

    bus = None

    try:
        # I2C-bus openen
        bus = SMBus(I2C_BUS)

        # PDA openen
        with PDA36AU() as pda:

            gain = pda.get_gain_db()

            # Continuous mode één keer starten
            pda.start_continuous()

            try:
                # DAC van 0 tot 4095
                for dac_value in range(
                    0,
                    4096,
                    step,
                ):

                    # Stopknop controleren
                    if stop_event.is_set():

                        with lock:
                            status = "Scan gestopt"

                        break

                    # DAC instellen
                    set_dac(
                        bus,
                        dac_value,
                    )

                    # Eventueel wachten
                    if delay > 0:
                        time.sleep(delay)

                    # Detector meten
                    gemiddelde = meet_gemiddelde(
                        pda,
                        aantal_blokken, update_scan_unit=not achtergrond,
                    )

                    # Meetpunt opslaan
                    with lock:

                        if achtergrond:
                            background_points.append(gemiddelde)
                            scan_background["points"] = len(background_points)
                        else:
                            punten.append([float(dac_value), gemiddelde])

            finally:
                # PDA stoppen
                pda.stop_continuous()

                # DAC veilig terug naar nul
                set_dac(
                    bus,
                    0,
                )

        # Alleen "klaar" melden wanneer
        # de scan niet handmatig gestopt is
        if not stop_event.is_set():

            with lock:

                status = f"Scan klaar - gain {gain} dB"
                if achtergrond:
                    values = np.asarray(background_points)
                    values = values[np.isfinite(values)]
                    if values.size >= 2:
                        scan_background.update(value=float(np.mean(values)),
                                               sigma=float(np.std(values, ddof=1)),
                                               y_unit=live_block["y_unit"], settings=settings,
                                               status="Achtergrondscan klaar")
                        status = "Achtergrondscan klaar"
                    else:
                        scan_background["status"] = "Achtergrondscan mislukt: minder dan twee geldige punten"
                        status = scan_background["status"]

    except Exception as exc:

        with lock:

            error = (
                f"{type(exc).__name__}: {exc}"
            )

            status = "Fout tijdens scan"

        # Bij een fout proberen de DAC
        # alsnog terug naar nul te zetten
        if bus is not None:

            try:
                set_dac(
                    bus,
                    0,
                )

            except Exception:
                pass

    finally:

        # I2C-bus sluiten
        if bus is not None:

            close = getattr(
                bus,
                "close",
                None,
            )

            if callable(close):

                try:
                    close()

                except Exception:
                    pass

        with lock:
            if achtergrond and (stop_event.is_set() or error):
                scan_background["status"] = "Achtergrondscan afgebroken; vorige referentie behouden"
            running = False

        stop_event.clear()


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
            "error": error,
            "aantal_punten": len(punten),
            "y_unit": scan_y_unit,
            "background": dict(scan_background),
            "settings": scan_settings,
            "background_compatible": scan_settings is None or scan_settings == scan_background["settings"],
        }


def get_points():
    """
    Geef alle meetpunten terug.

    Deze functie gebruiken we alleen wanneer
    de grafiek echt bijgewerkt moet worden.
    """

    with lock:

        return list(punten)


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
                     "sigma", "baseline_min_2sigma", "baseline_plus_2sigma"])
    for dac, signal in rows:
        writer.writerow([dac, signal if np.isfinite(signal) else "", unit,
                         baseline if baseline is not None else "",
                         sigma if sigma is not None else "", lower, upper])
    return output.getvalue().encode("utf-8-sig")


def stop_scan():
    """Vraag de scan om te stoppen."""

    stop_event.set()
