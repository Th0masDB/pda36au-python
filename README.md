# PDA36AU Linux / Python Driver

Unofficiële Linux/Python-driver voor de **Thorlabs PDA36AU** USB-fotodetector.

De driver communiceert rechtstreeks met het apparaat via **PyUSB/libusb** en heeft geen Windows-driver of Thorlabs GUI nodig.

> **Status:** werkend voor device-info, gain/configuratie, single-scan en continue ADC-acquisitie onder Linux.  
> Deze driver is reverse-engineered en is **niet officieel ondersteund door Thorlabs**.

---

## Features

- Detecteert de PDA36AU via USB (`VID 0x1313`, `PID 0x100D`)
- Leest apparaat- en firmware-informatie
- Leest en wijzigt gain
- Leest trigger- en acquisitie-instellingen
- Single-scan
- Continue ADC-acquisitie
- Live plotting met Matplotlib
- Ruwe 16-bit ADC-data
- Gecentreerde ADC-counts
- Door het apparaat gerapporteerde tijd- en spanningsschaal
- Opslaan van metingen als NumPy `.npz`
- Automatisch negeren van het lege priming-block bij het starten van een acquisitie

---

## Projectstructuur

```text
.
├── pda36au.py
├── main.py
├── requirements.txt
└── README.md
```

### `pda36au.py`

Bevat de driver en alle USB-/PDA36AU-functies.

### `main.py`

Voorbeeldprogramma / command-line interface voor:

- apparaatinfo
- single scan
- continuous scan
- live plot
- opslaan van metingen

---

## Vereisten

- Linux
- Python 3
- libusb
- PyUSB
- NumPy
- Matplotlib

Installeer op Debian/Ubuntu bijvoorbeeld eerst libusb:

```bash
sudo apt update
sudo apt install libusb-1.0-0
```

Maak daarna bij voorkeur een virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Installeer de Python-packages:

```bash
pip install pyusb numpy matplotlib
```

Of, als `requirements.txt` aanwezig is:

```bash
pip install -r requirements.txt
```

---

## USB-permissies

Controleer eerst of Linux het apparaat ziet:

```bash
lsusb -d 1313:100d
```

Je zou iets moeten zien zoals:

```text
ID 1313:100d ThorLabs PDA36AU
```

Als het apparaat alleen met `sudo` toegankelijk is, kun je een udev-regel toevoegen.

Maak bijvoorbeeld:

```bash
sudo nano /etc/udev/rules.d/99-thorlabs-pda36au.rules
```

met:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="1313", ATTR{idProduct}=="100d", MODE="0666"
```

Laad daarna de regels opnieuw:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Koppel de PDA36AU vervolgens los en sluit hem opnieuw aan.

> Voor een multi-user systeem is een groepsgebaseerde udev-regel veiliger dan `MODE="0666"`.

---

# Gebruik

## Apparaatinformatie uitlezen

```bash
python main.py info
```

Met USB-debugoutput:

```bash
python main.py info --verbose-usb
```

Voorbeeld van de uitgelezen informatie:

```text
Product:          PDA36AU
Gain index:       2
Gain dB:          20
Buffer length:    2500
X conversion:     1.0
X unit:           us
Y offset:         5.0
Y conversion:     ...
Y unit:           V
```

---

## Single scan

```bash
python main.py single
```

Standaard worden 2500 samples per kanaal gelezen.

Andere bufferlengte:

```bash
python main.py single --buffer 5000
```

Zonder plot:

```bash
python main.py single --no-plot
```

Meting opslaan:

```bash
python main.py single --save measurement.npz
```

---

## Continue acquisitie

Met live plot:

```bash
python main.py continuous
```

Zonder plot:

```bash
python main.py continuous --no-plot
```

Stoppen:

```text
Ctrl+C
```

Ruwe, rond `0x8000` gecentreerde ADC-counts tonen:

```bash
python main.py continuous --raw
```

---

# Gebruik vanuit eigen Python-code

Een minimaal voorbeeld:

```python
from pda36au import PDA36AU

with PDA36AU() as pda:
    block = pda.single_scan(buffer_length=2500)

    print(block.channel_0_scaled)
    print(block.channel_1_scaled)
```

---

## Gain instellen

De PDA36AU heeft gainstappen van 0 tot 70 dB.

Bijvoorbeeld 40 dB:

```python
from pda36au import PDA36AU

