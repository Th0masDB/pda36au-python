import sys
import time
import threading
import csv
import io
import math
from contextlib import ExitStack, suppress
from pathlib import Path

import numpy as np


# ---------------------------------------------------------
# Hoofdmap toevoegen aan Python-pad
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from config import I2C_BUS, DAC_ADDRESS, FAST_SCAN_LIMITS, FAST_SCAN_ADC_OFFSET_MS

# ---------------------------------------------------------
# Simulatie of echte hardware
# ---------------------------------------------------------

SIMULATIE = "--sim" in sys.argv or "--simulatie" in sys.argv
PDA_CONNECTED = "--PDACon" in sys.argv
FAST_SCAN = "--fast-scan" in sys.argv


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
live_block = {"block": None, "data": [], "x_unit": "sample", "y_unit": "", "sequence": 0}
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
        live_block.update(block=block, data=None, x_unit=getattr(block, "x_unit", "sample"),
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
        with PDA36AU() as pda, ExitStack() as session:
            stream = None
            if SIMULATIE and not PDA_CONNECTED:
                pda.start_continuous()
                session.callback(pda.stop_continuous)
            else:
                stream = session.enter_context(pda.start_streaming(free_run=True))
            next_update = 0.0
            with lock:
                live_status = "Achtergrond meten" if background_only else "Live meten"
            while not live_stop_event.is_set():
                if background_only:
                    with lock:
                        if not background["target"]:
                            break
                if stream is None:
                    publish_live_block(pda.read_block())
                    live_stop_event.wait(0.05)
                    continue

                # Drain every raw frame, with conversion only for display or baseline.
                frame = stream.read(timeout=1.0)
                now = time.monotonic()
                with lock:
                    show_frame = now >= next_update or background["target"] > 0
                if show_frame:
                    publish_live_block(pda.decode_block(frame.raw))
                    next_update = now + 0.05
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


def _format_live_samples(block):
    values = np.asarray(block.detector)
    x = getattr(block, "x", None)
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
                    "status": "Scannen (live PDA-data)" if running else live_status,
                    "error": live_error,
                    "background": {key: background[key] for key in
                                   ("value", "sigma", "y_unit", "target", "status")}}

    block = snapshot.pop("block")
    if snapshot["data"] is None:
        snapshot["data"] = _format_live_samples(block)
        with lock:
            if live_block["sequence"] == snapshot["sequence"]:
                live_block["data"] = snapshot["data"]
    return snapshot


# ---------------------------------------------------------
# DAC instellen
# ---------------------------------------------------------

def dac_message(value):
    return i2c_msg.write(DAC_ADDRESS, [value >> 8, value & 0xff])


def set_dac(bus, value):
    """Zet de DAC op een waarde tussen 0 en 4095."""
    bus.i2c_rdwr(dac_message(max(0, min(4095, value))))


# ---------------------------------------------------------
# PDA meten
# ---------------------------------------------------------

def meet_gemiddelde(pda, aantal_blokken, update_scan_unit=True):
    """Lees PDA-blokken en bereken het gemiddelde."""
    total = 0.0
    samples = 0

    for _ in range(aantal_blokken):
        if stop_event.is_set():
            break

        block = pda.read_block()
        publish_live_block(block, update_scan_unit=update_scan_unit)
        values = np.asarray(block.detector)
        total += float(values.sum(dtype=np.float64))
        samples += values.size

    return total / samples if samples else float("nan")


def _scan_limits(step):
    if SIMULATIE:
        if not PDA_CONNECTED:
            raise ValueError("Gebruik --sim --PDACon voor een snelle test met de echte PDA.")
        return 4095, 0.0, 0.001
    if FAST_SCAN_LIMITS is None:
        raise ValueError("Vul eerst de geverifieerde FAST_SCAN_LIMITS in config.py in.")
    top, step_limit, forward_ms, return_ms = FAST_SCAN_LIMITS
    if (not isinstance(top, int) or not 1 <= top <= 4095
            or not isinstance(step_limit, int) or not 1 <= step <= step_limit <= 4095
            or not all(math.isfinite(ms) and ms > 0 for ms in (forward_ms, return_ms))):
        raise ValueError("DAC-stap of hardwaregrenzen zijn ongeldig.")
    return top, forward_ms / 1000, return_ms / 1000


