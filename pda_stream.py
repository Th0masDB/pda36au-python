"""Bounded, raw ADC acquisition for the PDA36AU."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import math
import operator
import threading
import time
from typing import TYPE_CHECKING, Iterator

import numpy as np
import usb.core
import usb.util

if TYPE_CHECKING:
    from pda36au import PDA36AU, PDAScale

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StreamGap:
    """Host-time bounds for a detected event. Unknown loss has no frame count."""

    kind: str
    host_start_ns: int
    host_end_ns: int
    lost_frames: int | None = None
    received_sample_index: int | None = None


class StreamError(RuntimeError):
    def __init__(self, message: str, gap: StreamGap | None = None):
        super().__init__(message)
        self.gap = gap


@dataclass(frozen=True, slots=True)
class StreamingSamples:
    raw: bytes
    channels: np.ndarray
    detector_channel: int
    frame_index: int
    received_sample_index: int
    sample_interval_s: float | None
    host_received_time_ns: int
    gaps: tuple[StreamGap, ...] = ()

    @property
    def detector(self) -> np.ndarray:
        """Read-only detector ADC codes in wire order."""
        return self.channels[self.detector_channel]

    @property
    def external_input(self) -> np.ndarray:
        return self.channels[1 - self.detector_channel]

    @property
    def discontinuity(self) -> bool:
        """Start of session or a detected gap. Inter-frame ADC continuity is unknown."""
        return self.frame_index == 0 or bool(self.gaps)

    def relative_times_s(self) -> np.ndarray | None:
        """Time from the first sample of this frame, at the reported ADC interval."""
        if self.sample_interval_s is None:
            return None
        return np.arange(self.channels.shape[1], dtype=np.float64) * self.sample_interval_s


@dataclass(frozen=True, slots=True)
class StreamDiagnostics:
    buffer_length: int | None
    frames_received: int
    usb_transfers: int
    usb_timeouts: int
    usb_errors: int
    host_dropped_frames: int
    device_missed_blocks: int | None
    device_missed_delta: int
    gap_events: int
    last_gap: StreamGap | None
    pending_gaps: tuple[StreamGap, ...]
    queue_depth: int
    peak_queue_depth: int
    startup_discarded_bytes: int
    shutdown_discarded_bytes: int
    effective_sample_rate_hz: float | None
    last_received_time_ns: int | None
    stop_requested_time_ns: int | None
    adc_status: bytes | None
    error: str | None
    cleanup_error: str | None


class _FrameReader:
    """Reuse an exact-size USB buffer and assemble deliberate packet-sized reads."""

    def __init__(self, frame_bytes: int, transfer_bytes: int | None):
        self.frame_bytes = frame_bytes
        read_size = min(transfer_bytes or frame_bytes, frame_bytes)
        self.buffer = usb.util.create_buffer(read_size)
        self.tail = self.buffer
        if remainder := frame_bytes % read_size:
            self.tail = usb.util.create_buffer(remainder)
        self.pending = bytearray(frame_bytes) if read_size < frame_bytes else None
        self.offset = 0

    @property
    def request(self):
        if self.frame_bytes - self.offset < len(self.buffer):
            return self.tail
        return self.buffer

    def accept(self, count: int) -> bytes | None:
        buffer = self.request
        if count == 0 and self.offset == 0:
            # A zero-length packet can terminate the previous USB message.
            return None
        if count != len(buffer):
            raise StreamError(
                f"Unexpected ADC transfer length {count}/{len(buffer)} bytes "
                f"at offset {self.offset} of {self.frame_bytes}"
            )
        if self.pending is None:
            return bytes(buffer)
        self.pending[self.offset:self.offset + count] = buffer
        self.offset += count
        if self.offset == self.frame_bytes:
            self.offset = 0
            return bytes(self.pending)
        return None


class PDAStream:
    """A single-consumer stream. An overrun preserves queued frames and stops capture."""

    def __init__(
        self,
        pda: PDA36AU,
        buffer_length: int,
        queue_size: int,
        free_run: bool,
        status_interval_s: float | None,
        transfer_bytes: int | None,
    ):
        self._pda = pda
        self._requested_length = operator.index(buffer_length)
        self._queue_size = operator.index(queue_size)
        if not 1 <= self._requested_length <= 65535:
            raise ValueError("buffer_length must be 1..65535")
        if self._queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if pda.command_timeout_ms <= 0:
            raise ValueError("Streaming requires a positive command timeout")
        if pda.data_timeout_ms < 0:
            raise ValueError("data_timeout_ms cannot be negative")
        if status_interval_s is not None and (
            not math.isfinite(status_interval_s) or status_interval_s < 0.0005
        ):
            raise ValueError("status_interval_s must be at least 0.0005 seconds")
        if transfer_bytes is not None:
            transfer_bytes = operator.index(transfer_bytes)
            if transfer_bytes <= 0 or transfer_bytes % pda.DATA_PACKET_BYTES:
                raise ValueError(f"transfer_bytes must be a positive multiple of {pda.DATA_PACKET_BYTES}")
        self._free_run = free_run
        self._status_interval = status_interval_s
        self._transfer_bytes = transfer_bytes
        self._data_timeout = pda.data_timeout_ms or pda.DEFAULT_DATA_TIMEOUT_MS
        self.buffer_length: int | None = None
        self.scale: PDAScale | None = None
        self.sample_interval_s: float | None = None
        self._condition = threading.Condition()
        self._queue: deque[StreamingSamples] = deque()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._armed = threading.Event()
        self._control_thread = threading.Thread(target=self._control, name="PDA36AU-stream-control", daemon=True)
        self._reader_thread: threading.Thread | None = None
        self._done = False
        self._error: StreamError | None = None
        self._cleanup_error: StreamError | None = None
        self._started_ns = time.monotonic_ns()
        self._stop_requested_ns: int | None = None
        self._first_received_ns: int | None = None
        self._last_received_ns: int | None = None
        self._frames = 0
        self._transfers = 0
        self._timeouts = 0
        self._usb_errors = 0
        self._host_dropped = 0
        self._peak_queue = 0
        self._startup_discarded = 0
        self._shutdown_discarded = 0
        self._missed: int | None = None
        self._missed_delta = 0
        self._missed_checked_ns = self._started_ns
        self._adc_status: bytes | None = None
        self._events = 0
        self._last_gap: StreamGap | None = None
        self._pending_gaps: dict[str, StreamGap] = {}

    @property
    def active(self) -> bool:
        return self._control_thread.is_alive() or (
            self._reader_thread is not None and self._reader_thread.is_alive()
        )

    @property
    def diagnostics(self) -> StreamDiagnostics:
        with self._condition:
            rate = None
            if (
                self._first_received_ns is not None
                and self._last_received_ns is not None
                and self._last_received_ns > self._first_received_ns
                and self.buffer_length is not None
            ):
                seconds = (self._last_received_ns - self._first_received_ns) * 1e-9
                rate = (self._frames - 1) * self.buffer_length / seconds
            return StreamDiagnostics(
                buffer_length=self.buffer_length, frames_received=self._frames,
                usb_transfers=self._transfers, usb_timeouts=self._timeouts, usb_errors=self._usb_errors,
                host_dropped_frames=self._host_dropped, device_missed_blocks=self._missed,
                device_missed_delta=self._missed_delta, gap_events=self._events, last_gap=self._last_gap,
                pending_gaps=tuple(self._pending_gaps.values()), queue_depth=len(self._queue),
                peak_queue_depth=self._peak_queue, startup_discarded_bytes=self._startup_discarded,
                shutdown_discarded_bytes=self._shutdown_discarded,
                effective_sample_rate_hz=rate, last_received_time_ns=self._last_received_ns,
                stop_requested_time_ns=self._stop_requested_ns,
                adc_status=self._adc_status, error=str(self._error) if self._error else None,
                cleanup_error=str(self._cleanup_error) if self._cleanup_error else None,
            )

    def _start(self) -> None:
        self._control_thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def __enter__(self) -> PDAStream:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.stop()
        except StreamError as cleanup:
            if exc is None:
                raise
            if hasattr(exc, "add_note"):
                exc.add_note(str(cleanup))
            else:
                logger.error("%s", cleanup)

    def stop(self) -> None:
        """Stop the ADC, join the workers, and restore transient settings."""
        self._request_stop()
        if self._control_thread.ident is not None:
            command_budget = 18 * self._pda.command_timeout_ms / 1000
            self._control_thread.join(2.0 + command_budget + self._data_timeout / 1000)
        if self.active:
            raise StreamError("PDA36AU stream workers did not stop; the USB interface is still in use")
        if self._cleanup_error is not None:
            raise self._cleanup_error

    def read(self, timeout: float | None = None) -> StreamingSamples:
        """Wait for one frame. Drain valid frames before raising an acquisition fault."""
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._queue or self._error or self._done, timeout
            )
            if not ready:
                raise TimeoutError(f"No PDA36AU frame within {timeout:g} s")
            if self._queue:
                return self._queue.popleft()
            if self._error is not None:
                raise self._error
            if self._cleanup_error is not None:
                raise self._cleanup_error
            raise StopIteration

    def __iter__(self) -> Iterator[StreamingSamples]:
        while True:
            try:
                yield self.read()
            except StopIteration:
                return

    def _gap(self, gap: StreamGap) -> None:
        """Update event metadata while holding the condition lock."""
        previous = self._pending_gaps.get(gap.kind)
        self._last_gap = gap
        self._events += 1
        if previous is not None:
            lost = None
            if previous.lost_frames is not None and gap.lost_frames is not None:
                lost = previous.lost_frames + gap.lost_frames
            gap = StreamGap(gap.kind, previous.host_start_ns, gap.host_end_ns, lost)
        self._pending_gaps[gap.kind] = gap

    def _fail(self, kind: str, exc: Exception) -> None:
        with self._condition:
            if self._error is None:
                if isinstance(exc, usb.core.USBError):
                    self._usb_errors += 1
                gap = StreamGap(
                    kind, self._last_received_ns or self._started_ns, time.monotonic_ns()
                )
                self._gap(gap)
                self._error = StreamError(f"{kind}: {exc}", gap)
                self._error.__cause__ = exc
            self._request_stop()
            self._condition.notify_all()

    def _request_stop(self) -> None:
        with self._condition:
            if self._stop_requested_ns is None:
                self._stop_requested_ns = time.monotonic_ns()
                self._stop.set()

    def _poll_missed(self) -> None:
        value = self._pda.get_missed_blocks()
        now = time.monotonic_ns()
        with self._condition:
            if self._missed is not None and self._missed != value:
                delta = value - self._missed
                if delta > 0:
                    self._missed_delta += delta
                    self._gap(StreamGap("device_missed", self._missed_checked_ns, now, delta))
                else:
                    self._gap(StreamGap("device_counter_reset", self._missed_checked_ns, now))
            self._missed = value
            self._missed_checked_ns = now

    def _read_data(self) -> None:
        reader = _FrameReader(self.buffer_length * 4, self._transfer_bytes)
        self._armed.set()
        pda = self._pda
        try:
            while not self._stop.is_set():
                try:
                    count = pda.dev.read(pda.EP_DATA_IN, reader.request, timeout=self._data_timeout)
                except usb.core.USBTimeoutError:
                    if self._stop.is_set():
                        return
                    with self._condition:
                        self._timeouts += 1
                        self._gap(StreamGap("data_timeout", self._last_received_ns or self._started_ns,
                                            time.monotonic_ns()))
                    if reader.offset:
                        raise StreamError(f"Timed out after receiving {reader.offset}/{reader.frame_bytes} ADC bytes")
                    continue
                received = time.monotonic_ns()
                with self._condition:
                    if self._stop.is_set():
                        self._shutdown_discarded += count
                        return
                    self._transfers += 1
                    raw = reader.accept(count)
                    if raw is not None:
                        self._publish(raw, received)
        except Exception as exc:
            if not self._stop.is_set():
                self._fail("data_io" if isinstance(exc, usb.core.USBError) else "invalid_frame", exc)
        finally:
            if reader.offset and self._error is None:
                with self._condition:
                    self._shutdown_discarded += reader.offset

    def _publish(self, raw: bytes, received: int) -> None:
        assert self.buffer_length is not None
        index = self._frames
        offset = index * self.buffer_length
        self._frames += 1
        if self._first_received_ns is None:
            self._first_received_ns = received
        previous = self._last_received_ns or self._started_ns
        self._last_received_ns = received
        if len(self._queue) >= self._queue_size:
            self._host_dropped += 1
            gap = StreamGap("host_overflow", previous, received, 1, offset)
            self._gap(gap)
            self._error = StreamError(
                f"ADC queue limit {self._queue_size} exceeded at received sample index {offset}", gap
            )
            self._request_stop()
            self._condition.notify_all()
            return
        frame = StreamingSamples(
            raw, np.frombuffer(raw, dtype="<u2").reshape(2, self.buffer_length),
            self._pda.detector_channel, index, offset, self.sample_interval_s, received,
            tuple(self._pending_gaps.values()),
        )
        self._pending_gaps.clear()
        self._queue.append(frame)
        self._peak_queue = max(self._peak_queue, len(self._queue))
        self._condition.notify()

    def _drain_endpoint(self, buffer_length: int) -> int:
        pda = self._pda
        packets = (buffer_length * 4 + pda.DATA_PACKET_BYTES - 1) // pda.DATA_PACKET_BYTES
        buffer = usb.util.create_buffer(packets * pda.DATA_PACKET_BYTES)
        discarded = 0
        for _ in range(8):
            try:
                count = pda.dev.read(pda.EP_DATA_IN, buffer, timeout=12)
            except usb.core.USBTimeoutError:
                return discarded
            discarded += count
        raise StreamError("ADC endpoint stayed active after abort; could not establish a frame boundary")

    def _control(self) -> None:
        pda = self._pda
        original_length = None
        restore_slope = None
        prepared = False
        capturing = False
        try:
            original_length = pda.get_adc_buffer_length()
            if not 1 <= original_length <= 65535:
                raise StreamError(f"Invalid initial ADC buffer length {original_length}")
            slope = pda.get_trigger_slope()
            if self._stop.is_set():
                return
            prepared = True
            pda.abort()
            pda.abort()
            self._startup_discarded = self._drain_endpoint(original_length)
            if self._free_run and slope != pda.TRIGGER_SLOPE_NONE:
                restore_slope = slope
                pda.set_trigger_slope(pda.TRIGGER_SLOPE_NONE)
                slope = pda.get_trigger_slope()
                if slope != pda.TRIGGER_SLOPE_NONE:
                    raise StreamError(f"Device rejected no-edge trigger slope (read back {slope})")
            self.scale = pda.get_scale(refresh=True)
            period = self.scale.sample_period_seconds
            self.sample_interval_s = period if period is not None and period > 0 else None
            if self._stop.is_set():
                return
            self._started_ns = time.monotonic_ns()
            pda.set_continuous(1)
            pda.set_adc_buffer_length(self._requested_length)
            actual_length = pda.get_adc_buffer_length()
            if not 1 <= actual_length <= 65535:
                raise StreamError(f"Invalid continuous ADC buffer length {actual_length}")
            self.buffer_length = actual_length
            if self.buffer_length != self._requested_length:
                raise StreamError(
                    f"ADC buffer length {self._requested_length} rejected in continuous mode "
                    f"(device reports {self.buffer_length})"
                )
            self._poll_missed()
            if self._stop.is_set():
                return
            self._reader_thread = threading.Thread(
                target=self._read_data, name="PDA36AU-stream-data", daemon=True
            )
            self._reader_thread.start()
            if not self._armed.wait(1):
                raise StreamError("ADC data reader could not arm")
            pda.start_adc()
            capturing = True
            interval = self._status_interval
            if interval is None:
                interval = 0.001 if slope == pda.TRIGGER_SLOPE_NONE else 0.05
            while not self._stop.is_set():
                status = pda.get_adc_status()
                with self._condition:
                    self._adc_status = status
                self._ready.set()
                if time.monotonic_ns() - self._missed_checked_ns >= 1_000_000_000:
                    self._poll_missed()
                self._stop.wait(interval)
        except Exception as exc:
            self._fail("status_io" if capturing else "stream_start", exc)
        finally:
            self._request_stop()
            self._restore(prepared, capturing, original_length, restore_slope)
            with self._condition:
                self._done = True
                self._condition.notify_all()
            self._ready.set()

    def _restore(
        self, prepared: bool, capturing: bool, original_length: int | None, restore_slope: int | None
    ) -> None:
        pda = self._pda
        errors = []
        aborted = False
        if capturing:
            try:
                self._poll_missed()
            except Exception as exc:
                errors.append(f"final missed-block read: {exc}")
        if prepared:
            try:
                pda.abort()
                pda.abort()
                aborted = True
            except Exception as exc:
                errors.append(f"abort: {exc}")
        if self._reader_thread is not None and self._reader_thread.ident is not None:
            self._reader_thread.join(1.0 + self._data_timeout / 1000)
            if self._reader_thread.is_alive():
                errors.append("data reader did not exit")
        idle = aborted and not (self._reader_thread and self._reader_thread.is_alive())
        if idle and self.buffer_length is not None:
            try:
                discarded = self._drain_endpoint(self.buffer_length)
                with self._condition:
                    self._shutdown_discarded += discarded
            except Exception as exc:
                errors.append(f"drain endpoint after abort: {exc}")
        if idle:
            try:
                if pda.get_adc_buffer_length() != original_length:
                    pda.set_adc_buffer_length(original_length)
                    if pda.get_adc_buffer_length() != original_length:
                        raise StreamError("original ADC buffer length did not read back")
            except Exception as exc:
                errors.append(f"restore buffer length: {exc}")
        if idle and restore_slope is not None:
            try:
                pda.set_trigger_slope(restore_slope)
                if pda.get_trigger_slope() != restore_slope:
                    raise StreamError("original trigger slope did not read back")
            except Exception as exc:
                errors.append(f"restore trigger slope: {exc}")
        if errors:
            with self._condition:
                self._cleanup_error = StreamError("Stream cleanup failed. " + ". ".join(errors))