with PDA36AU() as pda:
    pda.set_gain_db(40)

    print("Gain:", pda.get_gain_db(), "dB")

    block = pda.single_scan(buffer_length=2500)
```

De bijbehorende gain-index is:

```text
0 ->  0 dB
1 -> 10 dB
2 -> 20 dB
3 -> 30 dB
4 -> 40 dB
5 -> 50 dB
6 -> 60 dB
7 -> 70 dB
```

Je kunt ook rechtstreeks met de index werken:

```python
pda.set_gain(4)
```

---

# Meetdata

Een `PDABlock` bevat verschillende representaties van dezelfde acquisitie.

## Ruwe ADC-data

```python
block.channel_0_raw
block.channel_1_raw
```

Dit zijn NumPy-arrays met unsigned 16-bit ADC-codes.

---

## Gecentreerde ADC-counts

```python
block.channel_0_counts
block.channel_1_counts
```

Hierbij wordt voorlopig:

```python
counts = raw - 32768
```

gebruikt.

Dit is vooral nuttig voor debugging en inspectie van de ruwe ADC-data.

---

## Gekalibreerde device-units

```python
block.channel_0_scaled
block.channel_1_scaled
```

De firmware levert onder andere:

```text
ConversionX
UnitsX
OffsetY
ConversionY
UnitsY
```

Voor het geteste apparaat rapporteert de firmware bijvoorbeeld:

```text
ConversionX = 1.0
UnitsX      = us

OffsetY     = 5.0
UnitsY      = V
```

De driver gebruikt voor de tijdas:

```text
t = sample_index * ConversionX
```

en voor de Y-as:

```text
y = raw * ConversionY - OffsetY
```

Gebruik voor de tijdas:

```python
block.x
```

met de unit:

```python
block.x_unit
```

De Y-unit staat in:

```python
block.y_unit
```

---

# ADC-bufferindeling

De USB-data is **niet interleaved**.

Dus niet:

```text
CH0, CH1, CH0, CH1, ...
```

maar:

```text
CH0 sample 0
CH0 sample 1
...
CH0 sample N-1

CH1 sample 0
CH1 sample 1
...
CH1 sample N-1
```

Bij een bufferlengte van 2500 bestaat één datablok uit:

```text
2500 samples channel 0 * 2 bytes
+
2500 samples channel 1 * 2 bytes
=
10000 bytes
```

Dus:

```text
bytes 0 ... 4999       -> channel 0
bytes 5000 ... 9999    -> channel 1
```

Deze indeling is afgeleid uit de Thorlabs SDK-code en bevestigd tijdens acquisitietests.

---

# USB-protocol

De PDA36AU presenteert één vendor-specific USB-interface met drie bulk-endpoints:

```text
0x02 OUT   command endpoint
0x82 IN    command response endpoint
0x83 IN    ADC data endpoint
```

USB device:

```text
Vendor ID:  0x1313
Product ID: 0x100D
```

De driver gebruikt PyUSB als interface naar libusb.

---

# Continuous-acquisitie

De werkende acquisitievolgorde is:

```text
abort
abort
set continuous mode
set ADC buffer length
arm data read on endpoint 0x83
start ADC
poll ADC status
read ADC blocks continuously
abort when finished
```

Bij het starten kan eerst een volledig leeg ADC-block worden ontvangen.

De driver herkent dit automatisch:

```python
if not any(raw_block):
    # priming block
    ignore()
```

Daarna worden alleen bruikbare blokken aan de applicatie doorgegeven.

---

# Belangrijke API-functies

Een aantal veelgebruikte functies uit `PDA36AU`:

```python
pda.get_gain()
pda.get_gain_db()
pda.set_gain(...)
pda.set_gain_db(...)

pda.get_mode()

pda.get_adc_buffer_length()
pda.set_adc_buffer_length(...)

pda.get_conversion_x()
pda.get_units_x()
pda.get_offset_y()
pda.get_conversion_y()
pda.get_units_y()

pda.get_trigger_channel()
pda.get_trigger_slope()
pda.get_trigger_mode()
pda.get_trigger_threshold()
pda.get_trigger_offset()

pda.get_wavelength()
pda.get_wavelength_coefficient()

pda.get_firmware_version()
pda.get_device_serial()

pda.single_scan(...)
pda.start_continuous(...)
pda.read_block(...)
pda.stop_continuous()
```

---

# Voorbeeld: eenvoudige meting op 40 dB

```python
from pda36au import PDA36AU
import matplotlib.pyplot as plt


