import numpy as np
import time
import sys
import matplotlib.pyplot as plt

SIMULATIE = "--simulatie" in sys.argv or "--sim" in sys.argv

if SIMULATIE:
    from simulation import SMBus, i2c_msg, PDA36AU
else:
    from smbus2 import SMBus, i2c_msg
    from pda36au import PDA36AU


ADDRESS = 0x60
bus = SMBus(1)
STEP= 2
DELAY = 0.01

def meet_gemiddelde(pda, aantal_blokken=5):
    waarden = []

    pda.start_continuous()

    try:
        for _ in range(aantal_blokken):
            block = pda.read_block()
            waarden.extend(block.detector)

    finally:
        pda.stop_continuous()

    return np.mean(waarden)

def set_dac(value):
    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(ADDRESS, [byte1, byte2])
    bus.i2c_rdwr(msg)

def scan_cavity():
    x = []
    for i in range(0,4096, STEP):
        set_dac(i)
        time.sleep(DELAY)
        x.append([i, meet_gemiddelde(pda)])
    set_dac(0)

    return np.array(x)


with PDA36AU() as pda:
    print(pda.get_gain_db())
    x = scan_cavity()
    plt.plot(x[:,0], x[:,1])
    plt.show()
    # print(x[:,0])
    