def _require_zero_dac(bus):
    register = i2c_msg.read(DAC_ADDRESS, 3)
    bus.i2c_rdwr(register)
    state, high, low = register
    if (state & 0x86) != 0x80 or high or (low & 0xf0):
        raise RuntimeError("Start de scan met een actieve DAC op code nul.")


def _fast_points(pda, bus, step, dwell, limits, *, update_scan_unit):
    top, min_dwell, return_dwell = limits
    if not math.isfinite(FAST_SCAN_ADC_OFFSET_MS) or FAST_SCAN_ADC_OFFSET_MS < 0:
        raise ValueError("FAST_SCAN_ADC_OFFSET_MS moet een niet-negatieve tijd zijn.")
    if not SIMULATIE:
        _require_zero_dac(bus)

    codes = np.array([*range(0, top, step), top])
    messages = [dac_message(int(code)) for code in codes]
    dwell = max(dwell, min_dwell)
    offset_ns = round(FAST_SCAN_ADC_OFFSET_MS * 1e6)
    completions = []
    forward_end = return_end = None
    finished = threading.Event()
    failures = []
    sums = np.zeros(len(codes))
    counts = np.zeros(len(codes), dtype=np.int64)

    def write(message):
        if SIMULATIE:
            time.sleep(27 / 400_000)  # Model an individual fast-mode transfer.
        bus.i2c_rdwr(message)

    def drive():
        nonlocal forward_end, return_end
        try:
            for message in messages:
                if stop_event.is_set():
                    break
                write(message)
                completions.append(time.monotonic_ns())
                if stop_event.wait(dwell):
                    break
        except Exception as exc:
            failures.append(RuntimeError(f"DAC-write mislukt, uitgang onzeker: {exc}"))
        finally:
            forward_end = time.monotonic_ns()
            if not failures:
                try:
                    for message in reversed(messages[:max(0, len(completions) - 1)]):
                        time.sleep(return_dwell)
                        write(message)
                except Exception as exc:
                    failures.append(RuntimeError(f"DAC-terugrit mislukt, uitgang onzeker: {exc}"))
            return_end = time.monotonic_ns()
            finished.set()

    with pda.start_streaming(free_run=True) as stream:
        frame = stream.read(timeout=1.0)
        if frame.sample_interval_s is None:
            raise RuntimeError("De PDA36AU gaf geen ADC-sample-interval door.")
        offsets = np.rint(np.arange(1 - frame.detector.size, 1) * frame.sample_interval_s * 1e9).astype(np.int64)
        worker = threading.Thread(target=drive, name="cavity-fast-dac")
        worker.start()
        try:
            while True:
                frame = stream.read(timeout=1.0)
                stamps = tuple(completions)
                end = forward_end
                sample_times = frame.host_received_time_ns - offset_ns + offsets
                slots = np.searchsorted(stamps, sample_times, side="right") - 1
                valid = slots >= 0
                if end is not None:
                    valid &= sample_times < end
                if valid.any():
                    slots = slots[valid]
                    sums += np.bincount(slots, weights=frame.detector[valid], minlength=len(codes))
                    counts += np.bincount(slots, minlength=len(codes))
                if finished.is_set() and sample_times[-1] >= forward_end:
                    break
        finally:
            if not finished.is_set():
                stop_event.set()
            worker.join()
        if failures:
            raise failures[0]
    publish_live_block(pda.decode_block(frame.raw), update_scan_unit=update_scan_unit)
    valid = counts > 0
    detector = sums[valid] / counts[valid]
    scale = stream.scale
    if scale is not None and scale.y_valid:
        detector = detector * scale.y_conversion - scale.y_offset
    else:
        detector -= pda.ADC_MIDSCALE
    forward_ms = (forward_end - completions[0]) / 1e6 if completions else 0
    return_ms = (return_end - forward_end) / 1e6
    source = "gesimuleerde DAC" if SIMULATIE else "DAC"
    timing = f" · {source} · {forward_ms:.1f} ms heen / {return_ms:.1f} ms terug"
    intervals = np.diff(completions)
    if intervals.size and intervals.sum() > 0:
        timing += f" · {1e9 / intervals.mean():.0f} updates/s · langste interval {intervals.max() / 1e6:.3f} ms"
    timing += f" · {stream.diagnostics.device_missed_delta} gemiste PDA-blokken"
    return zip(codes[valid], detector), timing


