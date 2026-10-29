import numpy as np
import time
import sys
import matplotlib.pyplot as plt
from config import I2C_BUS, DAC_ADDRESS

SIMULATIE = "--simulatie" in sys.argv or "--sim" in sys.argv
PDA_Connected = "--PDACon" in sys.argv

if SIMULATIE and PDA_Connected:
    from simulation import SMBus, i2c_msg
    from pda36au import PDA36AU
if SIMULATIE:
    from simulation import SMBus, i2c_msg, PDA36AU
else:
    from smbus2 import SMBus, i2c_msg
    from pda36au import PDA36AU


bus = SMBus(I2C_BUS)
STEP= 50
DELAY = 0.001
AANTAL_BLOKKEN = 1

def meet_gemiddelde(pda, aantal_blokken=5):
    waarden = []

    for _ in range(aantal_blokken):
        block = pda.read_block()
        waarden.extend(block.detector)


    return np.mean(waarden)

def set_dac(value):
    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(DAC_ADDRESS, [byte1, byte2])
    bus.i2c_rdwr(msg)

def scan_cavity():
    pda.start_continuous()
    x = []
    try:
        for i in range(0, 4096, STEP):

            set_dac(i)

            if DELAY > 0:
                time.sleep(DELAY)

            gemiddelde = meet_gemiddelde(
                pda,
                aantal_blokken=AANTAL_BLOKKEN
            )

            x.append([i, gemiddelde])

    finally:
        pda.stop_continuous()
        set_dac(0)

    return np.array(x)


with PDA36AU() as pda:
    print(pda.get_gain_db())
    x = scan_cavity()
    plt.plot(x[:,0], x[:,1])
    plt.show()
    # print(x[:,0])
