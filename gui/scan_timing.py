"""Meet DAC-feedback en verwerk gesynchroniseerde PDA-samples."""

from collections import deque
from dataclasses import dataclass
from math import ceil, floor
from time import monotonic_ns

import numpy as np


DEFAULT_TIMING_CODE = 1024
FEEDBACK_WINDOW_SAMPLES = 16
# Foutgrens bij uitblijven van data of feedback. Dit is geen meetvertraging.
TIMEOUT_NS = 5_000_000_000


class MeasurementStopped(Exception):
    pass


@dataclass(frozen=True)
class Calibration:
    zero: float
    per_code: float
    margin: float
    sample_period_s: float | None
    block_size: int
    window_samples: int = 1
    sample_margin: float = 0.0

    @property
    def minimum_step(self):
        return floor(2 * self.margin / abs(self.per_code)) + 1


@dataclass(frozen=True)
class StepMeasurement:
    detector: float
    feedback: float
    write_ms: float
    observed_ms: float
    queue_ms: float
    point_ms: float
    edge_us: float | None


class Acquisition:
    def __init__(self, pda, write_dac, stop_event, show_block):
        self.pda = pda
        self.write_dac = write_dac
        self.stop_event = stop_event
        self.show_block = show_block

    def check_stop(self):
        if self.stop_event.is_set():
            raise MeasurementStopped

    def read(self, deadline_ns, *, since_ns=0):
        while True:
            self.check_stop()
            remaining = (deadline_ns - monotonic_ns()) / 1e9
            if remaining <= 0:
                raise TimeoutError("Geen bruikbaar PDA-blok binnen de foutgrens.")
            try:
                block = self.pda.read_block(timeout=min(remaining, 0.2), recover_on_timeout=False)
            except TimeoutError:
                continue
            consumed_ns = monotonic_ns()
            self.check_stop()
            if block.received_ns >= since_ns:
                return block, consumed_ns

    def step(self, calibration, code, previous, target, samples=1, delay=0):
        """Bevestig de nieuwe DAC-stand en meet kanaal 1 daarna."""
        period = calibration.sample_period_s
        if delay and period is None:
            raise RuntimeError("De PDA gaf geen tijdsinterval voor de extra wachttijd.")
        skip = max(0, ceil(delay / period) - 1) if delay else 0
        change = abs(target - previous)
        margin = max(calibration.margin, min(0.1 * change, 2 * abs(calibration.per_code)))
        if change and change <= 2 * margin:
            raise ValueError("De DAC-stap is te klein voor de gemeten ruis op kanaal 2.")
        window = calibration.window_samples
        sample_margin = max(margin, calibration.sample_margin)

        self.check_stop()
        start_ns = monotonic_ns()
        self.write_dac(code)
        written_ns = monotonic_ns()
        deadline_ns = written_ns + TIMEOUT_NS
        trigger_ns = None
        queue_ms = 0.0
        edge_us = None
        detector_sum = feedback_sum = 0.0
        count = 0
        while count < samples:
            try:
                block, consumed_ns = self.read(deadline_ns, since_ns=start_ns)
            except TimeoutError as exc:
                detail = ("Kanaal 2 bevestigde de waarde niet" if trigger_ns is None
                          else "Te weinig geldige PDA-samples na de trigger")
                raise TimeoutError(f"{detail} bij DAC-waarde {code}.") from exc
            feedback = np.asarray(block.analog)
            in_band = np.abs(_window_means(feedback, window) - target) <= margin
            start = 0
            if trigger_ns is None:
                # Drie volledige meetvensters binnen de band bevestigen het niveau.
                confirmed = np.flatnonzero(in_band[:-2] & in_band[1:-1] & in_band[2:])
                if not confirmed.size:
                    continue
                trigger_ns = block.received_ns
                queue_ms = (consumed_ns - trigger_ns) / 1e6
                self.show_block(block)
                start = (int(confirmed[0]) + 3) * window
                if period and change > 2 * sample_margin:
                    edge_us = edge_duration_us(feedback[:start], previous, target, period)
                deadline_ns = monotonic_ns() + TIMEOUT_NS

            skipped = min(skip, feedback.size - start)
            start += skipped
            skip -= skipped
            end = in_band.size * window
            detector = np.asarray(block.detector)[start:end]
            feedback_samples = feedback[start:end]
            valid = (np.repeat(in_band, window)[start:] & np.isfinite(detector)
                     & (np.abs(feedback_samples - target) <= sample_margin))
            accepted = np.flatnonzero(valid)[:samples - count]
            detector_sum += float(detector[accepted].sum())
            feedback_sum += float(feedback_samples[accepted].sum())
            count += accepted.size
            if skipped or accepted.size:
                deadline_ns = monotonic_ns() + TIMEOUT_NS

        return StepMeasurement(
            detector_sum / count, feedback_sum / count, (written_ns - start_ns) / 1e6,
            (trigger_ns - start_ns) / 1e6, queue_ms, (monotonic_ns() - start_ns) / 1e6, edge_us,
        )


