import threading
import unittest
from time import monotonic_ns
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import simulation
from gui.scan_timing import Acquisition, Calibration, benchmark


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


class NoisyPDA(simulation.PDA36AU):
    def __init__(self, noise_samples):
        self.noise_samples = noise_samples

    def read_block(self, **kwargs):
        block = super().read_block(**kwargs)
        pattern = np.repeat([-23.474, 23.474], self.noise_samples) * (3.3 / 4095)
        noise = np.tile(pattern, block.analog.size // pattern.size)
        block.analog[:noise.size] += noise
        return block


class SettlingPDA(simulation.PDA36AU):
    def read_block(self, **kwargs):
        previous = self.previous_value
        block = super().read_block(**kwargs)
        code = self.previous_value
        if code != previous:
            block.analog[:] = code * 3.3 / 4095
            tail = np.sign(code - previous) * (3.3 / 4095) * np.linspace(26, 0, 512)
            block.analog[:tail.size] -= tail
        return block


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

    def test_noisy_feedback_filters_early_hits_and_excludes_outliers(self):
        good_window = [41, 59, 41, 59]
        early_hits = [50, 50, 50, 0]
        acquisition = self.acquisition(
            (early_hits * 4, [100] * 16, None),
            ([0] * 4 + good_window * 3 + [40, 40, 40, 80] + early_hits + good_window,
             [100] * 16 + [2, 4, 6, 100] + [100] * 4 + [8] * 4, None),
        )
        calibration = Calibration(0.0, 1.0, 2.0, 1e-6, 16, window_samples=4, sample_margin=27.039)
        result = acquisition.step(calibration, 50, 0, 50, samples=4)
        self.assertEqual(result.detector, 5)
        self.assertIsNone(result.edge_us)
        self.assertEqual(len(self.blocks[0].analog), 28)

    def test_benchmark_selects_shortest_adequate_window(self):
        cases = ((1, 50, 16, 1), (32, 50, 64, 1), (32, 141, 16, 141), (256, 50, 16, 141))
        for noise_samples, step, window, minimum_step in cases:
            with self.subTest(noise_samples=noise_samples, step=step), patch.object(simulation, "dac_value", 0):
                pda = NoisyPDA(noise_samples)
                pda.start_continuous()
                acquisition = Acquisition(pda, lambda code: setattr(simulation, "dac_value", code),
                                          threading.Event(), self.blocks.append)
                calibration, _ = benchmark(acquisition, 1024, [], step=step)
                self.assertAlmostEqual(calibration.sample_margin / abs(calibration.per_code), 70.422)
                self.assertEqual(calibration.window_samples, window)
                self.assertEqual(calibration.minimum_step, minimum_step)
                if step == 50 and minimum_step <= step:
                    acquisition.step(calibration, 0, 1024 * calibration.per_code, calibration.zero)
                    point = acquisition.step(calibration, 50, calibration.zero,
                                             calibration.zero + 50 * calibration.per_code, samples=2500)
                    self.assertAlmostEqual(point.feedback / calibration.per_code, 50, delta=0.05)

    def test_benchmark_measures_noise_after_the_settling_tail(self):
        with patch.object(simulation, "dac_value", 0):
            pda = SettlingPDA()
            pda.start_continuous()
            acquisition = Acquisition(pda, lambda code: setattr(simulation, "dac_value", code),
                                      threading.Event(), self.blocks.append)
            calibration, _ = benchmark(acquisition, 1024, [])
            self.assertEqual(calibration.minimum_step, 1)
            self.assertEqual(calibration.window_samples, 16)


if __name__ == "__main__":
    unittest.main()
