from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import numpy as np

from pda36au import PDA36AU, PDABlock


def configure_gain(
    pda: PDA36AU,
    gain_db: int | None,
) -> None:

    if gain_db is None:
        return

    pda.set_gain_db(
        gain_db
    )

    actual = pda.get_gain_db()

    if actual != gain_db:
        raise RuntimeError(
            f"Gain gevraagd: {gain_db} dB, "
            f"maar teruggelezen: {actual} dB"
        )


def print_device_info(
    pda: PDA36AU,
) -> None:

    print(
        f"Product:          "
        f"{pda.product_name}"
    )

    print(
        f"USB serial:       "
        f"{pda.serial_number}"
    )

    try:
        print(
            f"Device serial:    "
            f"{pda.get_device_serial()!r}"
        )
    except Exception as exc:
        print(
            f"Device serial:    "
            f"<error: {exc}>"
        )

    try:
        print(
            f"Firmware:         "
            f"{pda.get_firmware_version()!r}"
        )
    except Exception as exc:
        print(
            f"Firmware:         "
            f"<error: {exc}>"
        )

    gain_index = pda.get_gain()
    gain_db = pda.get_gain_db()

    print(
        f"Gain index:       "
        f"{gain_index}"
    )

    print(
        f"Gain dB:          "
        f"{gain_db}"
    )

    print(
        f"Mode raw:         "
        f"{pda.get_mode()}"
    )

    print(
        f"Buffer length:    "
        f"{pda.get_adc_buffer_length()}"
    )

    print(
        f"Time division:    "
        f"{pda.get_time_division()}"
    )

    print(
        f"Time subdivision: "
        f"{pda.get_time_subdivision()}"
    )

    scale = pda.get_scale(
        refresh=True
    )

    print()
    print("Device scale")

    print(
        f"  X conversion:   "
        f"{scale.x_conversion}"
    )

    print(
        f"  X unit:         "
        f"{scale.x_unit!r}"
    )

    print(
        f"  Y offset:       "
        f"{scale.y_offset}"
    )

    print(
        f"  Y conversion:   "
        f"{scale.y_conversion}"
    )

    print(
        f"  Y unit:         "
        f"{scale.y_unit!r}"
    )

    if (
        scale.sample_period_seconds
        is not None
    ):
        print(
            "  Sample period:  "
            f"{scale.sample_period_seconds:.9g} s"
        )

    if scale.sample_rate_hz is not None:
        print(
            "  Sample rate:    "
            f"{scale.sample_rate_hz:.9g} Hz"
        )

    if scale.zero_code is not None:
        print(
            "  Zero ADC code:  "
            f"{scale.zero_code:.6f}"
        )

    print()
    print("Trigger/config")

    print(
        f"  Trigger channel:   "
        f"{pda.get_trigger_channel()}"
    )

    print(
        f"  Trigger slope:     "
        f"{pda.get_trigger_slope()}"
    )

    print(
        f"  Trigger mode:      "
        f"{pda.get_trigger_mode()}"
    )

    print(
        f"  Trigger threshold: "
        f"{pda.get_trigger_threshold()}"
    )

    print(
        f"  Trigger offset:    "
        f"{pda.get_trigger_offset()}"
    )

    print(
        f"  External trigger:  "
        f"{pda.get_external_trigger_mode()}"
    )

    print(
        f"  Phase delay:       "
        f"{pda.get_phase_delay()}"
    )

    print(
        f"  Wavelength:        "
        f"{pda.get_wavelength()}"
    )

    print(
        f"  Wavelength coeff:  "
        f"{pda.get_wavelength_coefficient()}"
    )

    try:
        print(
            f"  Missed blocks:     "
            f"{pda.get_missed_blocks()}"
        )
    except Exception as exc:
        print(
            f"  Missed blocks:     "
            f"<error: {exc}>"
        )


def block_arrays(
    block: PDABlock,
    raw: bool,
):
    if raw:
        x = np.arange(
            len(block.channel_0_counts),
            dtype=np.float64,
        )

        return (
            x,
            block.channel_0_counts,
            block.channel_1_counts,
            "Sample index",
            "ADC counts t.o.v. 0x8000",
        )

    return (
        block.x,
        block.channel_0_scaled,
        block.channel_1_scaled,
        block.x_unit,
        block.y_unit,
    )