def edge_duration_us(feedback, previous, target, period_s):
    """Meet 10–90% van de flank binnen één PDA-blok."""
    fraction = (feedback - previous) / (target - previous)
    above_90 = np.flatnonzero((fraction[:-1] < 0.9) & (fraction[1:] >= 0.9))
    if above_90.size:
        end = int(above_90[0]) + 1
        above_10 = np.flatnonzero((fraction[:end] < 0.1) & (fraction[1:end + 1] >= 0.1))
        if above_10.size:
            # Een flank binnen één sample-interval is alleen tot die resolutie meetbaar.
            return max(1, end - int(above_10[-1]) - 1) * period_s * 1e6
    return None


def _window_means(values, window):
    size = values.size // window * window
    return values[:size].reshape(-1, window).mean(axis=1)


def _level(values, resolution):
    values = values[np.isfinite(values)]
    if values.size < 3:
        raise RuntimeError("Te weinig geldige samples op PDA-kanaal 2.")
    level = float(np.median(values))
    noise = float(np.quantile(np.abs(values - level), 0.99))
    margin = max(3 * noise, resolution, np.spacing(max(1.0, abs(level))))
    return level, margin


def _plateau(blocks, window):
    feedback = [np.asarray(block.analog) for block in blocks]
    resolution = blocks[-1].y_resolution
    _, sample_margin = _level(np.concatenate(feedback), resolution)
    level, margin = _level(np.concatenate([_window_means(values, window) for values in feedback]), resolution)
    return level, margin, sample_margin


def measure_usb(pda, stop_event):
    durations = []
    for _ in range(5):
        if stop_event.is_set():
            raise MeasurementStopped
        start_ns = monotonic_ns()
        pda.get_adc_status()
        durations.append((monotonic_ns() - start_ns) / 1e6)
    return durations


def benchmark(acquisition, reference_code, usb_ms):
    """Meet de PDA-doorvoer, kalibreer de DAC-feedback en meet acht overgangen."""
    start_ns = monotonic_ns()
    receipts = []
    samples = 0
    zero_blocks = deque(maxlen=4)
    for index in range(32):
        block, _ = acquisition.read(monotonic_ns() + TIMEOUT_NS, since_ns=start_ns)
        receipts.append(block.received_ns)
        zero_blocks.append(block)
        if index:
            samples += len(block.analog)

    window = max(1, min(FEEDBACK_WINDOW_SAMPLES, len(block.analog) // 3))
    zero, margin, sample_margin = _plateau(zero_blocks, window)
    acquisition.show_block(block)
    period = block.sample_period_s
    if period is not None and period <= 0:
        period = None

    # De referentiespanning wordt gemeten terwijl beide kanalen blijven lopen.
    acquisition.check_stop()
    begin_ns = monotonic_ns()
    acquisition.write_dac(reference_code)
    deadline_ns = monotonic_ns() + TIMEOUT_NS
    candidate = None
    while True:
        try:
            high_block, _ = acquisition.read(deadline_ns, since_ns=begin_ns)
        except TimeoutError as exc:
            raise TimeoutError("Geen stabiele DAC-verandering op kanaal 2. Controleer de aansluiting.") from exc
        high, noise = _level(np.asarray(high_block.analog), high_block.y_resolution)
        if abs(high - zero) <= 4 * max(sample_margin, noise):
            candidate = None
            continue
        if candidate is not None and abs(high - candidate[0]) <= max(sample_margin, noise):
            break
        candidate = (high, high_block)

    high, high_margin, high_sample_margin = _plateau((candidate[1], high_block), window)
    calibration = Calibration(zero, (high - zero) / reference_code,
                              max(margin, high_margin), period, len(block.analog),
                              window, max(sample_margin, high_sample_margin))
    transitions = []
    for code in (0, reference_code) * 4:
        previous, target = (zero, high) if code else (high, zero)
        transitions.append(acquisition.step(calibration, code, previous, target))

    intervals = np.diff(receipts) / 1e6
    seconds = (receipts[-1] - receipts[0]) / 1e9
    rows = [
        _row("PDA USB-status roundtrip (ms)", usb_ms),
        _row("PDA ADC-sampleperiode (µs)", [period * 1e6] if period else []),
        _row("PDA blokinterval op de host (ms)", intervals),
        _row("PDA ontvangen samples/s per kanaal", [samples / seconds] if seconds > 0 else []),
        _row("Kanaal 2 meetmarge per sample (DAC-codes)", [calibration.sample_margin / abs(calibration.per_code)]),
        _row("Kanaal 2 meetmarge per venster (DAC-codes)", [calibration.margin / abs(calibration.per_code)]),
        _row("Minimale DAC-stap (codes)", [calibration.minimum_step]),
        *step_rows(transitions),
    ]
    return calibration, rows


def _row(metric, values):
    return {
        "meting": metric,
        "mediaan": float(np.median(values)) if len(values) else None,
        "maximum": float(np.max(values)) if len(values) else None,
        "n": len(values),
    }


def step_rows(steps):
    return [
        _row("I²C DAC-write (ms)", [step.write_ms for step in steps]),
        _row("DAC-write tot bevestiging op de host (ms)", [step.observed_ms for step in steps]),
        _row("PDA ontvangst tot verwerking (ms)", [step.queue_ms for step in steps]),
        _row("DAC-flank 10–90% op kanaal 2 (µs)", [step.edge_us for step in steps if step.edge_us is not None]),
        _row("Tijd per meetpunt (ms)", [step.point_ms for step in steps]),
    ]
