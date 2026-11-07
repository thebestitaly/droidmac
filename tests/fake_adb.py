#!/usr/bin/env python3
"""Finto `adb` per i test: simula un telefono su una cartella locale.

FAKE_ROOT=/tmp/x  ->  /sdcard e /storage del "telefono" vivono in /tmp/x/sdcard e /tmp/x/storage.
Gli script di shell girano con la shell locale (servono GNU find/stat, come toybox).
FAKE_RATE  byte/s simulati nei trasferimenti (default 50 MB/s).
FAKE_STATE contenuto di $FAKE_ROOT/state: device | unauthorized | none.
"""
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.environ["FAKE_ROOT"].rstrip("/")
RATE = float(os.environ.get("FAKE_RATE", 50e6))
PATH_RE = re.compile(r"(?<![\w/.-])/(sdcard|storage)(?=[/'\"\s|;)]|$)")


def to_host(text: str) -> str:
    return PATH_RE.sub(lambda m: f"{ROOT}/{m.group(1)}", text)


def to_device(data: bytes) -> bytes:
    return data.replace(ROOT.encode(), b"")


def slow_copy(src, dst, keep_time):
    with open(src, "rb") as f, open(dst, "wb") as g:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            g.write(b)
            g.flush()
            time.sleep(len(b) / RATE)
    if keep_time:
        shutil.copystat(src, dst)


def transfer(srcs, dst, keep_time, remote_src):
    rc = 0
    for s in srcs:
        if not os.path.exists(s):
            where = "remote" if remote_src else "local"
            shown = to_device(s.encode()).decode()
            print(f"adb: error: {where} object '{shown}' does not exist", file=sys.stderr)
            rc = 1
            continue
        multi = os.path.isdir(dst) or dst.endswith("/") or len(srcs) > 1
        t = os.path.join(dst, os.path.basename(s.rstrip("/"))) if multi else dst
        if os.path.isdir(s):
            shutil.copytree(s, t, dirs_exist_ok=True, copy_function=lambda a, b: slow_copy(a, b, keep_time))
        else:
            slow_copy(s, t, keep_time)
    print(f"{len(srcs)} files pulled/pushed")
    return rc


def main():
    a = sys.argv[1:]
    if a[:1] == ["-s"]:
        a = a[2:]
    cmd = a[0] if a else ""
    if cmd == "version":
        print("Android Debug Bridge version 1.0.41 (fake)")
    elif cmd in ("start-server", "kill-server"):
        pass
    elif cmd == "devices":
        state = "device"
        if os.path.exists(f"{ROOT}/state"):
            state = open(f"{ROOT}/state").read().strip()
        print("List of devices attached")
        if state != "none":
            print(f"FAKE0001 {state} usb:1-1 product:fake model:Pixel_Fake device:fake transport_id:1")
    elif cmd in ("shell", "exec-out"):
        env = dict(os.environ, PATH=os.path.dirname(os.path.abspath(__file__)) + "/bin:" + os.environ["PATH"])
        p = subprocess.run(["sh", "-c", to_host(a[1])], env=env, stdout=subprocess.PIPE)
        out = p.stdout if cmd == "exec-out" else to_device(p.stdout)
        sys.stdout.buffer.write(out)
        return p.returncode
    elif cmd == "pull":
        args = [x for x in a[1:] if x != "-a"]
        return transfer([to_host(x) for x in args[:-1]], args[-1], "-a" in a, True)
    elif cmd == "push":
        return transfer(a[1:-1], to_host(a[-1]), True, False)
    elif cmd == "connect":
        print(f"failed to connect to '{a[1]}': Connection refused")
        return 1
    else:
        print(f"fake adb: comando non gestito {a}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
