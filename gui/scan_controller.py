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
background = {"value": None, "y_unit": "", "target": 0, "blocks": 0,
              "sum": 0.0, "samples": 0, "status": "Geen achtergrondmeting"}


def start_background(aantal_blokken=10):
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
        background.update(target=aantal_blokken, blocks=0, sum=0.0, samples=0,
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
        background.update(value=None, y_unit="", target=0, blocks=0,
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
            background["measurement_unit"] = unit
            background["sum"] += float(np.sum(finite))
            background["samples"] += int(finite.size)
            background["blocks"] += 1
            background["status"] = f'Achtergrond meten: {background["blocks"]}/{background["target"]} blokken'
            if background["blocks"] >= background["target"]:
                if background["samples"]:
                    background["value"] = background["sum"] / background["samples"]
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
                               ("value", "y_unit", "target", "status")}}


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

def meet_gemiddelde(pda, aantal_blokken):
    """Lees PDA-blokken en bereken het gemiddelde."""
    global scan_y_unit

    waarden = []

    for _ in range(aantal_blokken):

        if stop_event.is_set():
            break

        block = pda.read_block()
        publish_live_block(block)
        with lock:
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
):
    """Voer één volledige cavity scan uit."""

    global status
    global running
    global error
    global scan_y_unit

    # Voorkom dat twee scans tegelijk starten
    with lock:

        if running or live_running:
            return

        punten.clear()
        scan_y_unit = None

        status = "Scannen..."
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
                        aantal_blokken,
                    )

                    # Meetpunt opslaan
                    with lock:

                        punten.append([
                            float(dac_value),
                            gemiddelde,
                        ])

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

                status = (
                    f"Scan klaar - gain {gain} dB"
                )

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
            running = False

        stop_event.clear()


# ---------------------------------------------------------
# Functies voor de GUI
# ---------------------------------------------------------

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
            "background": {key: background[key] for key in ("value", "y_unit")},
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
        baseline = (background["value"]
                    if background["y_unit"] == unit else None)
    if not rows:
        raise ValueError("Er zijn nog geen scanmeetpunten om te exporteren.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["dac_waarde", "detectorsignaal", "eenheid", "baseline"])
    for dac, signal in rows:
        writer.writerow([dac, signal if np.isfinite(signal) else "", unit,
                         baseline if baseline is not None else ""])
    return output.getvalue().encode("utf-8-sig")


def stop_scan():
    """Vraag de scan om te stoppen."""

    stop_event.set()