def print_block_stats(
    block: PDABlock,
    raw: bool,
) -> None:

    (
        x,
        ch0,
        ch1,
        xunit,
        yunit,
    ) = block_arrays(
        block,
        raw,
    )

    print()

    print(
        f"Samples per channel: "
        f"{len(ch0)}"
    )

    print(
        "Channel 0: "
        f"mean={ch0.mean():.6g} {yunit}, "
        f"min={ch0.min():.6g}, "
        f"max={ch0.max():.6g}, "
        f"std={ch0.std():.6g}"
    )

    print(
        "Channel 1: "
        f"mean={ch1.mean():.6g} {yunit}, "
        f"min={ch1.min():.6g}, "
        f"max={ch1.max():.6g}, "
        f"std={ch1.std():.6g}"
    )

    if len(x) > 1:
        print(
            f"X range: "
            f"{x[0]:.6g} .. "
            f"{x[-1]:.6g} "
            f"{xunit}"
        )


def save_block(
    filename: str,
    block: PDABlock,
) -> None:

    path = Path(
        filename
    )

    np.savez(
        path,
        raw_bytes=np.frombuffer(
            block.raw,
            dtype=np.uint8,
        ),
        x=block.x,
        channel_0_raw=block.channel_0_raw,
        channel_1_raw=block.channel_1_raw,
        channel_0_counts=block.channel_0_counts,
        channel_1_counts=block.channel_1_counts,
        channel_0_scaled=block.channel_0_scaled,
        channel_1_scaled=block.channel_1_scaled,
        x_unit=np.array(
            block.x_unit
        ),
        y_unit=np.array(
            block.y_unit
        ),
        detector_channel=np.array(
            block.detector_channel
        ),
    )

    print(
        f"Opgeslagen als: {path}"
    )


def create_qt():
    """
    Import Qt only when a GUI is actually requested.

    This keeps `info` and `--no-plot` lightweight and makes them
    usable on a headless Raspberry Pi.
    """
    try:
        from PyQt5 import (
            QtCore,
            QtWidgets,
        )

        import pyqtgraph as pg

    except ImportError as exc:
        raise RuntimeError(
            "PyQtGraph/PyQt5 ontbreekt.\n"
            "Installeer bijvoorbeeld:\n"
            "  sudo apt install python3-pyqt5\n"
            "  pip install pyqtgraph"
        ) from exc

    # Low-cost rendering settings for Raspberry Pi.
    pg.setConfigOptions(
        antialias=False,
        useOpenGL=False,
    )

    return (
        QtCore,
        QtWidgets,
        pg,
    )


def plot_single_pyqtgraph(
    block: PDABlock,
    raw: bool,
) -> None:

    (
        QtCore,
        QtWidgets,
        pg,
    ) = create_qt()

    app = (
        QtWidgets.QApplication.instance()
        or QtWidgets.QApplication(
            sys.argv
        )
    )

    (
        x,
        ch0,
        ch1,
        xunit,
        yunit,
    ) = block_arrays(
        block,
        raw,
    )

    window = (
        pg.GraphicsLayoutWidget(
            title="PDA36AU single scan"
        )
    )

    window.resize(
        1000,
        600,
    )

    plot = window.addPlot(
        title="PDA36AU single scan"
    )

    plot.setLabel(
        "bottom",
        xunit,
    )

    plot.setLabel(
        "left",
        yunit,
    )

    plot.showGrid(
        x=True,
        y=True,
        alpha=0.25,
    )

    plot.addLegend()

    plot.plot(
        x,
        ch0,
        name="Channel 0",
    )

    plot.plot(
        x,
        ch1,
        name="Channel 1",
    )

    window.show()

    signal.signal(
        signal.SIGINT,
        lambda *_: app.quit(),
    )

    app.exec_()


