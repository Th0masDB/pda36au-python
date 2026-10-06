from smbus2 import SMBus, i2c_msg
import time

ADDRESS = 0x60
bus = SMBus(1)

def set_dac(value):
    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(ADDRESS, [byte1, byte2])
    bus.i2c_rdwr(msg)

test_values = [0, 1024, 2048, 3072, 4095]

try:
    for value in test_values:
        set_dac(value)
        voltage = 3.3 * value / 4095

        print(f"DAC = {value:4d} -> ongeveer {voltage:.3f} V")
        input("Meet VOUT en druk Enter voor de volgende waarde...")

finally:
    set_dac(0)
    bus.close()