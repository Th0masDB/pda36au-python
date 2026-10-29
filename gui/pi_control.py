"""Raspberry Pi-detectie en gecontroleerd afsluiten van de GUI-host."""

from pathlib import Path
import subprocess


def get_pi_model():
    """Lees het hardwaremodel; een ARM-processor alleen bewijst geen Pi."""
    for filename in ('/sys/firmware/devicetree/base/model', '/proc/device-tree/model'):
        try:
            model = Path(filename).read_text().strip('\x00\n ')
        except (OSError, UnicodeError):
            continue
        if model.startswith('Raspberry Pi'):
            return model
    return None


def shutdown():
    """Vraag systemd om netjes af te sluiten, zonder interactief wachtwoord."""
    _request_power_action('poweroff')


def reboot():
    """Vraag systemd om netjes te herstarten, zonder interactief wachtwoord."""
    _request_power_action('reboot')


def _request_power_action(action):
    if not get_pi_model():
        raise RuntimeError('Systeembeheer is alleen beschikbaar op een Raspberry Pi.')
    result = subprocess.run(
        ['systemctl', '--no-ask-password', action],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or 'Het systeem heeft het verzoek geweigerd.')