def run_single(
    pda: PDA36AU,
    *,
    buffer_length: int,
    raw: bool,
    show_plot: bool,
    save: str | None,
) -> None:

    print(
        "Single scan..."
    )

    block = pda.single_scan(
        buffer_length=buffer_length,
        timeout=5.0,
    )

    print_block_stats(
        block,
        raw,
    )

    if save:
        save_block(
            save,
            block,
        )

    if show_plot:
        plot_single_pyqtgraph(
            block,
            raw,
        )


def run_continuous_terminal(
    pda: PDA36AU,
    *,
    buffer_length: int,
    raw: bool,
) -> None:
    """
    Lightweight monitor.

    The ADC reader itself runs continuously in pda36au.py.
    We only print the newest block at about 10 Hz, avoiding hundreds
    of terminal lines per second.
    """
    pda.start_continuous(
        buffer_length=buffer_length
    )

    print(
        "Continuous scan gestart. "
        "Stop met Ctrl+C."
    )

    try:
        while True:
            block = pda.read_latest_block(
                timeout=1.0
            )

            if block is None:
                continue

            (
                _,
                ch0,
                ch1,
                _,
                yunit,
            ) = block_arrays(
                block,
                raw,
            )

            print(
                f"CH0={ch0.mean():11.6g}  "
                f"CH1={ch1.mean():11.6g}  "
                f"[{yunit}]"
            )

            time.sleep(
                0.1
            )

    except KeyboardInterrupt:
        print()
        print(
            "Continuous scan gestopt."
        )

    finally:
        pda.stop_continuous()


