#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import argparse
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from nicegui import run, ui


# -----------------------------------------------------------------------------
# Command-line opties
# -----------------------------------------------------------------------------
parser = argparse.ArgumentParser(description='NiceGUI webinterface voor cavity scan')
parser.add_argument(
    '--sim', '--simulatie',
    action='store_true',
    dest='simulatie',
    help='Gebruik simulation.py voor de I2C/DAC-simulatie',
)
parser.add_argument(
    '--PDACon',
    action='store_true',
    dest='pda_connected',
    help='PDA is aangesloten (zelfde vlag als in scan.py)',
)
parser.add_argument('--host', default='0.0.0.0')
parser.add_argument('--port', type=int, default=8080)
args = parser.parse_args()

SIMULATIE = args.simulatie
PDA_Connected = args.pda_connected


# -----------------------------------------------------------------------------
# Zelfde importlogica als de aangeleverde scan.py
# -----------------------------------------------------------------------------
if SIMULATIE and PDA_Connected:
    from simulation import SMBus, i2c_msg, PDA36AU
if SIMULATIE:
    from simulation import SMBus, i2c_msg
    from pda36au import PDA36AU
else:
    from smbus2 import SMBus, i2c_msg
    from pda36au import PDA36AU


# -----------------------------------------------------------------------------
# Scaninstellingen: dezelfde defaults als de nieuwe scan.py
# -----------------------------------------------------------------------------
ADDRESS = 0x60
DAC_MAX = 4095
STEP = 50
DELAY = 0.001
AANTAL_BLOKKEN = 1

# Live plotting is optioneel. Uit = minimale GUI-overhead tijdens de scan.
LIVE_PLOT_INTERVAL = 1.0
MAX_LIVE_POINTS = 400


@dataclass
class ScanState:
    points: list[list[float]] = field(default_factory=list)
    running: bool = False
    status: str = 'Gereed'
    error: str = ''
    current_index: int = 0
    total_points: int = 1
    gain_db: float | None = None
    elapsed: float = 0.0


state = ScanState()
state_lock = threading.Lock()
stop_event = threading.Event()


def meet_gemiddelde(pda, aantal_blokken=5):
    """Exact dezelfde meetmethode als in scan.py."""
    waarden = []

    for _ in range(aantal_blokken):
        if stop_event.is_set():
            break
        block = pda.read_block()
        waarden.extend(block.detector)

    if not waarden:
        return float('nan')

    return float(np.mean(waarden))


def set_dac(bus, value):
    """Zet de 12-bit DAC-waarde."""
    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(ADDRESS, [byte1, byte2])
    bus.i2c_rdwr(msg)


