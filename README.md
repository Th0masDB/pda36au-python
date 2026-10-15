# Cavity Scan Controller

Python software voor het uitvoeren en uitlezen van scans van een optische cavity.

Het doel van dit project is om uiteindelijk een complete cavity scan controller te ontwikkelen waarmee een cavity automatisch gescand kan worden en het bijbehorende detectorsignaal kan worden gemeten en geanalyseerd.

> [!WARNING]
> **Status:** work in progress  

## Doel van het project

Een cavity scan bestaat uiteindelijk uit meerdere onderdelen, waaronder:

- een scansignaal genereren voor bijvoorbeeld een piezo;
- het detectorsignaal tijdens de scan uitlezen;
- scan en detector synchroniseren;
- cavity-resonanties weergeven;
- meetdata analyseren;
- meetdata opslaan.

De Python-driver voor de **Thorlabs PDA36AU** vormt momenteel een van de eerste onderdelen van dit project.

## Huidige functionaliteit

Op dit moment ondersteunt het project communicatie met de PDA36AU via USB.

Beschikbaar:

- PDA36AU automatisch detecteren;
- apparaatinformatie uitlezen;
- detector gain instellen;
- enkele acquisitie uitvoeren;
- continue acquisitie uitvoeren;
- metingen live weergeven;
- ruwe ADC-data uitlezen;
- meetdata opslaan.

De communicatie gebeurt rechtstreeks via **PyUSB/libusb** en vereist geen officiële Thorlabs-software.

## Installatie

Clone de repository:

```bash
git clone -b scan-test https://github.com/Th0masDB/pda36au-python.git
cd pda36au-python
```

Installeer op Debian, Ubuntu of Raspberry Pi OS:

```bash
sudo apt update
sudo apt install libusb-1.0-0 python3-pyqt5 python3-venv
```

Maak een Python virtual environment:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
```

Installeer vervolgens de benodigde Python-packages:

```bash
pip install pyusb numpy pyqtgraph
```

## PDA36AU testen

Sluit de PDA36AU via USB aan en controleer of communicatie mogelijk is:

```bash
python pda-tool.py info
```

### Enkele acquisitie

```bash
python pda-tool.py single
```

Met bijvoorbeeld 40 dB gain:

```bash
python pda-tool.py single --gain 40
```

Zonder live grafiek:

```bash
python pda-tool.py single --no-plot
```

### Continue acquisitie

```bash
python pda-tool.py continuous
```

Stop de acquisitie met:

```text
Ctrl+C
```

## Gebruik vanuit Python

De PDA36AU kan ook rechtstreeks vanuit Python worden aangestuurd:

```python
from pda36au import PDA36AU

with PDA36AU() as detector:
    detector.set_gain_db(40)

    scan = detector.single_scan(
        buffer_length=2500
    )

    print(scan.channel_0_scaled)
    print(scan.channel_1_scaled)
```

## Roadmap

Het project bevindt zich nog in een vroege ontwikkelfase.

De volgende stappen zijn onder andere:

- [x] USB-communicatie met PDA36AU
- [x] PDA36AU data-acquisitie
- [x] Live detectorweergave
- [ ] Scan hardware aansturen
- [ ] Scan en detector synchroniseren (checken hoe betrouwbaar dit kan)
- [ ] Live cavity scan weergeven (misschien)
- [ ] Resonanties automatisch detecteren
- [ ] Meetdata analyseren
- [ ] Meetdata en instellingen opslaan

De exacte implementatie en softwareopbouw kunnen tijdens de ontwikkeling nog veranderen.

## Disclaimer

Dit project is experimenteel en nog in ontwikkeling.

De USB-communicatie met de PDA36AU is reverse-engineered. Dit project is niet officieel verbonden aan of ondersteund door Thorlabs.

Thorlabs en PDA36AU zijn handelsmerken van hun respectieve eigenaar.