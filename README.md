# Cavity Scan Controller

Python-software voor het uitvoeren en uitlezen van scans van een optische cavity, met een NiceGUI-webinterface.

Het doel van dit project is om uiteindelijk een complete cavity scan controller te ontwikkelen waarmee een cavity automatisch gescand kan worden en het bijbehorende detectorsignaal kan worden gemeten en geanalyseerd.

> [!WARNING]
> **Status:** work in progress  

## Doel van het project

Een cavity scan bestaat uiteindelijk uit meerdere onderdelen, waaronder:

- een scansignaal genereren voor een piezo;
- het detectorsignaal tijdens de scan uitlezen;
- cavity-resonanties weergeven;
- meetdata analyseren;
- meetdata opslaan.

Het project combineert een Python-driver voor de **Thorlabs PDA36AU** met DAC-aansturing via I²C en een webinterface voor scans en live detectorweergave.

## Huidige functionaliteit

In de webinterface:

- cavity scannen met instelbare DAC-stapgrootte, wachttijd en PDA-blokken per meetpunt;
- scans handmatig stoppen;
- het gemiddelde detectorsignaal per DAC-waarde weergeven;
- de scangrafiek optioneel tijdens de scan bijwerken;
- het nieuwste PDA-meetblok live bekijken, ook zonder scan;
- volledig simuleren of een echte PDA combineren met een gesimuleerde DAC.
- op een Raspberry Pi: een beheerpagina om het systeem netjes af te sluiten.

Daarnaast biedt de PDA-driver met `pda-tool.py`:

- PDA36AU automatisch detecteren;
- apparaatinformatie uitlezen;
- detector gain instellen;
- enkele acquisitie uitvoeren;
- continue acquisitie uitvoeren;
- metingen live weergeven;
- ruwe ADC-data uitlezen;
- meetdata opslaan.

