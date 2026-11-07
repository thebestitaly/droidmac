"""Tutto quello che parla con il telefono: comandi adb e script di shell Android.

Gli script remoti usano solo comandi presenti in toybox (Android 7+):
find, stat, rm, mkdir, rmdir, mv, df, cat, head, tail, getprop.
"""
from __future__ import annotations

import os
import posixpath
import shlex
import shutil
import subprocess


class AdbError(Exception):
    """Errore mostrato all'utente cosi' com'e'."""


# Percorso di adb; modificabile (test, variabile ADB).
ADB: str | None = None

SHELL_BUDGET = 24_000  # caratteri massimi per una singola riga `adb shell`

# Percorsi che non si possono eliminare o svuotare.
PROTECTED = {
    "/", "/sdcard", "/storage", "/storage/emulated", "/storage/emulated/0",
    "/storage/self", "/storage/self/primary", "/sdcard/Android",
    "/storage/emulated/0/Android", "/data", "/system", "/mnt",
}

# Cartelle standard di Android: dopo uno "Sposta" restano anche se vuote.
STANDARD_DIRS = {
    "DCIM", "Pictures", "Download", "Movies", "Music", "Documents", "Recordings", "Podcasts",
    "Ringtones", "Alarms", "Notifications", "Audiobooks", "Android",
}

REMOTE_PLACES = [
    ("Memoria interna", "/sdcard"),
    ("Fotocamera", "/sdcard/DCIM/Camera"),
    ("Screenshot", "/sdcard/Pictures/Screenshots"),
    ("Screenshot", "/sdcard/DCIM/Screenshots"),
    ("Download", "/sdcard/Download"),
    ("WhatsApp", "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"),
    ("WhatsApp", "/sdcard/WhatsApp/Media"),
    ("Telegram", "/sdcard/Android/media/org.telegram.messenger/Telegram"),
    ("Telegram", "/sdcard/Telegram"),
    ("Immagini", "/sdcard/Pictures"),
    ("Film", "/sdcard/Movies"),
    ("Musica", "/sdcard/Music"),
    ("Documenti", "/sdcard/Documents"),
    ("Registrazioni", "/sdcard/Recordings"),
]


