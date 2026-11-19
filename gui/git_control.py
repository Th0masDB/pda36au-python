"""Git-branches van de checkout waarin de webinterface draait."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent.parent
LOCAL_PREFIX = "refs/heads/"
ORIGIN_PREFIX = "refs/remotes/origin/"


def _git(*args, timeout=10):
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout, check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Git is niet beschikbaar op de server.") from exc
    except OSError as exc:
        raise RuntimeError(f"Git kon niet worden gestart ({exc}).") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Git reageert te langzaam. Probeer opnieuw.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or exc.stdout.strip() or "Git-opdracht mislukt.") from exc
    return result.stdout.rstrip("\n")


def _branch_refs():
    if Path(_git("rev-parse", "--show-toplevel")).resolve() != ROOT:
        raise RuntimeError("De applicatiemap is geen eigen Git-werkmap.")

    local = set()
    origin = set()
    refs = _git("for-each-ref", "--format=%(refname)%09%(symref)", LOCAL_PREFIX, ORIGIN_PREFIX)
    for line in refs.split("\n"):
        ref, _, symbolic = line.partition("\t")
        if symbolic:
            continue
        if ref.startswith(LOCAL_PREFIX):
            local.add(ref.removeprefix(LOCAL_PREFIX))
        elif ref.startswith(ORIGIN_PREFIX):
            origin.add(ref.removeprefix(ORIGIN_PREFIX))
    return local, origin


def get_branches():
    local, origin = _branch_refs()
    current = _git("branch", "--show-current") or None
    return current, sorted(local | origin)


def switch_branch(branch):
    local, origin = _branch_refs()
    command = ["switch", "--no-guess", "--no-overwrite-ignore"]
    if branch in local:
        command.extend(["--", branch])
    elif branch in origin:
        command.extend(["--create", branch, "--track", "--", f"{ORIGIN_PREFIX}{branch}"])
    else:
        raise RuntimeError("Deze Git-branch is niet meer beschikbaar. Open de Raspberry Pi-pagina opnieuw.")

    # Laat Git de werkmap en index afwerken voordat de meetlock wordt vrijgegeven.
    _git(*command, timeout=None)
    return branch
