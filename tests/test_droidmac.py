"""Test end-to-end: server reale + finto adb su un telefono simulato.

    python3 -m unittest discover -s tests -v

Richiede GNU find/stat (Linux, o `brew install coreutils findutils` su macOS),
perche' simulano i comandi toybox del telefono.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from droidmac import adb, server  # noqa: E402

FAKE_ADB = os.path.join(ROOT_DIR, "tests", "fake_adb.py")
SERIAL = "FAKE0001"


def gnu_tools():
    try:
        out = subprocess.run(["stat", "--version"], capture_output=True, text=True).stdout
        return "GNU" in out or "coreutils" in out
    except OSError:
        return False


def write(path, size=0, data=None, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if data is not None else os.urandom(size))
    if mtime:
        os.utime(path, (mtime, mtime))


@unittest.skipUnless(gnu_tools(), "servono GNU find/stat per simulare il telefono")
class DroidMacTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="droidmac-test-")
        cls.root = os.path.join(cls.tmp, "phone")
        cls.home = os.path.join(cls.tmp, "home")
        internal = os.path.join(cls.root, "storage", "emulated", "0")
        os.makedirs(internal)
        os.makedirs(os.path.join(cls.root, "storage", "ABCD-1234", "DCIM"))
        os.symlink(internal, os.path.join(cls.root, "sdcard"))
        cls.internal = internal
        for sub in ("Downloads", "Pictures", "Desktop"):
            os.makedirs(os.path.join(cls.home, sub))

        os.environ.update(FAKE_ROOT=cls.root, FAKE_RATE="200e6", HOME=cls.home)
        adb.ADB = FAKE_ADB
        cls.app, cls.srv = server.create_server(port=0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # telefono e Mac puliti per ogni test
        for d in (self.internal, os.path.join(self.home, "Pictures")):
            for name in os.listdir(d):
                p = os.path.join(d, name)
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
        for sub in ("DCIM/Camera", "Download", "Documents"):
            os.makedirs(os.path.join(self.internal, sub))
        self.mac = os.path.join(self.home, "Pictures")

    # ------------------------------------------------------------------ helpers

    def phone(self, rel):
        return os.path.join(self.internal, rel)

    def call(self, path, body=None, headers=None, token=True):
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["X-Token"] = self.app.token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def run_job(self, path, body, timeout=30):
        code, job = self.call(path, body)
        self.assertEqual(code, 200, job)
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, data = self.call("/api/jobs")
            j = next(x for x in data["jobs"] if x["id"] == job["id"])
            if j["status"] not in ("queued", "scanning", "running"):
                return j
            time.sleep(0.05)
        self.fail("job non terminato")

    def pull(self, sources, **kw):
        return self.run_job("/api/transfer/pull", {"serial": SERIAL, "sources": sources, "dest": self.mac, **kw})

    # ------------------------------------------------------------------ test

    def test_security_guards(self):
        self.assertEqual(self.call("/api/devices", token=False)[0], 403)
        self.assertEqual(self.call("/api/devices", headers={"Host": "evil.example:80"})[0], 403)
        with urllib.request.urlopen(self.base + "/ping") as r:
            self.assertEqual(json.load(r)["app"], "DroidMac")

    def test_devices_and_info(self):
        _, d = self.call("/api/devices")
        self.assertEqual(d["devices"][0]["serial"], SERIAL)
        self.assertEqual(d["devices"][0]["model"], "Pixel Fake")
        _, info = self.call(f"/api/device?serial={SERIAL}")
        self.assertEqual(info["android"], "15")
        labels = [p["label"] for p in info["places"]]
        self.assertIn("Fotocamera", labels)
        self.assertIn("Scheda SD", labels)
        self.assertGreater(info["storage"]["total"], 0)

    def test_listing_odd_names(self):
        name = "fattura (gen) 'test' & co $HOME.pdf"
        write(self.phone(f"Download/{name}"), 1234)
        write(self.phone("Download/.nomedia"), 0)
        _, ls = self.call(f"/api/remote/ls?serial={SERIAL}&path=/sdcard/Download")
        by = {e["name"]: e for e in ls["entries"]}
        self.assertEqual(by[name]["size"], 1234)
        self.assertEqual(by[".nomedia"]["size"], 0)
        code, err = self.call(f"/api/remote/ls?serial={SERIAL}&path=/sdcard/nope")
        self.assertEqual(code, 400)
        self.assertIn("non trovata", err["error"])

    def test_copy_skip_and_conflict(self):
        for i in range(25):
            write(self.phone(f"DCIM/Camera/IMG_{i:03}.jpg"), 50_000 + i, mtime=1_600_000_000)
        j = self.pull(["/sdcard/DCIM/Camera"], skip=True)
        self.assertEqual((j["status"], j["done_files"]), ("done", 25))
        local = os.path.join(self.mac, "Camera", "IMG_000.jpg")
        self.assertEqual(os.path.getmtime(local), 1_600_000_000)  # data originale conservata

        j = self.pull(["/sdcard/DCIM/Camera"], skip=True)
        self.assertEqual((j["total_files"], j["skipped"]), (0, 25))

        write(local, data=b"diverso")  # stesso nome, contenuto diverso
        j = self.pull(["/sdcard/DCIM/Camera/IMG_000.jpg"], skip=True, dest=os.path.dirname(local))
        self.assertEqual(j["done_files"], 1)
        self.assertEqual(open(local, "rb").read(), b"diverso")  # mai sovrascritto
        self.assertTrue(os.path.exists(os.path.join(self.mac, "Camera", "IMG_000 (1).jpg")))

    def test_move_keeps_system_folders(self):
        write(self.phone("DCIM/Camera/a.jpg"), 1000)
        write(self.phone("Vacanze/Spiaggia/b.jpg"), 2000)
        j = self.pull(["/sdcard/DCIM/Camera", "/sdcard/Vacanze"], move=True)
        self.assertEqual((j["status"], j["done_files"]), ("done", 2))
        self.assertTrue(os.path.isdir(self.phone("DCIM/Camera")))       # cartella di sistema: resta
        self.assertEqual(os.listdir(self.phone("DCIM/Camera")), [])
        self.assertFalse(os.path.exists(self.phone("Vacanze")))          # cartella utente: rimossa
        self.assertEqual(os.path.getsize(os.path.join(self.mac, "Vacanze", "Spiaggia", "b.jpg")), 2000)

    def test_protected_paths(self):
        code, err = self.call("/api/transfer/pull", {"serial": SERIAL, "sources": ["/sdcard"], "dest": self.mac, "move": True})
        self.assertEqual(code, 400)
        code, _ = self.call("/api/remote/delete", {"serial": SERIAL, "paths": ["/storage/emulated/0/"]})
        self.assertEqual(code, 400)
        code, _ = self.call("/api/remote/delete", {"serial": SERIAL, "paths": ["/sdcard/Android"]})
        self.assertEqual(code, 400)

    def test_push_skips_mac_junk(self):
        src = os.path.join(self.home, "Desktop", "Da caricare")
        write(os.path.join(src, ".DS_Store"), 10)
        write(os.path.join(src, "._a.txt"), 10)
        write(os.path.join(src, "a b.txt"), data=b"ciao")
        write(os.path.join(src, "sub", "c.bin"), 300_000)
        j = self.run_job("/api/transfer/push", {"serial": SERIAL, "sources": [src], "dest": "/sdcard/Documents"})
        self.assertEqual((j["status"], j["done_files"]), ("done", 2), j["errors"])
        got = sorted(os.path.relpath(os.path.join(r, f), self.phone("Documents"))
                     for r, _, fs in os.walk(self.phone("Documents")) for f in fs)
        self.assertEqual(got, ["Da caricare/a b.txt", "Da caricare/sub/c.bin"])
        j = self.run_job("/api/transfer/push", {"serial": SERIAL, "sources": [src], "dest": "/sdcard/Documents"})
        self.assertEqual(j["skipped"], 2)

    def test_mkdir_rename_delete(self):
        self.assertEqual(self.call("/api/remote/mkdir", {"serial": SERIAL, "path": "/sdcard/Documents", "name": "Nuova"})[0], 200)
        write(self.phone("Documents/Altra/x.txt"), 5)
        code, err = self.call("/api/remote/rename", {"serial": SERIAL, "path": "/sdcard/Documents/Nuova", "name": "Altra"})
        self.assertEqual(code, 400)
        self.assertIn("Esiste", err["error"])
        self.assertEqual(self.call("/api/remote/rename", {"serial": SERIAL, "path": "/sdcard/Documents/Nuova", "name": "Rinominata"})[0], 200)
        j = self.run_job("/api/remote/delete", {"serial": SERIAL, "paths": ["/sdcard/Documents/Rinominata", "/sdcard/Documents/Altra"]})
        self.assertEqual(j["status"], "done")
        self.assertEqual(os.listdir(self.phone("Documents")), [])

    def test_cancel_removes_partial_files(self):
        os.environ["FAKE_RATE"] = "4e6"
        try:
            for i in range(4):
                write(self.phone(f"DCIM/Camera/VID_{i}.mp4"), 3_000_000)
            code, job = self.call("/api/transfer/pull", {"serial": SERIAL, "sources": ["/sdcard/DCIM/Camera"], "dest": self.mac})
            time.sleep(1.2)
            self.call("/api/jobs/cancel", {"id": job["id"]})
            j = self.run_job_wait(job["id"])
            self.assertEqual(j["status"], "cancelled")
        finally:
            os.environ["FAKE_RATE"] = "200e6"
        folder = os.path.join(self.mac, "Camera")
        for f in os.listdir(folder):  # solo file completi
            self.assertEqual(os.path.getsize(os.path.join(folder, f)), 3_000_000)
        self.assertLess(len(os.listdir(folder)), 4)

    def run_job_wait(self, job_id):
        for _ in range(200):
            _, data = self.call("/api/jobs")
            j = next(x for x in data["jobs"] if x["id"] == job_id)
            if j["status"] not in ("queued", "scanning", "running"):
                return j
            time.sleep(0.05)
        self.fail("job non terminato")

    def test_file_preview_with_range(self):
        data = bytes(range(256)) * 400
        write(self.phone("Download/v.mp4"), data=data)
        url = f"{self.base}/api/remote/file?serial={SERIAL}&path=/sdcard/Download/v.mp4&t={self.app.token}"
        with urllib.request.urlopen(url) as r:
            self.assertEqual(r.headers["Content-Type"], "video/mp4")
            self.assertEqual(r.read(), data)
        req = urllib.request.Request(url, headers={"Range": "bytes=1000-1999"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.headers["Content-Range"], f"bytes 1000-1999/{len(data)}")
            self.assertEqual(r.read(), data[1000:2000])
        # file del Mac
        write(os.path.join(self.mac, "n.txt"), data=b"ciao mondo")
        q = urllib.parse.urlencode({"path": os.path.join(self.mac, "n.txt"), "t": self.app.token})
        with urllib.request.urlopen(f"{self.base}/api/local/file?{q}") as r:
            self.assertEqual(r.read(), b"ciao mondo")
            self.assertEqual(r.headers["Content-Security-Policy"], "sandbox")
        # senza token: vietato
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(url.split("&t=")[0])


class UnitTest(unittest.TestCase):
    def test_app_mode_exits_after_window_closed(self):
        app = server.App(app_mode=True)
        closed = threading.Event()
        app.server = type("S", (), {"shutdown": lambda self: closed.set()})()
        app.touch()
        app.bye_at = time.time() + 0.01  # la pagina ha appena chiamato /api/bye
        threading.Thread(target=app.watch, kwargs={"grace": 0.1}, daemon=True).start()
        self.assertTrue(closed.wait(5))


    def test_keep_dir(self):
        self.assertTrue(adb.keep_dir("/sdcard/DCIM"))
        self.assertTrue(adb.keep_dir("/storage/emulated/0/DCIM/Camera"))
        self.assertTrue(adb.keep_dir("/storage/ABCD-1234/DCIM"))
        self.assertFalse(adb.keep_dir("/sdcard/Vacanze"))
        self.assertFalse(adb.keep_dir("/sdcard/DCIM/Camera/2024"))

    def test_parse_range(self):
        self.assertEqual(server.parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(server.parse_range("bytes=900-", 1000), (900, 999))
        self.assertEqual(server.parse_range("bytes=-100", 1000), (900, 999))
        self.assertIsNone(server.parse_range("bytes=2000-", 1000))
        self.assertIsNone(server.parse_range(None, 1000))

    def test_unique_name(self):
        from droidmac.localfs import unique_name
        taken = {"/a/foto.jpg", "/a/foto (1).jpg"}
        self.assertEqual(unique_name("/a/foto.jpg", taken.__contains__, os.path.join, os.path.split), "/a/foto (2).jpg")


if __name__ == "__main__":
    unittest.main()
