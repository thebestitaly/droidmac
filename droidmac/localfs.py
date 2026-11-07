"""File system del Mac."""
from __future__ import annotations

import os
import shutil

from .adb import AdbError

# File di servizio macOS che non vanno mai copiati sul telefono.
MAC_JUNK = {".DS_Store", ".localized", "Icon\r"}


def lnorm(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path or "~"))


def local_ls(path):
    path = lnorm(path)
    if not os.path.isdir(path):
        raise AdbError(f"Cartella non trovata: {path}")
    entries = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat()  # segue i link
                    is_dir = e.is_dir()
                except OSError:
                    continue
                entries.append({"name": e.name, "type": "dir" if is_dir else "file",
                                "size": None if is_dir else st.st_size, "mtime": int(st.st_mtime)})
    except PermissionError:
        raise AdbError(f"macOS nega l'accesso a {path}. Consenti l'accesso quando macOS lo chiede, oppure "
                       "in Impostazioni di Sistema › Privacy e sicurezza › File e cartelle.")
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        free = None
    return {"path": path, "entries": entries, "free": free}


def local_places():
    home = os.path.expanduser("~")
    places = [{"label": "Home", "path": home}]
    for label, sub in [("Scrivania", "Desktop"), ("Download", "Downloads"), ("Immagini", "Pictures"),
                       ("Filmati", "Movies"), ("Documenti", "Documents"), ("Musica", "Music")]:
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            places.append({"label": label, "path": p})
    if os.path.isdir("/Volumes"):  # dischi esterni
        for name in sorted(os.listdir("/Volumes")):
            p = os.path.join("/Volumes", name)
            if os.path.isdir(p) and not os.path.islink(p) and not name.startswith("."):
                places.append({"label": name, "path": p, "volume": True})
    return places


def local_tree(path):
    path = lnorm(path)
    if os.path.isfile(path):
        return [], [(path, os.path.getsize(path))]
    dirs, files = [path], []
    for root, dnames, fnames in os.walk(path):
        dnames[:] = [d for d in dnames if not os.path.islink(os.path.join(root, d))]
        dirs += [os.path.join(root, d) for d in dnames]
        for f in fnames:
            if f in MAC_JUNK or f.startswith("._"):
                continue
            p = os.path.join(root, f)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            files.append((p, os.path.getsize(p)))
    return dirs, files


def local_mkdir(path, name):
    name = name.strip()
    if not name or "/" in name:
        raise AdbError("Nome non valido")
    p = os.path.join(lnorm(path), name)
    if os.path.exists(p):
        raise AdbError(f"Esiste già: {name}")
    os.makedirs(p)


def fsize(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return -1


def unique_name(path, taken, join, split):
    """'foto.jpg' -> 'foto (1).jpg' finche' il nome non e' libero."""
    folder, name = split(path)
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        cand = join(folder, f"{stem} ({i}){ext}")
        if not taken(cand):
            return cand
        i += 1
