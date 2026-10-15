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


parser = argparse.ArgumentParser(description='Web-GUI voor cavity scan')
parser.add_argument('--sim', '--simulatie', action='store_true', dest='simulatie',
                    help='Gebruik de gesimuleerde DAC/PDA36AU in simulation.py')
parser.add_argument('--host', default='0.0.0.0',
                    help='Adres waarop de webserver luistert (default: 0.0.0.0)')
parser.add_argument('--port', type=int, default=8080,
                    help='TCP-poort van de webinterface (default: 8080)')
args = parser.parse_args()

SIMULATIE = args.simulatie

if SIMULATIE:
    from simulation import SMBus, i2c_msg, PDA36AU
else:
    from smbus2 import SMBus, i2c_msg
    from pda36au import PDA36AU


ADDRESS = 0x60
DAC_MAX = 4095


@dataclass
class ScanState:
    points: list[list[float]] = field(default_factory=list)
    running: bool = False
    status: str = 'Gereed'
    error: str = ''
    current_dac: int = 0
    total_points: int = 1
    gain_db: float | None = None


state = ScanState()
state_lock = threading.Lock()
stop_event = threading.Event()


def meet_gemiddelde(pda, aantal_blokken: int = 5) -> float:
    """Lees meerdere PDA-blokken uit en geef het gemiddelde detectorsignaal terug."""
    waarden = []

    pda.start_continuous()
    try:
        for _ in range(aantal_blokken):
            if stop_event.is_set():
                break
            block = pda.read_block()
            waarden.extend(block.detector)
    finally:
        pda.stop_continuous()

    if not waarden:
        return float('nan')

    return float(np.mean(waarden))


def set_dac(bus, value: int) -> None:
    """Schrijf een 12-bit waarde naar de DAC."""
    value = max(0, min(DAC_MAX, int(value)))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(ADDRESS, [byte1, byte2])
    bus.i2c_rdwr(msg)


def scan_worker(step: int, delay: float, aantal_blokken: int) -> None:
    """Voer de blocking hardware-scan uit in een worker-thread."""
    bus = None

    try:
        bus = SMBus(1)
        dac_values = list(range(0, DAC_MAX + 1, step))

        with state_lock:
            state.points.clear()
            state.running = True
            state.status = 'PDA36AU verbinden...'
            state.error = ''
            state.current_dac = 0
            state.total_points = max(1, len(dac_values))
            state.gain_db = None

        with PDA36AU() as pda:
            gain = pda.get_gain_db()
            with state_lock:
                state.gain_db = float(gain)
                state.status = 'Scannen...'

            for index, dac_value in enumerate(dac_values, start=1):
                if stop_event.is_set():
                    with state_lock:
                        state.status = 'Scan gestopt'
                    break

                set_dac(bus, dac_value)
                time.sleep(delay)
                gemiddelde = meet_gemiddelde(pda, aantal_blokken)

                with state_lock:
                    state.points.append([float(dac_value), gemiddelde])
                    state.current_dac = index
            else:
                with state_lock:
                    state.status = 'Scan klaar'

    except Exception as exc:
        with state_lock:
            state.error = f'{type(exc).__name__}: {exc}'
            state.status = 'Fout tijdens scan'

    finally:
        if bus is not None:
            try:
                set_dac(bus, 0)
            except Exception:
                pass

            close = getattr(bus, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        with state_lock:
            state.running = False
        stop_event.clear()


def build_page() -> None:
    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-4'):
        ui.label('Cavity Scan Controller').classes('text-2xl font-bold')
        ui.label(
            'NiceGUI draait op de Raspberry Pi; de grafiek bekijk je via een browser op je laptop of telefoon.'
        ).classes('text-gray-600')

        with ui.card().classes('w-full'):
            with ui.row().classes('items-end gap-4 w-full'):
                step_input = ui.number(
                    label='DAC stapgrootte',
                    value=2,
                    min=1,
                    max=4095,
                    step=1,
                ).classes('w-44')

                delay_input = ui.number(
                    label='Delay per stap (ms)',
                    value=10,
                    min=0,
                    step=1,
                ).classes('w-44')

                blocks_input = ui.number(
                    label='PDA blokken / punt',
                    value=5,
                    min=1,
                    max=100,
                    step=1,
                ).classes('w-44')

                start_button = ui.button('Start scan', icon='play_arrow')
                stop_button = ui.button('Stop', icon='stop')
                stop_button.disable()

            mode_text = 'SIMULATIE' if SIMULATIE else 'HARDWARE'
            ui.label(f'Modus: {mode_text}').classes('text-sm font-medium')

        with ui.card().classes('w-full'):
            status_label = ui.label('Status: Gereed').classes('font-medium')
            gain_label = ui.label('Gain: -')
            point_label = ui.label('Punten: 0')
            progress = ui.linear_progress(value=0).classes('w-full')

        chart = ui.echart({
            'animation': False,
            'tooltip': {'trigger': 'axis'},
            'grid': {
                'left': 70,
                'right': 30,
                'top': 35,
                'bottom': 60,
            },
            'xAxis': {
                'type': 'value',
                'name': 'DAC waarde',
                'nameLocation': 'middle',
                'nameGap': 35,
                'min': 0,
                'max': DAC_MAX,
            },
            'yAxis': {
                'type': 'value',
                'name': 'Gemiddeld detectorsignaal',
                'nameLocation': 'middle',
                'nameGap': 50,
                'scale': True,
            },
            'series': [{
                'name': 'PDA36AU',
                'type': 'line',
                'showSymbol': False,
                'data': [],
            }],
        }).classes('w-full').style('height: 520px')

        error_label = ui.label('').classes('text-red-600 whitespace-pre-wrap')

    async def start_scan() -> None:
        with state_lock:
            if state.running:
                return

        step = max(1, min(DAC_MAX, int(step_input.value or 2)))
        delay = max(0.0, float(delay_input.value or 0.0) / 1000.0)
        aantal_blokken = max(1, int(blocks_input.value or 5))

        stop_event.clear()
        start_button.disable()
        stop_button.enable()
        error_label.set_text('')

        await run.io_bound(scan_worker, step, delay, aantal_blokken)

    def stop_scan() -> None:
        stop_event.set()
        status_label.set_text('Status: stoppen...')
        stop_button.disable()

    start_button.on('click', start_scan)
    stop_button.on('click', stop_scan)

    rendered_points = {'count': -1}

    def refresh_ui() -> None:
        with state_lock:
            points = list(state.points)
            running = state.running
            status = state.status
            error = state.error
            current = state.current_dac
            total = state.total_points
            gain = state.gain_db

        if len(points) != rendered_points['count']:
            chart.options['series'][0]['data'] = points
            chart.update()
            rendered_points['count'] = len(points)

        status_label.set_text(f'Status: {status}')
        point_label.set_text(f'Punten: {len(points)} / {total}')
        gain_label.set_text('Gain: -' if gain is None else f'Gain: {gain:g} dB')
        progress.value = min(1.0, current / max(1, total))
        error_label.set_text(error)

        if running:
            start_button.disable()
            stop_button.enable()
        else:
            start_button.enable()
            stop_button.disable()

    ui.timer(0.25, refresh_ui)


build_page()

ui.run(
    host=args.host,
    port=args.port,
    title='Cavity Scan Controller',
    reload=False,
    show=False,
)