def run_continuous_gui(
    pda: PDA36AU,
    *,
    buffer_length: int,
    raw: bool,
    fps: float,
) -> None:
    """
    PyQtGraph live view.

    IMPORTANT:
    USB acquisition and GUI refresh are fully decoupled.

    The driver thread keeps reading endpoint 0x83 as fast as data
    arrives. The Qt timer only asks for the newest block at `fps`.
    """
    (
        QtCore,
        QtWidgets,
        pg,
    ) = create_qt()

    app = (
        QtWidgets.QApplication.instance()
        or QtWidgets.QApplication(
            sys.argv
        )
    )

    window = (
        pg.GraphicsLayoutWidget(
            title="PDA36AU Live"
        )
    )

    window.resize(
        1100,
        650,
    )

    plot = window.addPlot(
        title="PDA36AU"
    )

    plot.showGrid(
        x=True,
        y=True,
        alpha=0.25,
    )

    plot.addLegend()

    curve0 = plot.plot(
        name="Channel 0",
    )

    curve1 = plot.plot(
        name="Channel 1",
    )

    status_label = (
        pg.LabelItem(
            justify="left"
        )
    )

    window.nextRow()

    window.addItem(
        status_label
    )

    pda.start_continuous(
        buffer_length=buffer_length
    )

    scale = pda._scale_cache

    if (
        scale is not None
        and scale.sample_rate_hz is not None
    ):
        rate_text = (
            f"{scale.sample_rate_hz / 1e6:.3f} MS/s"
        )
    else:
        rate_text = "sample rate onbekend"

    gain = pda.get_gain_db()

    print(
        "Continuous GUI gestart. "
        f"Gain={gain} dB, "
        f"GUI={fps:g} FPS, "
        f"{rate_text}"
    )

    print(
        "Sluit het venster of druk Ctrl+C."
    )

    window.show()

    interval_ms = max(
        20,
        int(round(1000.0 / fps)),
    )

    timer = QtCore.QTimer()

    state = {
        "display_blocks": 0,
        "first_block": True,
        "closing": False,
    }

    def update_plot():
        try:
            block = pda.read_latest_block(
                timeout=0.0
            )

        except Exception as exc:
            print(
                "Acquisitie-fout:",
                exc,
            )

            app.quit()
            return

        if block is None:
            return

        state["display_blocks"] += 1

        (
            x,
            ch0,
            ch1,
            xunit,
            yunit,
        ) = block_arrays(
            block,
            raw,
        )

        curve0.setData(
            x,
            ch0,
        )

        curve1.setData(
            x,
            ch1,
        )

        if state["first_block"]:
            plot.setLabel(
                "bottom",
                xunit,
            )

            plot.setLabel(
                "left",
                yunit,
            )

            if len(x) > 1:
                plot.setXRange(
                    float(x[0]),
                    float(x[-1]),
                    padding=0.0,
                )

            state["first_block"] = False

        # Manual Y range is cheaper and more predictable than continuously
        # invoking full auto-range on a Raspberry Pi.
        ymin = min(
            float(np.min(ch0)),
            float(np.min(ch1)),
        )

        ymax = max(
            float(np.max(ch0)),
            float(np.max(ch1)),
        )

        span = ymax - ymin

        if span <= 0:
            span = 1.0

        margin = (
            span * 0.08
        )

        plot.setYRange(
            ymin - margin,
            ymax + margin,
            padding=0.0,
        )

        status_label.setText(
            f"Gain: {gain} dB    "
            f"GUI: {fps:g} FPS    "
            f"CH0 mean: {np.mean(ch0):.6g} {yunit}    "
            f"CH1 mean: {np.mean(ch1):.6g} {yunit}"
        )

    def cleanup():
        if state["closing"]:
            return

        state["closing"] = True

        timer.stop()

        try:
            pda.stop_continuous()
        except Exception as exc:
            print(
                "Fout bij stoppen:",
                exc,
            )

    timer.timeout.connect(
        update_plot
    )

    timer.start(
        interval_ms
    )

    app.aboutToQuit.connect(
        cleanup
    )

    signal.signal(
        signal.SIGINT,
        lambda *_: app.quit(),
    )

    try:
        app.exec_()

    finally:
        cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Thorlabs PDA36AU "
            "Linux/Python driver"
        )
    )

    parser.add_argument(
        "mode",
        choices=[
            "info",
            "single",
            "continuous",
        ],
    )

    parser.add_argument(
        "--gain",
        type=int,
        choices=[
            0,
            10,
            20,
            30,
            40,
            50,
            60,
            70,
        ],
        default=None,
        help=(
            "Gain in dB. "
            "Als niet opgegeven blijft "
            "de huidige gain actief."
        ),
    )

    parser.add_argument(
        "--buffer",
        type=int,
        default=2500,
        help=(
            "Samples per kanaal per block "
            "(default: 2500)"
        ),
    )

    parser.add_argument(
        "--fps",
        type=float,
        default=20.0,
        help=(
            "PyQtGraph refresh rate "
            "(default: 20 FPS)"
        ),
    )

    parser.add_argument(
        "--no-plot",
        action="store_true",
        help=(
            "Geen GUI openen"
        ),
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help=(
            "Toon ADC counts in plaats "
            "van gekalibreerde volts"
        ),
    )

    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help=(
            "Bij single: sla block op "
            "als .npz"
        ),
    )

    parser.add_argument(
        "--verbose-usb",
        action="store_true",
    )

    parser.add_argument(
        "--detector-channel",
        type=int,
        choices=[0, 1],
        default=0,
    )

    args = parser.parse_args()

    if args.fps <= 0:
        parser.error(
            "--fps moet groter dan 0 zijn"
        )

    return args


def main() -> None:
    args = parse_args()

    with PDA36AU(
        verbose=args.verbose_usb,
        detector_channel=args.detector_channel,
    ) as pda:

        configure_gain(
            pda,
            args.gain,
        )

        if args.mode == "info":
            print_device_info(
                pda
            )

            return

        gain = pda.get_gain_db()

        print(
            f"Product: {pda.product_name}"
        )

        print(
            f"Serial:  {pda.serial_number}"
        )

        print(
            f"Gain:    {gain} dB"
        )

        if args.mode == "single":
            run_single(
                pda,
                buffer_length=args.buffer,
                raw=args.raw,
                show_plot=not args.no_plot,
                save=args.save,
            )

        elif args.mode == "continuous":
            if args.no_plot:
                run_continuous_terminal(
                    pda,
                    buffer_length=args.buffer,
                    raw=args.raw,
                )

            else:
                run_continuous_gui(
                    pda,
                    buffer_length=args.buffer,
                    raw=args.raw,
                    fps=args.fps,
                )


if __name__ == "__main__":
    main()