"""Gedeelde instellingen voor de Cavity controller.

Herstart de applicatie na het wijzigen van deze instellingen.
"""

APP_VERSION = "0.1.0"
I2C_BUS = 1
DAC_ADDRESS = 0x60

# Set only after verifying the loaded piezo/amplifier and the DAC supply.
# (highest safe DAC code, largest safe step in codes, minimum forward and return dwell in ms)
FAST_SCAN_LIMITS = None
FAST_SCAN_ADC_OFFSET_MS = 0.0
