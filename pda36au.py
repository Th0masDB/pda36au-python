from __future__ import annotations

import math
import queue
import struct
import threading
from dataclasses import dataclass

import numpy as np
import usb.core
import usb.util


@dataclass(frozen=True)
class PDAScale:
    x_conversion: float
    x_unit: str
    y_offset: float
    y_conversion: float
    y_unit: str

    @property
    def x_valid(self) -> bool:
        return (
            math.isfinite(self.x_conversion)
            and self.x_conversion != 0.0
        )

    @property
    def y_valid(self) -> bool:
        return (
            math.isfinite(self.y_conversion)
            and self.y_conversion != 0.0
            and math.isfinite(self.y_offset)
        )

    @property
    def zero_code(self) -> float | None:
        if not self.y_valid:
            return None
        return self.y_offset / self.y_conversion

    @property
    def sample_period_seconds(self) -> float | None:
        if not self.x_valid:
            return None

        factors = {
            "s": 1.0,
            "ms": 1e-3,
            "us": 1e-6,
            "µs": 1e-6,
            "ns": 1e-9,
        }

        factor = factors.get(self.x_unit.strip().lower())
        if factor is None:
            return None

        return self.x_conversion * factor

    @property
    def sample_rate_hz(self) -> float | None:
        period = self.sample_period_seconds

        if period is None or period == 0.0:
            return None

        return 1.0 / period


@dataclass
class PDABlock:
    raw: bytes

    channel_0_raw: np.ndarray
    channel_1_raw: np.ndarray

    channel_0_counts: np.ndarray
    channel_1_counts: np.ndarray

    x: np.ndarray

    channel_0_scaled: np.ndarray
    channel_1_scaled: np.ndarray

    x_unit: str
    y_unit: str

    detector_channel: int = 0

    @property
    def detector_raw(self) -> np.ndarray:
        if self.detector_channel == 0:
            return self.channel_0_raw
        return self.channel_1_raw

    @property
    def analog_raw(self) -> np.ndarray:
        if self.detector_channel == 0:
            return self.channel_1_raw
        return self.channel_0_raw

    @property
    def detector_counts(self) -> np.ndarray:
        if self.detector_channel == 0:
            return self.channel_0_counts
        return self.channel_1_counts

    @property
    def analog_counts(self) -> np.ndarray:
        if self.detector_channel == 0:
            return self.channel_1_counts
        return self.channel_0_counts

    @property
    def detector(self) -> np.ndarray:
        if self.detector_channel == 0:
            return self.channel_0_scaled
        return self.channel_1_scaled

    @property
    def analog(self) -> np.ndarray:
        if self.detector_channel == 0:
            return self.channel_1_scaled
        return self.channel_0_scaled


