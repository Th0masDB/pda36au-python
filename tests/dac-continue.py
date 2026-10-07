from smbus2 import SMBus, i2c_msg
import time

ADDRESS = 0x60

STEP = 16
DELAY = 0.01

bus = SMBus(1)


def set_dac(value):
    value = max(0, min(4095, value))

    byte1 = (value >> 8) & 0x0F
    byte2 = value & 0xFF

    msg = i2c_msg.write(ADDRESS, [byte1, byte2])
    bus.i2c_rdwr(msg)


try:
    while True:

        # 0 V -> 5 V
        for value in range(0, 4096, STEP):
            set_dac(value)
            time.sleep(DELAY)

        set_dac(4095)

        # 5 V -> 0 V
        for value in range(4095, -1, -STEP):
            set_dac(value)
            time.sleep(DELAY)

        set_dac(0)

except KeyboardInterrupt:
    set_dac(0)
    print("\nDAC teruggezet naar 0 V.")

finally:
    bus.close()