with PDA36AU() as pda:

    pda.set_gain_db(40)

    print("Gain:", pda.get_gain_db(), "dB")

    block = pda.single_scan(
        buffer_length=2500
    )

    plt.plot(
        block.x,
        block.channel_0_scaled,
        label="Channel 0",
    )

    plt.xlabel(block.x_unit)
    plt.ylabel(block.y_unit)
    plt.legend()
    plt.show()
```

---

# Meting opslaan

De CLI kan één scan als `.npz` opslaan:

```bash
python main.py single --save measurement.npz
```

Een opgeslagen bestand kan bijvoorbeeld worden geopend met:

```python
import numpy as np

data = np.load("measurement.npz")

print(data.files)

x = data["x"]
ch0 = data["channel_0_scaled"]
ch1 = data["channel_1_scaled"]
```

---

# Channel 0 en Channel 1

De bytevolgorde van beide kanalen is bekend, maar de onderzochte SDK-code labelt de twee ruwe buffers niet expliciet als:

```text
photodetector
```

en:

```text
analog input
```

Daarom gebruikt de driver als canonieke namen:

```text
channel_0
channel_1
```

Er bestaat een configureerbare detector-alias:

```python
PDA36AU(detector_channel=0)
```

of:

```python
PDA36AU(detector_channel=1)
```

Totdat de fysieke kanaaltoewijzing volledig bevestigd is, is het beter om in meetsoftware `channel_0` en `channel_1` te gebruiken.

---

# Bekende beperkingen

- Dit is een reverse-engineered driver.
- Niet officieel ondersteund door Thorlabs.
- Alleen getest op de PDA36AU waarvoor het protocol is onderzocht.
- De fysieke betekenis van `channel_0` versus `channel_1` is nog niet volledig uit de SDK-benaming af te leiden.
- Niet iedere functie uit de originele Windows-software is geïmplementeerd.
- Trigger- en PID-functies zijn grotendeels gereconstrueerd, maar zijn minder uitgebreid getest dan de normale ADC-acquisitie.
- Instellingen die naar het apparaat schrijven moeten voorzichtig gebruikt worden.
- `save_settings()` schrijft instellingen mogelijk persistent naar het apparaat; gebruik dit alleen wanneer dat expliciet gewenst is.

---

# Troubleshooting

## PDA36AU wordt niet gevonden

Controleer:

```bash
lsusb -d 1313:100d
```

Als niets verschijnt:

1. USB-kabel loskoppelen.
2. Enkele seconden wachten.
3. PDA36AU opnieuw aansluiten.
4. `lsusb` opnieuw uitvoeren.

---

## `Permission denied` / PyUSB ziet het apparaat niet

Controleer de udev-regel en probeer tijdelijk:

```bash
sudo python main.py info
```

Als dit wel werkt, is het vrijwel zeker een Linux USB-permissieprobleem.

---

## `No backend available`

Installeer libusb:

```bash
sudo apt install libusb-1.0-0
```

Controleer daarna opnieuw.

---

## Geen ADC-block ontvangen

Controleer eerst:

```bash
python main.py info
```

en daarna:

```bash
python main.py continuous --verbose-usb
```

De continuous driver heeft zowel een actieve read op endpoint `0x83` als periodieke ADC-statusqueries nodig.

---

# Development notes

Het protocol is gereconstrueerd door combinatie van:

- USB descriptors van de PDA36AU
- live USB-tests onder Linux
- analyse van `USB_PDA_SDK.dll`
- analyse van `usb_pda_device.dll`
- analyse van `usb_driver.dll`

Belangrijke bevindingen waren onder andere:

- command endpoint `0x02`
- response endpoint `0x82`
- ADC endpoint `0x83`
- 32-bit antwoord voor ADC-bufferlengte
- kanaal-major ADC-layout
- een leeg priming-block bij het starten
- continue statuspolling tijdens streaming
- firmwaregeleverde X/Y-schaalfactoren

---

# Disclaimer

Deze software is onafhankelijk ontwikkeld en is niet verbonden aan of goedgekeurd door Thorlabs.

Gebruik op eigen risico, vooral voor functies die instellingen wijzigen of persistent opslaan.

Thorlabs en PDA36AU zijn handelsmerken van hun respectieve eigenaar.

## Bij deze software is gebruikgemaakt van AI.
