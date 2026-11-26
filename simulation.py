from time import monotonic_ns, sleep

import numpy as np

# Huidige gesimuleerde DAC-waarde
dac_value = 0


# --------------------------------------------------
# Gesimuleerde I2C / DAC
# --------------------------------------------------

class FakeMessage:
    def __init__(self, address, data):
        self.address = address
        self.data = data


class i2c_msg:
    @staticmethod
    def write(address, data):
        return FakeMessage(address, data)


class SMBus:
    def __init__(self, bus_number=None):
        pass

    def open(self, bus_number):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def i2c_rdwr(self, msg):
        global dac_value

        byte1 = msg.data[0]
        byte2 = msg.data[1]

        dac_value = ((byte1 & 0x0F) << 8) | byte2


# --------------------------------------------------
# Gesimuleerde PDA36AU
# --------------------------------------------------

class FakeBlock:
    def __init__(self, detector, analog):
        self.detector = detector
        self.analog = analog
        self.received_ns = monotonic_ns()
        self.sample_period_s = 1e-6
        self.y_resolution = 0.0
        self.x = np.arange(len(detector))
        self.x_unit = "µs"


class PDA36AU:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def get_gain_db(self):
        return 20

    def get_adc_status(self):
        return b"\x01"

    def start_continuous(self, *, free_run=False):
        self.previous_value = dac_value

    def stop_continuous(self):
        pass

    def read_block(self, timeout=5.0, *, latest=False, recover_on_timeout=True):
        sleep(0.0025)
        value = dac_value

        # Simpele cavity-resonantie
        codes = np.full(2500, value, dtype=float)
        if value != self.previous_value:
            codes[:500] = self.previous_value
            codes[500:508] = np.linspace(self.previous_value, value, 8)
            self.previous_value = value
        detector = 1 / (1 + ((codes - 2000) / 80) ** 2) + np.random.normal(0, 0.02, codes.size)
        analog = 3.3 * codes / 4095
        return FakeBlock(detector, analog)