class PDA36AU:
    VID = 0x1313
    PID = 0x100D

    INTERFACE = 0

    EP_COMMAND_OUT = 0x02
    EP_RESPONSE_IN = 0x82
    EP_DATA_IN = 0x83

    ADC_MIDSCALE = 32768

    DEFAULT_COMMAND_TIMEOUT_MS = 1000
    DEFAULT_DATA_TIMEOUT_MS = 1000

    # Read commands
    CMD_START_ADC = 0x01
    CMD_GET_STATE = 0x02
    CMD_ADC_STATUS = 0x03
    CMD_GET_TIME_DIV = 0x04
    CMD_GET_TIME_SUBDIV = 0x05
    CMD_GET_MODE = 0x06
    CMD_GET_GAIN = 0x07
    CMD_GET_POT = 0x08
    CMD_GET_WAVELENGTH = 0x09
    CMD_GET_WAVELENGTH_COEFF = 0x0A
    CMD_GET_PHASE_DELAY = 0x0B
    CMD_GET_TRIGGER_CHANNEL = 0x0C
    CMD_GET_TRIGGER_SLOPE = 0x0D
    CMD_GET_TRIGGER_MODE = 0x0E
    CMD_GET_TRIGGER_THRESHOLD = 0x0F
    CMD_GET_TRIGGER_OFFSET = 0x10
    CMD_GET_TRIGGER_FREQ = 0x11

    CMD_GET_CONVERSION_X = 0x12
    CMD_GET_UNITS_X = 0x13
    CMD_GET_OFFSET_Y = 0x14
    CMD_GET_CONVERSION_Y = 0x15
    CMD_GET_UNITS_Y = 0x16

    CMD_GET_ADC_BUFFER_LENGTH = 0x17
    CMD_GET_MISSED_BLOCKS = 0x18

    CMD_GET_RESPONSE_ENTRIES = 0x1A
    CMD_GET_RESPONSE_LOW = 0x1B
    CMD_GET_RESPONSE_HIGH = 0x1C
    CMD_GET_RESPONSE_STEP = 0x1D

    CMD_GET_PID_P = 0x33
    CMD_GET_PID_I = 0x35
    CMD_GET_PID_D = 0x37
    CMD_GET_LIA = 0x39

    CMD_GET_SERIAL = 0x3A
    CMD_GET_FW_VERSION = 0x3C
    CMD_GET_EXT_TRIGGER_MODE = 0x3D

    # Write commands
    CMD_SAVE_SETTINGS = 0x20
    CMD_SET_GAIN = 0x21
    CMD_SET_POT = 0x22
    CMD_SET_TRIGGER_CHANNEL = 0x23
    CMD_SET_TRIGGER_THRESHOLD = 0x24
    CMD_UPDATE_PDA = 0x25
    CMD_SET_TIME_DIV = 0x26
    CMD_SET_MODE = 0x28
    CMD_SET_WAVELENGTH = 0x29
    CMD_SET_PHASE_DELAY = 0x2B
    CMD_SET_TRIGGER_SLOPE = 0x2C
    CMD_SET_TRIGGER_MODE = 0x2D
    CMD_SET_TRIGGER_OFFSET = 0x2E
    CMD_SET_ADC_BUFFER_LENGTH = 0x2F
    CMD_SET_CONTINUOUS = 0x30
    CMD_ABORT = 0x31

    CMD_SET_PID_P = 0x34
    CMD_SET_PID_I = 0x36
    CMD_SET_PID_D = 0x38

    CMD_SET_EXT_TRIGGER_MODE = 0x3E

    def __init__(
        self,
        command_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
        data_timeout_ms: int = DEFAULT_DATA_TIMEOUT_MS,
        verbose: bool = False,
        detector_channel: int = 0,
        queue_size: int = 32,
    ):
        if detector_channel not in (0, 1):
            raise ValueError("detector_channel moet 0 of 1 zijn")

        self.command_timeout_ms = command_timeout_ms
        self.data_timeout_ms = data_timeout_ms
        self.verbose = verbose
        self.detector_channel = detector_channel

        self.dev = usb.core.find(
            idVendor=self.VID,
            idProduct=self.PID,
        )

        if self.dev is None:
            raise RuntimeError(
                "PDA36AU niet gevonden. "
                "Controleer met: lsusb -d 1313:100d"
            )

        try:
            if self.dev.is_kernel_driver_active(self.INTERFACE):
                self.dev.detach_kernel_driver(self.INTERFACE)
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(
            self.dev,
            self.INTERFACE,
        )

        self._claimed = True

        self._command_lock = threading.Lock()

        self._stream_thread: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None

        self._stream_stop = threading.Event()
        self._stream_armed = threading.Event()
        self._stream_started = threading.Event()

        self._stream_error: Exception | None = None
        self._last_adc_status: bytes | None = None

        self._block_queue: queue.Queue[bytes] = queue.Queue(
            maxsize=queue_size
        )

        self._buffer_length: int | None = None
        self._expected_block_bytes: int | None = None

        self._scale_cache: PDAScale | None = None

    # ------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------

    def __enter__(self) -> "PDA36AU":
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.close()

    @property
    def product_name(self) -> str:
        try:
            return usb.util.get_string(
                self.dev,
                self.dev.iProduct,
            )
        except Exception:
            return "PDA36AU"

    @property
    def serial_number(self) -> str:
        try:
            return usb.util.get_string(
                self.dev,
                self.dev.iSerialNumber,
            )
        except Exception:
            return "<unknown>"

    def close(self) -> None:
        if (
            self._stream_thread is not None
            and self._stream_thread.is_alive()
        ):
            try:
                self.stop_continuous()
            except Exception:
                pass

        if getattr(self, "_claimed", False):
            try:
                usb.util.release_interface(
                    self.dev,
                    self.INTERFACE,
                )
            except usb.core.USBError:
                pass

            self._claimed = False

        usb.util.dispose_resources(self.dev)

    # ------------------------------------------------------------
    # Low-level command interface
    # ------------------------------------------------------------

    def command(
        self,
        payload: bytes | bytearray | list[int],
        *,
        response_size: int = 512,
        timeout_ms: int | None = None,
    ) -> bytes:

        if timeout_ms is None:
            timeout_ms = self.command_timeout_ms

        tx = bytes(payload)

        with self._command_lock:
            if self.verbose:
                print(
                    f"TX 0x{self.EP_COMMAND_OUT:02X}: "
                    f"{tx.hex(' ')}"
                )

            written = self.dev.write(
                self.EP_COMMAND_OUT,
                tx,
                timeout=timeout_ms,
            )

            if written != len(tx):
                raise RuntimeError(
                    f"Onvolledige USB-write: "
                    f"{written}/{len(tx)} bytes"
                )

            rx = bytes(
                self.dev.read(
                    self.EP_RESPONSE_IN,
                    response_size,
                    timeout=timeout_ms,
                )
            )

            if self.verbose:
                print(
                    f"RX 0x{self.EP_RESPONSE_IN:02X} "
                    f"({len(rx)} bytes): "
                    f"{rx.hex(' ')}"
                )

            return rx

    @staticmethod
    def _float32(
        rx: bytes,
        name: str,
    ) -> float:
        if len(rx) < 4:
            raise RuntimeError(
                f"{name}: antwoord te kort"
            )

        return struct.unpack(
            "<f",
            rx[:4],
        )[0]

    @staticmethod
    def _uint32(
        rx: bytes,
        name: str,
    ) -> int:
        if len(rx) < 4:
            raise RuntimeError(
                f"{name}: antwoord te kort"
            )

        return struct.unpack(
            "<I",
            rx[:4],
        )[0]

    @staticmethod
    def _ascii(
        rx: bytes,
        max_len: int,
    ) -> str:
        return (
            rx[:max_len]
            .split(b"\x00", 1)[0]
            .decode("ascii", errors="replace")
        )

    # ------------------------------------------------------------
    # Device getters
    # ------------------------------------------------------------

    def get_state(self) -> int:
        rx = self.command(
            [self.CMD_GET_STATE]
        )

        if not rx:
            raise RuntimeError(
                "Leeg antwoord op get_state()"
            )

        return int.from_bytes(
            rx[: min(2, len(rx))],
            "little",
        )

    def get_mode(self) -> int:
        return self.command(
            [self.CMD_GET_MODE]
        )[0]

    def get_gain(self) -> int:
        return self.command(
            [self.CMD_GET_GAIN]
        )[0]

    def get_gain_db(self) -> int | None:
        index = self.get_gain()

        if 0 <= index <= 7:
            return index * 10

        return None

    def get_pot(self) -> int:
        rx = self.command(
            [self.CMD_GET_POT]
        )

        return int.from_bytes(
            rx[:2],
            "little",
        )

    def get_wavelength(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_WAVELENGTH]
            ),
            "get_wavelength",
        )

    def get_wavelength_coefficient(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_WAVELENGTH_COEFF]
            ),
            "get_wavelength_coefficient",
        )

    def get_phase_delay(self) -> int:
        rx = self.command(
            [self.CMD_GET_PHASE_DELAY]
        )

        return int.from_bytes(
            rx[:2],
            "little",
        )

    def get_trigger_channel(self) -> int:
        return self.command(
            [self.CMD_GET_TRIGGER_CHANNEL]
        )[0]

    def get_trigger_slope(self) -> int:
        return self.command(
            [self.CMD_GET_TRIGGER_SLOPE]
        )[0]

    def get_trigger_mode(self) -> int:
        return self.command(
            [self.CMD_GET_TRIGGER_MODE]
        )[0]

    def get_trigger_threshold(self) -> int:
        rx = self.command(
            [self.CMD_GET_TRIGGER_THRESHOLD]
        )

        return int.from_bytes(
            rx[:2],
            "little",
        )

    def get_trigger_offset(self) -> int:
        rx = self.command(
            [self.CMD_GET_TRIGGER_OFFSET]
        )

        return int.from_bytes(
            rx[:4],
            "little",
            signed=True,
        )

    def get_trigger_frequency(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_TRIGGER_FREQ]
            ),
            "get_trigger_frequency",
        )

    def get_conversion_x(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_CONVERSION_X]
            ),
            "get_conversion_x",
        )

    def get_units_x(self) -> str:
        return self._ascii(
            self.command(
                [self.CMD_GET_UNITS_X]
            ),
            5,
        )

    def get_offset_y(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_OFFSET_Y]
            ),
            "get_offset_y",
        )

    def get_conversion_y(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_CONVERSION_Y]
            ),
            "get_conversion_y",
        )

    def get_units_y(self) -> str:
        return self._ascii(
            self.command(
                [self.CMD_GET_UNITS_Y]
            ),
            5,
        )

    def get_scale(
        self,
        refresh: bool = False,
    ) -> PDAScale:

        if (
            self._scale_cache is None
            or refresh
        ):
            self._scale_cache = PDAScale(
                x_conversion=self.get_conversion_x(),
                x_unit=self.get_units_x(),
                y_offset=self.get_offset_y(),
                y_conversion=self.get_conversion_y(),
                y_unit=self.get_units_y(),
            )

        return self._scale_cache

    def get_time_division(self) -> int:
        rx = self.command(
            [self.CMD_GET_TIME_DIV]
        )

        return int.from_bytes(
            rx[:4],
            "little",
        )

    def get_time_subdivision(self) -> int:
        rx = self.command(
            [self.CMD_GET_TIME_SUBDIV]
        )

        return int.from_bytes(
            rx[:4],
            "little",
        )

    def get_adc_buffer_length(self) -> int:
        return self._uint32(
            self.command(
                [self.CMD_GET_ADC_BUFFER_LENGTH]
            ),
            "get_adc_buffer_length",
        )

    def get_missed_blocks(self) -> int:
        return self._uint32(
            self.command(
                [self.CMD_GET_MISSED_BLOCKS]
            ),
            "get_missed_blocks",
        )

    def get_external_trigger_mode(self) -> int:
        return self.command(
            [self.CMD_GET_EXT_TRIGGER_MODE]
        )[0]

    def get_device_serial(self) -> str:
        return self._ascii(
            self.command(
                [self.CMD_GET_SERIAL]
            ),
            18,
        )

    def get_firmware_version(self) -> str:
        return self._ascii(
            self.command(
                [self.CMD_GET_FW_VERSION]
            ),
            48,
        )

    def get_pid_p(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_PID_P]
            ),
            "get_pid_p",
        )

    def get_pid_i(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_PID_I]
            ),
            "get_pid_i",
        )

    def get_pid_d(self) -> float:
        return self._float32(
            self.command(
                [self.CMD_GET_PID_D]
            ),
            "get_pid_d",
        )

    def get_adc_status(self) -> bytes:
        return self.command(
            [
                self.CMD_ADC_STATUS,
                0x01,
                0x00,
                0x00,
                0x00,
            ]
        )

    # ------------------------------------------------------------
    # Device setters
    # ------------------------------------------------------------

    def abort(self) -> bytes:
        return self.command(
            [self.CMD_ABORT, 0x01]
        )

    def start_adc(self) -> bytes:
        return self.command(
            [self.CMD_START_ADC, 0x01]
        )

    def save_settings(self) -> bytes:
        return self.command(
            [self.CMD_SAVE_SETTINGS, 0x01]
        )

    def update_pda(self) -> bytes:
        return self.command(
            [self.CMD_UPDATE_PDA, 0x01]
        )

    def set_continuous(
        self,
        mode: int,
    ) -> bytes:
        return self.command(
            bytes(
                [self.CMD_SET_CONTINUOUS]
            )
            + struct.pack(
                "<I",
                int(mode),
            )
        )

    def set_mode(
        self,
        mode: int,
    ) -> bytes:
        return self.command(
            [
                self.CMD_SET_MODE,
                int(mode) & 0xFF,
            ]
        )

    def set_gain(
        self,
        gain_index: int,
    ) -> bytes:

        gain_index = int(gain_index)

        if not 0 <= gain_index <= 7:
            raise ValueError(
                "gain_index moet 0..7 zijn"
            )

        return self.command(
            bytes(
                [self.CMD_SET_GAIN]
            )
            + struct.pack(
                "<H",
                gain_index,
            )
        )

    def set_gain_db(
        self,
        gain_db: int,
    ) -> bytes:

        gain_db = int(gain_db)

        if gain_db not in range(
            0,
            71,
            10,
        ):
            raise ValueError(
                "gain_db moet "
                "0, 10, 20, ..., 70 zijn"
            )

        return self.set_gain(
            gain_db // 10
        )

    def set_pot(
        self,
        value: int,
    ) -> bytes:
        return self.command(
            bytes(
                [self.CMD_SET_POT]
            )
            + struct.pack(
                "<H",
                int(value),
            )
        )

    def set_wavelength(
        self,
        wavelength: float,
    ) -> bytes:

        self._scale_cache = None

        return self.command(
            bytes(
                [self.CMD_SET_WAVELENGTH]
            )
            + struct.pack(
                "<f",
                float(wavelength),
            )
        )

    def set_phase_delay(
        self,
        value: int,
    ) -> bytes:
        return self.command(
            bytes(
                [self.CMD_SET_PHASE_DELAY]
            )
            + struct.pack(
                "<h",
                int(value),
            )
        )

    def set_trigger_channel(
        self,
        channel: int,
    ) -> bytes:
        return self.command(
            [
                self.CMD_SET_TRIGGER_CHANNEL,
                int(channel) & 0xFF,
            ]
        )

    def set_trigger_slope(
        self,
        slope: int,
    ) -> bytes:
        return self.command(
            [
                self.CMD_SET_TRIGGER_SLOPE,
                int(slope) & 0xFF,
            ]
        )

    def set_trigger_mode(
        self,
        mode: int,
    ) -> bytes:
        return self.command(
            [
                self.CMD_SET_TRIGGER_MODE,
                int(mode) & 0xFF,
            ]
        )

    def set_trigger_threshold(
        self,
        value: int,
    ) -> bytes:
        return self.command(
            bytes(
                [self.CMD_SET_TRIGGER_THRESHOLD]
            )
            + struct.pack(
                "<H",
                int(value),
            )
        )

    def set_trigger_offset(
        self,
        value: int,
    ) -> bytes:
        return self.command(
            bytes(
                [self.CMD_SET_TRIGGER_OFFSET]
            )
            + struct.pack(
                "<i",
                int(value),
            )
        )

    def set_external_trigger_mode(
        self,
        mode: int,
    ) -> bytes:
        return self.command(
            [
                self.CMD_SET_EXT_TRIGGER_MODE,
                int(mode) & 0xFF,
            ]
        )

    def set_adc_buffer_length(
        self,
        length: int,
    ) -> bytes:

        length = int(length)

        if not 1 <= length <= 0xFFFF:
            raise ValueError(
                "ADC buffer length moet "
                "1..65535 zijn"
            )

        rx = self.command(
            bytes(
                [self.CMD_SET_ADC_BUFFER_LENGTH]
            )
            + struct.pack(
                "<H",
                length,
            )
        )

        self._buffer_length = length
        self._expected_block_bytes = (
            length * 4
        )

        return rx

    # ------------------------------------------------------------
    # ADC block decoding
    # ------------------------------------------------------------

    def decode_block(
        self,
        raw: bytes,
    ) -> PDABlock:

        if len(raw) % 4 != 0:
            raise ValueError(
                "Databloklengte is geen "
                "veelvoud van 4"
            )

        values = np.frombuffer(
            raw,
            dtype="<u2",
        ).copy()

        n = len(values) // 2

        # DLL-confirmed channel-major layout.
        ch0_raw = values[:n]
        ch1_raw = values[n:]

        ch0_counts = (
            ch0_raw.astype(np.int32)
            - self.ADC_MIDSCALE
        )

        ch1_counts = (
            ch1_raw.astype(np.int32)
            - self.ADC_MIDSCALE
        )

        # Never perform USB commands from the decode/display path.
        # The scale is cached before acquisition starts.
        scale = self._scale_cache

        if scale is not None and scale.x_valid:
            x = (
                np.arange(
                    n,
                    dtype=np.float64,
                )
                * scale.x_conversion
            )

            x_unit = (
                scale.x_unit
                or "device-x"
            )

        else:
            x = np.arange(
                n,
                dtype=np.float64,
            )

            x_unit = "sample"

        if scale is not None and scale.y_valid:
            ch0_scaled = (
                ch0_raw.astype(np.float64)
                * scale.y_conversion
                - scale.y_offset
            )

            ch1_scaled = (
                ch1_raw.astype(np.float64)
                * scale.y_conversion
                - scale.y_offset
            )

            y_unit = (
                scale.y_unit
                or "device-y"
            )

        else:
            ch0_scaled = (
                ch0_counts.astype(
                    np.float64
                )
            )

            ch1_scaled = (
                ch1_counts.astype(
                    np.float64
                )
            )

            y_unit = "ADC counts"

        return PDABlock(
            raw=raw,
            channel_0_raw=ch0_raw,
            channel_1_raw=ch1_raw,
            channel_0_counts=ch0_counts,
            channel_1_counts=ch1_counts,
            x=x,
            channel_0_scaled=ch0_scaled,
            channel_1_scaled=ch1_scaled,
            x_unit=x_unit,
            y_unit=y_unit,
            detector_channel=self.detector_channel,
        )

    @staticmethod
    def is_empty_priming_block(
        raw: bytes,
    ) -> bool:
        return (
            len(raw) > 0
            and not any(raw)
        )

    # ------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------

    def _clear_block_queue(self) -> None:
        while True:
            try:
                self._block_queue.get_nowait()
            except queue.Empty:
                break

    def _put_block(
        self,
        raw: bytes,
    ) -> None:
        """
        The ADC reader must never block because a GUI is slow.

        If the application queue is full, discard the oldest
        display block and keep the newest one.
        """
        try:
            self._block_queue.put_nowait(
                raw
            )

        except queue.Full:
            try:
                self._block_queue.get_nowait()
            except queue.Empty:
                pass

            self._block_queue.put_nowait(
                raw
            )

    def _data_reader(self) -> None:
        assert (
            self._expected_block_bytes
            is not None
        )

        pending = bytearray()

        self._stream_armed.set()

        while not self._stream_stop.is_set():
            try:
                remaining = (
                    self._expected_block_bytes
                    - len(pending)
                )

                chunk = bytes(
                    self.dev.read(
                        self.EP_DATA_IN,
                        remaining,
                        timeout=self.data_timeout_ms,
                    )
                )

            except usb.core.USBTimeoutError:
                # A single libusb timeout is not fatal.
                continue

            except usb.core.USBError as exc:
                if not self._stream_stop.is_set():
                    self._stream_error = exc

                    if self.verbose:
                        print(
                            "PDA36AU data USB error:",
                            repr(exc),
                        )

                break

            if not chunk:
                continue

            pending.extend(chunk)

            while (
                len(pending)
                >= self._expected_block_bytes
            ):
                raw = bytes(
                    pending[
                        : self._expected_block_bytes
                    ]
                )

                del pending[
                    : self._expected_block_bytes
                ]

                if not self._stream_started.is_set():
                    continue

                if self.is_empty_priming_block(
                    raw
                ):
                    if self.verbose:
                        print(
                            "PDA36AU: "
                            "priming-block genegeerd"
                        )

                    continue

                self._put_block(
                    raw
                )

    def _status_poller(
        self,
        interval: float = 0.10,
    ) -> None:
        """
        Periodic status traffic is intentionally kept running while
        endpoint 0x83 is being read. This matches the sequence that
        proved stable on the real device.

        100 ms is sufficient and reduces load on a Raspberry Pi.
        """
        previous = None

        while not self._stream_stop.is_set():
            try:
                status = self.get_adc_status()

                self._last_adc_status = status

                if (
                    self.verbose
                    and status
                    and status[0] != previous
                ):
                    print(
                        "PDA36AU ADC status: "
                        f"0x{status[0]:02X}"
                    )

                    previous = status[0]

            except usb.core.USBTimeoutError:
                pass

            except usb.core.USBError as exc:
                if self._stream_stop.is_set():
                    break

                if self.verbose:
                    print(
                        "PDA36AU status USB error:",
                        repr(exc),
                    )

            self._stream_stop.wait(
                interval
            )

    def start_continuous(
        self,
        buffer_length: int = 2500,
    ) -> None:

        if (
            self._stream_thread is not None
            and self._stream_thread.is_alive()
        ):
            raise RuntimeError(
                "Continuous acquisitie draait al"
            )

        self._stream_error = None

        self._stream_stop.clear()
        self._stream_armed.clear()
        self._stream_started.clear()

        self._clear_block_queue()

        # Proven start sequence.
        self.abort()
        self.abort()

        self.set_continuous(1)

        self.set_adc_buffer_length(
            buffer_length
        )

        actual = (
            self.get_adc_buffer_length()
        )

        if actual != buffer_length:
            raise RuntimeError(
                "Buffer mismatch: "
                f"ingesteld={buffer_length}, "
                f"gelezen={actual}"
            )

        self._buffer_length = actual
        self._expected_block_bytes = (
            actual * 4
        )

        # Cache calibration before the ADC stream starts.
        # No scale USB queries are then required from the GUI.
        try:
            self.get_scale(
                refresh=True
            )
        except Exception as exc:
            self._scale_cache = None

            if self.verbose:
                print(
                    "PDA36AU scale query failed:",
                    repr(exc),
                )

        self._stream_thread = (
            threading.Thread(
                target=self._data_reader,
                name="PDA36AU-data-reader",
                daemon=True,
            )
        )

        self._stream_thread.start()

        if not self._stream_armed.wait(
            timeout=1.0
        ):
            raise RuntimeError(
                "Datareader kon niet worden armed"
            )

        # The reader is now already waiting on endpoint 0x83.
        self._stream_started.set()

        self.start_adc()

        self._last_adc_status = (
            self.get_adc_status()
        )

        self._status_thread = (
            threading.Thread(
                target=self._status_poller,
                name="PDA36AU-status-poller",
                daemon=True,
            )
        )

        self._status_thread.start()

    def stop_continuous(self) -> None:
        self._stream_stop.set()

        if self._status_thread is not None:
            self._status_thread.join(
                timeout=1.0
            )

        try:
            self.abort()

        finally:
            if self._stream_thread is not None:
                self._stream_thread.join(
                    timeout=2.0
                )

            self._status_thread = None
            self._stream_thread = None

            self._stream_started.clear()
            self._stream_armed.clear()

    def read_raw_block(
        self,
        timeout: float = 5.0,
    ) -> bytes:

        if self._stream_error is not None:
            raise RuntimeError(
                "USB-datareader is gestopt: "
                f"{self._stream_error}"
            )

        try:
            return self._block_queue.get(
                timeout=timeout
            )

        except queue.Empty as exc:
            if self._stream_error is not None:
                raise RuntimeError(
                    "USB-datareader is gestopt: "
                    f"{self._stream_error}"
                ) from self._stream_error

            status = (
                self._last_adc_status.hex(" ")
                if self._last_adc_status
                else "<geen>"
            )

            raise TimeoutError(
                "Geen PDA36AU-datablok ontvangen "
                "binnen timeout. "
                f"Laatste ADC-status: {status}"
            ) from exc

    def read_block(
        self,
        timeout: float = 5.0,
    ) -> PDABlock:
        return self.decode_block(
            self.read_raw_block(
                timeout=timeout
            )
        )

    def read_latest_block(
        self,
        timeout: float = 0.0,
    ) -> PDABlock | None:
        """
        Return only the newest available block.

        This is intended for a GUI. If 20 ADC blocks arrive between
        two screen refreshes, the GUI does not need to draw all 20.
        The endpoint 0x83 reader still runs continuously in its own
        thread.

        Returns None when no block is currently available.
        """
        if self._stream_error is not None:
            raise RuntimeError(
                "USB-datareader is gestopt: "
                f"{self._stream_error}"
            )

        try:
            if timeout > 0:
                raw = self._block_queue.get(
                    timeout=timeout
                )
            else:
                raw = (
                    self._block_queue.get_nowait()
                )

        except queue.Empty:
            return None

        # Drop older display blocks and keep the newest.
        while True:
            try:
                raw = (
                    self._block_queue.get_nowait()
                )
            except queue.Empty:
                break

        return self.decode_block(
            raw
        )

    def single_scan(
        self,
        buffer_length: int = 2500,
        timeout: float = 5.0,
    ) -> PDABlock:
        """
        Software single scan using the proven continuous transfer
        sequence: start, receive one valid block, then abort.
        """
        self.start_continuous(
            buffer_length=buffer_length
        )

        try:
            return self.read_block(
                timeout=timeout
            )

        finally:
            self.stop_continuous()