def scan_cavity_worker(step, delay, aantal_blokken):
    """
    Blocking scan voor de worker-thread.

    Belangrijk: start_continuous() wordt één keer vóór de volledige scan
    aangeroepen en stop_continuous() één keer erna, net als in de nieuwe scan.py.
    """
    bus = None
    start_time = time.perf_counter()

    try:
        bus = SMBus(1)
        dac_values = list(range(0, 4096, step))

        with state_lock:
            state.points.clear()
            state.running = True
            state.status = 'PDA36AU verbinden...'
            state.error = ''
            state.current_index = 0
            state.total_points = max(1, len(dac_values))
            state.gain_db = None
            state.elapsed = 0.0

        with PDA36AU() as pda:
            gain = pda.get_gain_db()

            with state_lock:
                try:
                    state.gain_db = float(gain)
                except (TypeError, ValueError):
                    state.gain_db = None
                state.status = 'Scannen...'

            pda.start_continuous()
            try:
                for index, dac_value in enumerate(dac_values, start=1):
                    if stop_event.is_set():
                        with state_lock:
                            state.status = 'Scan gestopt'
                        break

                    set_dac(bus, dac_value)

                    if delay > 0:
                        time.sleep(delay)

                    gemiddelde = meet_gemiddelde(
                        pda,
                        aantal_blokken=aantal_blokken,
                    )

                    with state_lock:
                        state.points.append([float(dac_value), gemiddelde])
                        state.current_index = index
                        state.elapsed = time.perf_counter() - start_time
                else:
                    with state_lock:
                        state.status = 'Scan klaar'
            finally:
                pda.stop_continuous()
                set_dac(bus, 0)

    except Exception as exc:
        with state_lock:
            state.error = f'{type(exc).__name__}: {exc}'
            state.status = 'Fout tijdens scan'

        # Probeer de DAC ook bij een fout veilig terug op nul te zetten.
        if bus is not None:
            try:
                set_dac(bus, 0)
            except Exception:
                pass

    finally:
        elapsed = time.perf_counter() - start_time

        if bus is not None:
            close = getattr(bus, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        with state_lock:
            state.elapsed = elapsed
            state.running = False

        stop_event.clear()


def build_page():
    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-4'):
        ui.label('Cavity Scan Controller').classes('text-2xl font-bold')
        ui.label(
            'De scan draait op de Raspberry Pi; deze pagina kan vanaf een andere computer worden geopend.'
        ).classes('text-gray-600')

        with ui.card().classes('w-full'):
            with ui.row().classes('items-end gap-4 w-full'):
                step_input = ui.number(
                    label='DAC stapgrootte',
                    value=STEP,
                    min=1,
                    max=4095,
                    step=1,
                ).classes('w-44')

                delay_input = ui.number(
                    label='Delay per stap (ms)',
                    value=DELAY * 1000,
                    min=0,
                    step=0.1,
                ).classes('w-44')

                blocks_input = ui.number(
                    label='PDA blokken / punt',
                    value=AANTAL_BLOKKEN,
                    min=1,
                    max=100,
                    step=1,
                ).classes('w-44')

                live_plot_switch = ui.switch(
                    'Live grafiek',
                    value=False,
                ).tooltip(
                    'Uit is het snelst. Na afloop verschijnt altijd de volledige grafiek.'
                )

                start_button = ui.button('Start scan', icon='play_arrow')
                stop_button = ui.button('Stop', icon='stop')
                stop_button.disable()

            if SIMULATIE and PDA_Connected:
                mode_text = 'SIMULATIE + PDACon'
            elif SIMULATIE:
                mode_text = 'SIMULATIE'
            else:
                mode_text = 'HARDWARE'

            ui.label(f'Modus: {mode_text}').classes('text-sm font-medium')

        with ui.card().classes('w-full'):
            status_label = ui.label('Status: Gereed').classes('font-medium')
            gain_label = ui.label('Gain: -')
            point_label = ui.label('Punten: 0')
            time_label = ui.label('Scantijd: 0.00 s')
            progress = ui.linear_progress(value=0).classes('w-full')

        chart = ui.echart({
            'animation': False,
            'tooltip': {'trigger': 'axis'},
            'grid': {
                'left': 75,
                'right': 30,
                'top': 35,
                'bottom': 65,
            },
            'xAxis': {
                'type': 'value',
                'name': 'DAC waarde',
                'nameLocation': 'middle',
                'nameGap': 38,
                'min': 0,
                'max': DAC_MAX,
            },
            'yAxis': {
                'type': 'value',
                'name': 'Gemiddeld detectorsignaal',
                'nameLocation': 'middle',
                'nameGap': 55,
                'scale': True,
            },
            'series': [{
                'name': 'PDA36AU',
                'type': 'line',
                'showSymbol': True,
                'symbolSize': 5,
                'data': [],
            }],
        }).classes('w-full').style('height: 520px')

        error_label = ui.label('').classes('text-red-600 whitespace-pre-wrap')

    async def start_scan():
        with state_lock:
            if state.running:
                return

        step = max(1, min(DAC_MAX, int(step_input.value or STEP)))
        delay = max(0.0, float(delay_input.value or 0.0) / 1000.0)
        aantal_blokken = max(1, int(blocks_input.value or AANTAL_BLOKKEN))

        stop_event.clear()
        start_button.disable()
        stop_button.enable()
        error_label.set_text('')

        # run.io_bound voorkomt dat de blocking hardware-I/O NiceGUI blokkeert.
        await run.io_bound(
            scan_cavity_worker,
            step,
            delay,
            aantal_blokken,
        )

    def stop_scan():
        stop_event.set()
        status_label.set_text('Status: stoppen...')
        stop_button.disable()

    start_button.on('click', start_scan)
    stop_button.on('click', stop_scan)

    rendered = {
        'point_count': -1,
        'was_running': False,
    }

    last_ui = {
        'status': None,
        'points': None,
        'gain': None,
        'time': None,
        'progress': None,
        'error': None,
        'running': None,
    }

    def snapshot(copy_points=False):
        with state_lock:
            return {
                'points': list(state.points) if copy_points else None,
                'point_count': len(state.points),
                'running': state.running,
                'status': state.status,
                'error': state.error,
                'current': state.current_index,
                'total': state.total_points,
                'gain': state.gain_db,
                'elapsed': state.elapsed,
            }

    def refresh_status():
        snap = snapshot(copy_points=False)

        status_text = f"Status: {snap['status']}"
        points_text = f"Punten: {snap['point_count']} / {snap['total']}"
        gain_text = 'Gain: -' if snap['gain'] is None else f"Gain: {snap['gain']:g} dB"
        time_text = f"Scantijd: {snap['elapsed']:.2f} s"
        progress_value = min(1.0, snap['current'] / max(1, snap['total']))

        if status_text != last_ui['status']:
            status_label.set_text(status_text)
            last_ui['status'] = status_text

        if points_text != last_ui['points']:
            point_label.set_text(points_text)
            last_ui['points'] = points_text

        if gain_text != last_ui['gain']:
            gain_label.set_text(gain_text)
            last_ui['gain'] = gain_text

        if time_text != last_ui['time']:
            time_label.set_text(time_text)
            last_ui['time'] = time_text

        if progress_value != last_ui['progress']:
            progress.value = progress_value
            last_ui['progress'] = progress_value

        if snap['error'] != last_ui['error']:
            error_label.set_text(snap['error'])
            last_ui['error'] = snap['error']

        if snap['running'] != last_ui['running']:
            if snap['running']:
                start_button.disable()
                stop_button.enable()
            else:
                start_button.enable()
                stop_button.disable()
            last_ui['running'] = snap['running']

    def refresh_chart():
        snap = snapshot(copy_points=False)
        count = snap['point_count']
        running = snap['running']

        # Geen plot-updates tijdens de scan als live plotting uit staat.
        if running and not live_plot_switch.value:
            rendered['was_running'] = True
            return

        scan_just_finished = rendered['was_running'] and not running
        if count == rendered['point_count'] and not scan_just_finished:
            rendered['was_running'] = running
            return

        snap = snapshot(copy_points=True)
        points = snap['points']

        if running and len(points) > MAX_LIVE_POINTS:
            stride = max(1, len(points) // MAX_LIVE_POINTS)
            display_points = points[::stride]
            if display_points and display_points[-1] != points[-1]:
                display_points.append(points[-1])
        else:
            display_points = points

        chart.options['series'][0]['data'] = display_points
        chart.update()

        rendered['point_count'] = len(points)
        rendered['was_running'] = running

    # Status kan regelmatig verversen zonder de meetlus zwaar te belasten.
    ui.timer(0.5, refresh_status)
    ui.timer(LIVE_PLOT_INTERVAL, refresh_chart)


build_page()

ui.run(
    host=args.host,
    port=args.port,
    title='Cavity Scan Controller',
    reload=False,
    show=False,
)