De communicatie gebeurt rechtstreeks via **PyUSB/libusb** (en vereist dus geen officiële Thorlabs-software).

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
pip install pyusb numpy nicegui smbus2
```

Voor de losse grafieken van `pda-tool.py` en `scan.py`:

```bash
pip install pyqtgraph matplotlib
```

Voor echte scans gebruikt de controller I²C-bus **1** en DAC-adres **0x60**.
Deze gedeelde instellingen staan in `config.py`:

```python
APP_VERSION = "0.1.0"
I2C_BUS = 1
DAC_ADDRESS = 0x60
```

De webinterface en `scan.py` gebruiken dezelfde bus en hetzelfde DAC-adres.
Herstart de applicatie nadat je de instellingen hebt gewijzigd.

Zorg dat I²C beschikbaar is en dat je gebruiker toegang heeft tot de I²C-bus en de PDA via USB.

## Webinterface

Voer de onderstaande opdrachten uit vanuit de projectmap, met de virtual environment actief.

### Starten

Zonder aangesloten hardware:

```bash
python gui/main.py --sim
```

Met een echte PDA en DAC:

```bash
python gui/main.py
```

Met een echte PDA en een gesimuleerde DAC:

```bash
python gui/main.py --sim --PDACon
```

`--simulatie` is een alternatief voor `--sim`. De optie `--PDACon` werkt in combinatie met simulatie.
Open na het starten [http://localhost:8080](http://localhost:8080).

### Cavity scannen

Ga naar **Scan**, stel de meetparameters in en klik op **Start scan**:

| Instelling | Betekenis | Standaard |
| --- | --- | --- |
| DAC stapgrootte | Stap tussen opeenvolgende DAC-waarden binnen 0–4095 | 50 |
| Delay per stap (ms) | Wachttijd na het instellen van de DAC | 1 ms |
| PDA blokken per punt | Aantal meetblokken waarvan het detectorsignaal wordt gemiddeld | 1 |
| Live grafiek | Scangrafiek tijdens de meting bijwerken, maximaal twee keer per seconde | Uit |

Met **Stop** vraag je de scan te stoppen. Na afloop verschijnt de volledige
scangrafiek, ook als **Live grafiek** uit stond. De controller probeert de DAC
na afloop, bij stoppen en bij een fout terug op nul te zetten.

### Live PDA-signaal

Open **Grafieken** en klik op **Start live meting**. De grafiek toont het
nieuwste PDA-meetblok en ververst maximaal vijf keer per seconde. De assen gebruiken
de eenheden van de PDA.
Dit is het signaal binnen één meetblok, geen doorlopende geschiedenis.

De meting blijft actief bij navigeren. Klik op **Stop live meting** voordat
je een scan start. Tijdens scans toont deze pagina automatisch de PDA-blokken
die de scan uitleest. De losse live meting wijzigt de DAC niet.

### Automatisch starten als systemd-service

Dit voorbeeld gebruikt gebruiker `cavity` en de projectmap
`/home/cavity/pda36au-python`. Zorg dat de gebruiker, de projectmap en de virtual
environment bestaan en dat `cavity` toegang heeft tot de USB- en I²C-hardware.
Pas de gebruikersnaam en paden aan als jouw installatie anders is.

Open op de Pi het servicebestand:

```bash
sudo nano /etc/systemd/system/pda36au.service
```

Plaats hierin:

```ini
[Unit]
Description=PDA36AU Python application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=cavity
WorkingDirectory=/home/cavity/pda36au-python
ExecStart=/home/cavity/pda36au-python/.venv/bin/python /home/cavity/pda36au-python/gui/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activeer de service en laat deze automatisch starten bij het opstarten:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pda36au.service
```

Als de service al draait, herstart je deze na wijzigingen aan de serviceconfiguratie
of de Python-code met `sudo systemctl restart pda36au.service`.
Controleer de status en logs met:

```bash
systemctl status pda36au.service
journalctl -u pda36au.service -n 100 --no-pager
```

`Restart=always` herstart de applicatie bij uitvallen, maar verhindert geen
normale systeemshutdown.

### Raspberry Pi-beheer

Wanneer de webinterface op een Raspberry Pi draait, verschijnt **Raspberry Pi**
in het menu. De detectie gebruikt het hardwaremodel van de server, niet het
apparaat waarop je de browser opent. Op andere computers wordt ook een direct
bezoek aan `/raspberry-pi` teruggestuurd naar Home.

Stop eerst alle metingen en kies **Raspberry Pi afsluiten**. Na bevestiging
vraagt de applicatie via `systemctl --no-ask-password poweroff` een normale
systeemshutdown aan. Wacht tot de Pi volledig is afgesloten voordat je de
voeding loskoppelt. Deze knop sluit ook bij gebruik van `--sim` de echte Pi af.

Met **Raspberry Pi herstarten** kun je na bevestiging de Pi opnieuw opstarten.
Ook hiervoor moeten de scan en live meting gestopt zijn. De applicatie gebruikt
`systemctl --no-ask-password reboot`. De webinterface is tijdelijk onbereikbaar;
met de onderstaande systemd-service start deze automatisch weer. Open de pagina
opnieuw zodra de Pi is opgestart. Heb je de Polkit-regel eerder alleen voor
afsluiten ingesteld, voeg dan ook de twee `reboot`-acties uit het voorbeeld toe.

De gebruiker die de GUI uitvoert moet via het besturingssysteem toestemming
hebben om af te sluiten. Als die toestemming ontbreekt, toont de pagina een
fout; de applicatie vraagt geen sudo-wachtwoord. De beheerpagina is beschikbaar
voor iedereen met toegang tot de webinterface; gebruik deze op een vertrouwd netwerk.


#### Afsluit- en herstartrechten voor de servicegebruiker

De GUI draait met deze service als `cavity`. Geef deze gebruiker via Polkit
toestemming om zonder wachtwoord af te sluiten en te herstarten. Open op de Pi:

```bash
sudo nano /etc/polkit-1/rules.d/50-cavity-poweroff.rules
```

Plaats hierin:

```javascript
polkit.addRule(function(action, subject) {
    if (subject.user === "cavity" &&
        (action.id === "org.freedesktop.login1.power-off" ||
         action.id === "org.freedesktop.login1.power-off-multiple-sessions" ||
         action.id === "org.freedesktop.login1.reboot" ||
         action.id === "org.freedesktop.login1.reboot-multiple-sessions")) {
        return polkit.Result.YES;
    }
});
```

Gebruik dezelfde gebruikersnaam als bij `User=` in de service. Bewaar in nano
met **Ctrl+O**, druk **Enter** en sluit met **Ctrl+X**. Polkit leest de regel
automatisch in; hiervoor hoef je de Pi of de service niet te herstarten.

Deze regel geeft de gebruiker toestemming om af te sluiten en te herstarten, ook wanneer andere
gebruikers ingelogd zijn. De applicatie hoeft hiervoor niet als root te draaien
en de Python-code hoeft niet te worden aangepast. Iedereen met toegang tot de
webinterface kan vervolgens via de bevestigingsknoppen de Pi afsluiten of herstarten.

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

## Projectstructuur

| Bestand | Functie |
| --- | --- |
| `config.py` | Gedeelde appversie, I²C-bus en DAC-adres |
| `gui/main.py` | NiceGUI-pagina's voor Home, Scan, Grafieken en Raspberry Pi-beheer |
| `gui/pi_control.py` | Raspberry Pi-detectie en systeemshutdown |
| `gui/scan_controller.py` | Scanlogica, live acquisitie en gedeelde meetstatus |
| `pda36au.py` | USB-driver voor de PDA36AU |
| `simulation.py` | Gesimuleerde DAC en PDA met een cavity-resonantie en ruis |
| `pda-tool.py` | Commandlinetool voor apparaatinformatie en acquisitie |
| `scan.py` | Los scanscript met Matplotlib-weergave |

## Roadmap

Het project bevindt zich nog in een vroege ontwikkelfase.

De volgende stappen zijn onder andere:

- [x] USB-communicatie met PDA36AU
- [x] PDA36AU data-acquisitie
- [x] Live detectorweergave
- [x] DAC-aansturing via I²C implementeren
- [x] Live cavity scan weergeven in de webinterface
- [ ] Resonanties automatisch detecteren
- [ ] Meetdata analyseren
- [ ] Meetdata en instellingen vanuit de webinterface opslaan

De exacte implementatie en softwareopbouw kunnen tijdens de ontwikkeling nog veranderen.

## Disclaimer

Dit project is experimenteel en nog in ontwikkeling.

De USB-communicatie met de PDA36AU is reverse-engineered. Dit project is niet officieel verbonden aan of ondersteund door Thorlabs.

Thorlabs en PDA36AU zijn handelsmerken van hun respectieve eigenaar.