def _stepped_points(pda, bus, step, delay, aantal_blokken, limits, *, update_scan_unit):
    codes = range(0, 4096, step)
    if limits is not None:
        _require_zero_dac(bus)
        top, min_dwell, return_dwell = limits
        codes = (*range(0, top, step), top)
        delay = max(delay, min_dwell)

    pda.start_continuous()
    current = 0
    try:
        for code in codes:
            if stop_event.is_set():
                break
            current = None
            set_dac(bus, code)
            current = code
            if delay > 0:
                time.sleep(delay)
            yield code, meet_gemiddelde(pda, aantal_blokken, update_scan_unit=update_scan_unit)
    finally:
        try:
            if limits is not None:
                while current:
                    time.sleep(return_dwell)
                    current = max(0, current - step)
                    set_dac(bus, current)
        finally:
            pda.stop_continuous()


# ---------------------------------------------------------
# Cavity scan
# ---------------------------------------------------------

def scan_cavity(
    step=50,
    delay=0.001,
    aantal_blokken=1,
    achtergrond=False,
    *,
    fast=None,
):
    """Voer één volledige cavity scan uit."""

    global status
    global running
    global error
    global scan_y_unit, scan_settings

    fast = FAST_SCAN if fast is None else bool(fast)
    settings = (int(step), float(delay), 1 if fast else int(aantal_blokken), fast)
    if not 1 <= step <= 4095 or not math.isfinite(delay) or delay < 0 or not 1 <= aantal_blokken <= 100:
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
        stop_event.clear()
        running = True
        error = ""

    bus = None
    timing = ""
    guarded = fast or (not SIMULATIE and FAST_SCAN_LIMITS is not None)

    try:
        limits = _scan_limits(step) if guarded else None
        # I2C-bus openen
        bus = SMBus(I2C_BUS)

        # PDA openen
        with PDA36AU() as pda:

            gain = pda.get_gain_db()

            if fast:
                rows, timing = _fast_points(
                    pda, bus, step, delay, limits, update_scan_unit=not achtergrond,
                )
            else:
                rows = _stepped_points(pda, bus, step, delay, aantal_blokken, limits,
                                       update_scan_unit=not achtergrond)

            for dac_value, gemiddelde in rows:
                with lock:
                    if achtergrond:
                        background_points.append(gemiddelde)
                        scan_background["points"] = len(background_points)
                    else:
                        punten.append([float(dac_value), float(gemiddelde)])

            if not guarded:
                set_dac(bus, 0)

        # Alleen "klaar" melden wanneer
        # de scan niet handmatig gestopt is
        if not stop_event.is_set():

            with lock:

                status = f"Scan klaar - gain {gain} dB{timing}"
                if achtergrond:
                    values = np.asarray(background_points)
                    values = values[np.isfinite(values)]
                    if values.size >= 2:
                        scan_background.update(value=float(np.mean(values)),
                                               sigma=float(np.std(values, ddof=1)),
                                               y_unit=live_block["y_unit"], settings=settings,
                                               status="Achtergrondscan klaar")
                        status = f"Achtergrondscan klaar{timing}"
                    else:
                        scan_background["status"] = "Achtergrondscan mislukt: minder dan twee geldige punten"
                        status = scan_background["status"]
        else:
            with lock:
                status = "Scan gestopt"

    except Exception as exc:

        with lock:

            error = (
                f"{type(exc).__name__}: {exc}"
            )

            status = "Fout tijdens scan"

        # Bij een fout proberen de DAC
        # alsnog terug naar nul te zetten
        if bus is not None and not guarded:

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
