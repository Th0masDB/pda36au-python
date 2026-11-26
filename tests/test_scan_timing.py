import threading
import unittest
from time import monotonic_ns
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from gui.scan_timing import Acquisition, Calibration


class FakePDA:
    def __init__(self, *frames):
        self.frames = iter(frames)

    def read_block(self, **kwargs):
        frame = next(self.frames, None)
        if frame is None:
            raise TimeoutError
        analog, detector, receipt = frame
        return SimpleNamespace(analog=np.array(analog), detector=np.array(detector),
                               received_ns=receipt or monotonic_ns())


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.codes = []
        self.blocks = []
        self.calibration = Calibration(0.0, 1.0, 0.2, 1e-6, 10)

    def acquisition(self, *frames):
        return Acquisition(FakePDA(*frames), self.codes.append, threading.Event(), self.blocks.append)

    def test_averages_only_after_confirmed_new_level(self):
        acquisition = self.acquisition(
            ([10] * 10, [100] * 10, 1),
            ([0] * 10, [100] * 10, None),
            ([0, 2, 5, 9, 10, 10, 10, 10, 0, 10], [100] * 6 + [2, 4, 100, 6], None),
        )
        result = acquisition.step(self.calibration, 10, 0, 10, samples=3)
        self.assertEqual(self.codes, [10])
        self.assertEqual(len(self.blocks), 1)
        self.assertEqual(result.detector, 4)
        self.assertEqual(result.feedback, 10)
        self.assertAlmostEqual(result.edge_us, 2)

    def test_post_trigger_delay_continues_in_next_block(self):
        acquisition = self.acquisition(
            ([0, 10, 10, 10, 10, 10], [100] * 6, None),
            ([10] * 3, [100, 7, 11], None),
        )
        result = acquisition.step(self.calibration, 10, 0, 10, samples=2, delay=4e-6)
        self.assertEqual(result.detector, 9)

    def test_missing_feedback_times_out(self):
        acquisition = self.acquisition(([0] * 10, [1] * 10, None))
        with patch("gui.scan_timing.TIMEOUT_NS", 1_000_000):
            with self.assertRaisesRegex(TimeoutError, "Kanaal 2.*DAC-waarde 10"):
                acquisition.step(self.calibration, 10, 0, 10)
        self.assertEqual(self.blocks, [])


if __name__ == "__main__":
    unittest.main()
