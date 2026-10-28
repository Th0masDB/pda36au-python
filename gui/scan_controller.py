import sys
import time
import threading
from pathlib import Path

import numpy as np


# ---------------------------------------------------------
# Hoofdmap toevoegen aan Python-pad
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))


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
# Instellingen
# ---------------------------------------------------------

ADDRESS = 0x60


# ---------------------------------------------------------
# Gegevens van de huidige scan
# ---------------------------------------------------------

punten = []

status = "Gereed"
running = False
error = ""

lock = threading.Lock()
stop_event = threading.Event()


# ---------------------------------------------------------
# DAC instellen
# ---------------------------------------------------------

def set_dac(bus, value):
    """Zet de DAC op een waarde tussen 0 en 4095."""

    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(
        ADDRESS,
        [byte1, byte2],
    )

    bus.i2c_rdwr(msg)


# ---------------------------------------------------------
# PDA meten
# ---------------------------------------------------------

def meet_gemiddelde(pda, aantal_blokken):
    """Lees PDA-blokken en bereken het gemiddelde."""

    waarden = []

    for _ in range(aantal_blokken):

        if stop_event.is_set():
            break

        block = pda.read_block()

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

    # Voorkom dat twee scans tegelijk starten
    with lock:

        if running:
            return

        punten.clear()

        status = "Scannen..."
        running = True
        error = ""

    stop_event.clear()

    bus = None

    try:
        # I2C-bus openen
        bus = SMBus(1)

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
        }


def get_points():
    """
    Geef alle meetpunten terug.

    Deze functie gebruiken we alleen wanneer
    de grafiek echt bijgewerkt moet worden.
    """

    with lock:

        return list(punten)


def stop_scan():
    """Vraag de scan om te stoppen."""

    stop_event.set()