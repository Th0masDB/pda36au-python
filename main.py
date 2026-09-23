"""
main.py

Voorbeelden
-----------
Device-info:
    python main.py info

Een scan:
    python main.py single
    python main.py single --save scan.npz

Continuous:
    python main.py continuous
    python main.py continuous --no-plot

Raw ADC counts plotten in plaats van device units:
    python main.py continuous --raw

Als later blijkt dat de fysieke kanalen omgewisseld zijn:
    python main.py continuous --detector-channel 1
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from pda36au import PDA36AU, PDABlock


def print_device_info(pda: PDA36AU) -> None:
    print(f"Product:          {pda.product_name}")
    print(f"USB serial:       {pda.serial_number}")

    try:
        print(f"Device serial:    {pda.get_device_serial()!r}")
    except Exception as exc:
        print(f"Device serial:    <error: {exc}>")

    try:
        print(f"Firmware:         {pda.get_firmware_version()!r}")
    except Exception as exc:
        print(f"Firmware:         <error: {exc}>")

    gain = pda.get_gain()
    gain_db = pda.get_gain_db()

    print(f"Gain index:       {gain}")
    print(f"Gain dB:          {gain_db if gain_db is not None else 'unknown'}")
    print(f"Mode raw:         {pda.get_mode()}")
    print(f"Buffer length:    {pda.get_adc_buffer_length()}")
    print(f"Time division:    {pda.get_time_division()}")
    print(f"Time subdivision: {pda.get_time_subdivision()}")

    scale = pda.get_scale(refresh=True)

    print()
    print("Device scale")
    print(f"  X conversion:   {scale.x_conversion!r}")
    print(f"  X unit:         {scale.x_unit!r}")
    print(f"  Y offset:       {scale.y_offset!r}")
    print(f"  Y conversion:   {scale.y_conversion!r}")
    print(f"  Y unit:         {scale.y_unit!r}")
    print(f"  X scale valid:  {scale.x_valid}")
    print(f"  Y scale valid:  {scale.y_valid}")

    if scale.sample_period_seconds is not None:
        print(
            f"  Sample period:  {scale.sample_period_seconds:.9g} s"
        )

    if scale.sample_rate_hz is not None:
        print(
            f"  Sample rate:    {scale.sample_rate_hz:.9g} Hz"
        )

    if scale.zero_code is not None:
        print(
            f"  Zero ADC code:  {scale.zero_code:.6f}"
        )

    if scale.y_valid:
        print(
            f"  Y/count:        {scale.y_conversion:.12g} "
            f"{scale.y_unit}/count"
        )

    try:
        n = pda.get_adc_buffer_length()
        if scale.sample_period_seconds is not None:
            print(
                f"  Block duration: {n * scale.sample_period_seconds:.9g} s "
                f"({n} samples/channel)"
            )
    except Exception:
        pass

    print()
    print("Trigger/config")
    print(f"  Trigger channel:   {pda.get_trigger_channel()}")
    print(f"  Trigger slope:     {pda.get_trigger_slope()}")
    print(f"  Trigger mode:      {pda.get_trigger_mode()}")
    print(f"  Trigger threshold: {pda.get_trigger_threshold()}")
    print(f"  Trigger offset:    {pda.get_trigger_offset()}")
    print(f"  External trigger:  {pda.get_external_trigger_mode()}")
    print(f"  Phase delay:       {pda.get_phase_delay()}")
    print(f"  Wavelength:        {pda.get_wavelength()}")
    print(f"  Wavelength coeff:  {pda.get_wavelength_coefficient()}")

    try:
        print(f"  Missed blocks:     {pda.get_missed_blocks()}")
    except Exception as exc:
        print(f"  Missed blocks:     <error: {exc}>")


def block_arrays(block: PDABlock, raw: bool):
    if raw:
        return (
            np.arange(len(block.channel_0_counts), dtype=np.float64),
            block.channel_0_counts,
            block.channel_1_counts,
            "Sample index",
            "ADC counts relative to 0x8000",
        )

    return (
        block.x,
        block.channel_0_scaled,
        block.channel_1_scaled,
        block.x_unit or "device-x",
        block.y_unit or "device-y",
    )


def print_block_stats(block: PDABlock, raw: bool) -> None:
    x, ch0, ch1, xunit, yunit = block_arrays(block, raw)

    print()
    print(f"Block: {len(ch0)} samples/channel")
    print(
        f"Channel 0: mean={ch0.mean():.6g} {yunit}, "
        f"min={ch0.min():.6g}, max={ch0.max():.6g}, "
        f"std={ch0.std():.6g}"
    )
    print(
        f"Channel 1: mean={ch1.mean():.6g} {yunit}, "
        f"min={ch1.min():.6g}, max={ch1.max():.6g}, "
        f"std={ch1.std():.6g}"
    )

    if len(x) > 1:
        print(
            f"X range: {x[0]:.6g} .. {x[-1]:.6g} {xunit}"
        )


def save_block(filename: str, block: PDABlock) -> None:
    path = Path(filename)

    np.savez(
        path,
        raw_bytes=np.frombuffer(block.raw, dtype=np.uint8),
        x=block.x,
        channel_0_raw=block.channel_0_raw,
        channel_1_raw=block.channel_1_raw,
        channel_0_counts=block.channel_0_counts,
        channel_1_counts=block.channel_1_counts,
        channel_0_scaled=block.channel_0_scaled,
        channel_1_scaled=block.channel_1_scaled,
        x_unit=np.array(block.x_unit),
        y_unit=np.array(block.y_unit),
        detector_channel=np.array(block.detector_channel),
    )

    print(f"Opgeslagen als: {path}")


def plot_single(block: PDABlock, raw: bool) -> None:
    x, ch0, ch1, xunit, yunit = block_arrays(block, raw)

    plt.figure()
    plt.plot(x, ch0, label="Channel 0 (USB first half)")
    plt.plot(x, ch1, label="Channel 1 (USB second half)")
    plt.xlabel(xunit)
    plt.ylabel(yunit)
    plt.title("PDA36AU single scan")
    plt.legend()
    plt.tight_layout()
    plt.show()


def run_single(
    pda: PDA36AU,
    buffer_length: int,
    show_plot: bool,
    save: str | None,
    raw: bool,
) -> None:
    print("Single scan...")

    block = pda.single_scan(
        buffer_length=buffer_length,
        timeout=5.0,
    )

    print_block_stats(block, raw=raw)

    if save:
        save_block(save, block)

    if show_plot:
        plot_single(block, raw=raw)


def run_continuous(
    pda: PDA36AU,
    buffer_length: int,
    show_plot: bool,
    raw: bool,
) -> None:
    print("Continuous scan starten...")
    print("Stop met Ctrl+C.")

    pda.start_continuous(
        buffer_length=buffer_length
    )

    try:
        if not show_plot:
            block_number = 0

            while True:
                block = pda.read_block(timeout=5.0)
                block_number += 1

                _, ch0, ch1, _, yunit = block_arrays(
                    block,
                    raw,
                )

                print(
                    f"block={block_number:8d}  "
                    f"CH0 mean={ch0.mean():11.5g}  "
                    f"CH1 mean={ch1.mean():11.5g}  "
                    f"[{yunit}]"
                )

        else:
            plt.ion()

            fig = plt.figure()
            ax = fig.add_subplot(111)

            block = pda.read_block(timeout=5.0)
            block_number = 1

            x, ch0, ch1, xunit, yunit = block_arrays(
                block,
                raw,
            )

            line_0, = ax.plot(
                x,
                ch0,
                label="Channel 0 (USB first half)",
            )

            line_1, = ax.plot(
                x,
                ch1,
                label="Channel 1 (USB second half)",
            )

            ax.set_xlabel(xunit)
            ax.set_ylabel(yunit)
            ax.legend()
            fig.tight_layout()

            while plt.fignum_exists(fig.number):
                if block_number > 1:
                    block = pda.read_block(timeout=5.0)
                    x, ch0, ch1, xunit, yunit = block_arrays(
                        block,
                        raw,
                    )

                line_0.set_xdata(x)
                line_1.set_xdata(x)
                line_0.set_ydata(ch0)
                line_1.set_ydata(ch1)

                minimum = min(
                    float(ch0.min()),
                    float(ch1.min()),
                )
                maximum = max(
                    float(ch0.max()),
                    float(ch1.max()),
                )

                margin = max(
                    abs(maximum - minimum) * 0.1,
                    1e-12,
                )

                ax.set_xlim(
                    float(x[0]),
                    float(x[-1]) if len(x) > 1 else float(x[0] + 1),
                )
                ax.set_ylim(
                    minimum - margin,
                    maximum + margin,
                )

                ax.set_xlabel(xunit)
                ax.set_ylabel(yunit)

                ax.set_title(
                    "PDA36AU continuous "
                    f"| block {block_number} "
                    f"| CH0 mean {ch0.mean():.5g} "
                    f"| CH1 mean {ch1.mean():.5g}"
                )

                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                plt.pause(0.001)

                block_number += 1

    except KeyboardInterrupt:
        print()
        print("Continuous scan gestopt.")

    finally:
        pda.stop_continuous()

        if show_plot:
            plt.ioff()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PDA36AU Linux/Python acquisition"
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
        "--buffer",
        type=int,
        default=2500,
        help="Sampleframes per channel per block (default 2500)",
    )

    parser.add_argument(
        "--no-plot",
        action="store_true",
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help="Gebruik centred ADC counts i.p.v. firmware device-units",
    )

    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Bij single mode: schrijf een .npz bestand",
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
        help=(
            "Fysieke detector-alias. Default 0; gebruik 1 als later blijkt "
            "dat de twee fysieke kanalen omgekeerd zijn."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with PDA36AU(
        verbose=args.verbose_usb,
        detector_channel=args.detector_channel,
    ) as pda:

        if args.mode == "info":
            print_device_info(pda)
            return

        # Kort overzicht voor meetmodes.
        gain = pda.get_gain()
        gain_db = pda.get_gain_db()

        print(f"Product: {pda.product_name}")
        print(f"Serial:  {pda.serial_number}")
        print(
            f"Gain:    index {gain}"
            + (
                f" ({gain_db} dB)"
                if gain_db is not None
                else ""
            )
        )

        scale = pda.get_scale(refresh=True)
        print(
            f"Scale:   X={scale.x_conversion!r} {scale.x_unit!r}, "
            f"Y={scale.y_conversion!r}, offset={scale.y_offset!r} "
            f"{scale.y_unit!r}"
        )

        if scale.sample_rate_hz is not None:
            print(f"Rate:    {scale.sample_rate_hz:.6g} samples/s/channel")

        if scale.zero_code is not None:
            print(f"Zero:    ADC code {scale.zero_code:.3f}")

        if args.mode == "single":
            run_single(
                pda=pda,
                buffer_length=args.buffer,
                show_plot=not args.no_plot,
                save=args.save,
                raw=args.raw,
            )

        elif args.mode == "continuous":
            run_continuous(
                pda=pda,
                buffer_length=args.buffer,
                show_plot=not args.no_plot,
                raw=args.raw,
            )


if __name__ == "__main__":
    main()