def find_adb():
    candidates = [
        os.environ.get("ADB"),
        shutil.which("adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def dec(b: bytes) -> str:
    # surrogateescape: i nomi file non UTF-8 fanno il giro completo senza perdite
    return b.decode("utf-8", "surrogateescape")


def q(s: str) -> str:
    return shlex.quote(s)


def base_cmd(serial=None):
    if not ADB:
        raise AdbError("adb non trovato. Installa con: brew install android-platform-tools")
    return [ADB] + (["-s", serial] if serial else [])


def run_adb(args, serial=None, timeout=60):
    try:
        p = subprocess.run(base_cmd(serial) + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AdbError("adb non risponde (timeout)")
    return dec(p.stdout), dec(p.stderr), p.returncode


def shell(serial, script, timeout=180):
    """Esegue uno script sul telefono. Gli script terminano con `true`, quindi un
    exit code != 0 indica un errore di adb (telefono scollegato, ecc.)."""
    out, err, rc = run_adb(["shell", script], serial, timeout)
    if rc != 0:
        msg = (err or out).strip().splitlines()
        raise AdbError(msg[-1] if msg else f"adb shell fallito ({rc})")
    return out.replace("\r\n", "\n")


def stream(serial, script):
    """Processo con lo stdout binario di uno script remoto (adb exec-out)."""
    return subprocess.Popen(base_cmd(serial) + ["exec-out", script],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def chunk_args(paths, budget=SHELL_BUDGET):
    part, size = [], 0
    for p in paths:
        n = len(q(p)) + 1
        if part and size + n > budget:
            yield part
            part, size = [], 0
        part.append(p)
        size += n
    if part:
        yield part


def rnorm(path: str) -> str:
    path = (path or "/sdcard").strip()
    if not path.startswith("/"):
        path = "/" + path
    return posixpath.normpath(path) if path != "/" else "/"


def assert_deletable(path: str):
    p = rnorm(path)
    if p in PROTECTED or not (p.startswith("/sdcard/") or p.startswith("/storage/")):
        raise AdbError(f"Per sicurezza non posso eliminare: {p}")


def keep_dir(path: str) -> bool:
    """Cartelle che restano anche se si svuotano dopo uno spostamento."""
    p = rnorm(path)
    if p == "/storage/emulated/0" or p.startswith("/storage/emulated/0/"):
        p = "/sdcard" + p[len("/storage/emulated/0"):]
    if p in PROTECTED or p in {pl for _, pl in REMOTE_PLACES}:
        return True
    parts = p.strip("/").split("/")
    if parts[0] == "sdcard" and len(parts) == 2:
        return parts[1] in STANDARD_DIRS
    if parts[0] == "storage" and len(parts) <= 3:  # radice della scheda SD
        return len(parts) < 3 or parts[2] in STANDARD_DIRS
    return False


# --------------------------------------------------------------------------- #

def list_devices():
    out, err, rc = run_adb(["devices", "-l"], timeout=15)
    if rc != 0:
        raise AdbError(err.strip() or "adb devices fallito")
    devices = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        wifi = ":" in serial or "._adb-tls-connect." in serial
        devices.append({
            "serial": serial,
            "state": state,
            "model": info.get("model", "").replace("_", " ") or serial,
            "transport": "wifi" if wifi else "usb",
        })
    return devices


def device_info(serial):
    places = " ".join(q(p) for _, p in REMOTE_PLACES)
    script = (
        'echo "__P__$(getprop ro.product.manufacturer)|$(getprop ro.product.model)|$(getprop ro.build.version.release)"; '
        "df -k /storage/emulated/0 2>/dev/null | tail -n 1 | sed 's/^/__DF__/'; "
        f'for p in {places}; do [ -d "$p" ] && echo "__D__$p"; done; '
        'for d in /storage/*; do case "$d" in /storage/emulated|/storage/self) ;; '
        '*) [ -d "$d" ] && echo "__SD__$d";; esac; done; true'
    )
    out = shell(serial, script, timeout=30)
    info = {"manufacturer": "", "model": "", "android": "", "storage": None, "places": []}
    existing, sd = set(), []
    for line in out.splitlines():
        if line.startswith("__P__"):
            info["manufacturer"], info["model"], info["android"] = (line[5:].split("|") + ["", "", ""])[:3]
        elif line.startswith("__DF__"):
            nums = [int(t) for t in line[6:].split() if t.isdigit()]
            if len(nums) >= 3:
                info["storage"] = {"total": nums[0] * 1024, "used": nums[1] * 1024, "free": nums[2] * 1024}
        elif line.startswith("__D__"):
            existing.add(line[5:])
        elif line.startswith("__SD__"):
            sd.append(line[6:])
    seen = set()
    for label, path in REMOTE_PLACES:
        if path in existing and label not in seen:
            seen.add(label)
            info["places"].append({"label": label, "path": path})
    for i, path in enumerate(sd):
        info["places"].append({"label": "Scheda SD" if i == 0 else f"Scheda SD {i + 1}", "path": path})
    return info


def remote_ls(serial, path):
    path = rnorm(path)
    qp = q(path)
    script = (
        f"[ -d {qp} ] || {{ echo __NODIR__; exit 0; }}; "
        f"[ -r {qp} ] || {{ echo __NOPERM__; exit 0; }}; "
        f'find -H {qp} -mindepth 1 -maxdepth 1 -exec stat -L -c "%F|%s|%Y|%n" {{}} + 2>/dev/null; true'
    )
    out = shell(serial, script, timeout=120)
    entries = []
    for line in out.splitlines():
        if line == "__NODIR__":
            raise AdbError(f"Cartella non trovata: {path}")
        if line == "__NOPERM__":
            raise AdbError(f"Accesso negato da Android: {path}")
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        ftype, size, mtime, full = parts
        kind = "dir" if ftype == "directory" else ("file" if ftype.startswith("regular") else "other")
        try:
            entries.append({"name": posixpath.basename(full), "type": kind,
                            "size": int(size) if kind == "file" else None, "mtime": int(mtime)})
        except ValueError:
            continue
    return {"path": path, "entries": entries}


def remote_tree(serial, path):
    """Tutte le cartelle e i file sotto `path` (oppure `path` stesso se e' un file)."""
    qp = q(rnorm(path))
    script = f'find -H {qp} \\( -type d -o -type f \\) -exec stat -c "%F|%s|%n" {{}} + 2>/dev/null; true'
    out = shell(serial, script, timeout=900)
    dirs, files = [], []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        ftype, size, full = parts
        if ftype == "directory":
            dirs.append(full.rstrip("/") or "/")
        elif ftype.startswith("regular"):
            files.append((full, int(size)))
    return dirs, files


def remote_sizes(serial, paths):
    sizes = {}
    for part in chunk_args(paths):
        out = shell(serial, 'stat -L -c "%s|%n" -- ' + " ".join(q(p) for p in part) + " 2>/dev/null; true")
        for line in out.splitlines():
            size, _, name = line.partition("|")
            if size.isdigit():
                sizes[name] = int(size)
    return sizes


def remote_rm(serial, paths, recursive=False):
    flag = "-rf" if recursive else "-f"
    errors = []
    for part in chunk_args(paths):
        out = shell(serial, f"rm {flag} -- " + " ".join(q(p) for p in part) + " 2>&1; true", timeout=900)
        errors += [line for line in out.splitlines() if line.strip()]
    return errors


def remote_mkdirs(serial, paths):
    for part in chunk_args(sorted(set(paths))):
        out = shell(serial, "mkdir -p -- " + " ".join(q(p) for p in part) + " 2>&1; true")
        if out.strip():
            raise AdbError(out.strip().splitlines()[-1])


def remote_rmdirs(serial, paths):
    """Rimuove le cartelle vuote (dalla piu' profonda); ignora quelle non vuote."""
    for part in chunk_args(sorted(set(paths), key=len, reverse=True)):
        shell(serial, "rmdir -- " + " ".join(q(p) for p in part) + " 2>/dev/null; true")


def remote_rename(serial, src, name):
    src = rnorm(src)
    name = name.strip()
    if not name or "/" in name or name in (".", ".."):
        raise AdbError("Nome non valido")
    assert_deletable(src)
    dst = posixpath.join(posixpath.dirname(src), name)
    out = shell(serial, f"if [ -e {q(dst)} ]; then echo __EXISTS__; else mv -- {q(src)} {q(dst)} 2>&1; fi; true")
    if "__EXISTS__" in out:
        raise AdbError(f"Esiste già: {name}")
    if out.strip():
        raise AdbError(out.strip())


def connect(host, pair_host="", code=""):
    if code:
        out, err, _ = run_adb(["pair", pair_host.strip(), code.strip()], timeout=30)
        text = (out + err).strip()
        if "Successfully paired" not in text:
            raise AdbError(text or "Abbinamento non riuscito")
    out, err, _ = run_adb(["connect", host.strip()], timeout=20)
    text = (out + err).strip()
    if "connected to" not in text or "failed" in text:
        raise AdbError(text or "Connessione non riuscita")
    return text
