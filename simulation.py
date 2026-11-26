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


class PDA36AU:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def get_gain_db(self):
        return 20

    def start_continuous(self, *, free_run=False):
        pass

    def stop_continuous(self):
        pass

    def read_block(self, timeout=5.0, *, latest=False):
        value = dac_value

        # Simpele cavity-resonantie
        midden = 2000
        breedte = 80

        # Lorentz-profiel
        signaal = 1 / (
            1 + ((value - midden) / breedte) ** 2
        )

        # 2500 meetpunten, zoals de echte PDA ongeveer gebruikt
        detector = signaal + np.random.normal(
            0,
            0.02,
            2500
        )

        analog = np.full_like(detector, 3.3 * value / 4095)
        return FakeBlock(detector, analog)
