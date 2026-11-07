"""Server HTTP locale: interfaccia web + API JSON.

Avvio:  python3 -m droidmac [--port 8765] [--no-browser] [--app]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import APP_NAME, VERSION, adb
from .adb import AdbError, q, rnorm
from .jobs import JobQueue, delete_job, pull_job, push_job
from .localfs import lnorm, local_ls, local_mkdir, local_places

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".heic": "image/heic", ".heif": "image/heif",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".avif": "image/avif",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".3gp": "video/3gpp", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".amr": "audio/amr",
    ".pdf": "application/pdf",
}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".vcf", ".ini", ".yml", ".yaml", ".srt"}


def file_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXT:
        return "text/plain; charset=utf-8"
    return TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def parse_range(header, size):
    """'bytes=100-' -> (100, size-1). None se assente o non valido."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or size == 0 or (not m.group(1) and not m.group(2)):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:  # ultimi N byte
        start, end = max(0, size - int(m.group(2))), size - 1
    return (start, end) if start <= end < size else None


class App:
    """Stato del processo: token, coda, ciclo di vita."""

    def __init__(self, app_mode=False):
        self.token = secrets.token_urlsafe(24)
        self.jobs = JobQueue()
        self.app_mode = app_mode
        self.last_seen = time.time()
        self.bye_at = None
        self.server = None

    def touch(self):
        self.last_seen = time.time()
        self.bye_at = None

    def watch(self, grace=90, idle=1800):
        """In modalita' app chiude il server quando la finestra viene chiusa
        (dopo aver finito i trasferimenti in corso)."""
        while True:
            time.sleep(2)
            if self.jobs.busy():
                continue
            now = time.time()
            closed = self.bye_at and now - self.bye_at > grace and self.last_seen <= self.bye_at
            if closed or now - self.last_seen > idle:
                print("Finestra chiusa: esco.", flush=True)
                self.server.shutdown()
                return

    # ---------------- API ----------------

    def api_get(self):
        return {
            "/api/state": lambda qs: {
                "adb": adb.ADB, "adb_version": adb.run_adb(["version"], timeout=10)[0].split("\n")[0] if adb.ADB else "",
                "version": VERSION, "platform": sys.platform, "home": os.path.expanduser("~"), "app_mode": self.app_mode,
            },
            "/api/devices": lambda qs: {"devices": adb.list_devices()},
            "/api/device": lambda qs: adb.device_info(qs["serial"]),
            "/api/remote/ls": lambda qs: adb.remote_ls(qs["serial"], qs.get("path", "/sdcard")),
            "/api/local/ls": lambda qs: local_ls(qs.get("path", "~")),
            "/api/local/places": lambda qs: {"places": local_places()},
            "/api/jobs": lambda qs: {"jobs": [j.to_dict() for j in self.jobs.list()]},
        }

    def api_post(self):
        def ok(fn):
            def wrapped(b):
                fn(b)
                return {"ok": True}
            return wrapped

        return {
            "/api/transfer/pull": lambda b: self.jobs.submit(pull_job(
                b["serial"], b["sources"], b["dest"], bool(b.get("move")), bool(b.get("skip", True)))).to_dict(),
            "/api/transfer/push": lambda b: self.jobs.submit(push_job(
                b["serial"], b["sources"], b["dest"], bool(b.get("skip", True)))).to_dict(),
            "/api/remote/delete": lambda b: self.jobs.submit(delete_job(b["serial"], b["paths"])).to_dict(),
            "/api/remote/mkdir": ok(lambda b: self._remote_mkdir(b)),
            "/api/remote/rename": ok(lambda b: adb.remote_rename(b["serial"], b["path"], b["name"])),
            "/api/local/mkdir": ok(lambda b: local_mkdir(b["path"], b["name"])),
            "/api/local/reveal": ok(lambda b: self._reveal(b["path"])),
            "/api/connect": lambda b: {"message": adb.connect(b["host"], b.get("pair_host", ""), b.get("code", ""))},
            "/api/jobs/cancel": ok(lambda b: (j := self.jobs.get(b["id"])) and j.active and j.cancel()),
            "/api/jobs/clear": ok(lambda b: self.jobs.clear_finished()),
        }

    @staticmethod
    def _remote_mkdir(b):
        name = b["name"].strip()
        if not name or "/" in name:
            raise AdbError("Nome non valido")
        adb.remote_mkdirs(b["serial"], [rnorm(b["path"]).rstrip("/") + "/" + name])

    @staticmethod
    def _reveal(path):
        p = lnorm(path)
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p] if os.path.isfile(p) else ["open", p])
        else:
            subprocess.Popen(["xdg-open", p if os.path.isdir(p) else os.path.dirname(p)])


def make_handler(app: App, allowed_hosts):
    api_get, api_post = app.api_get(), app.api_post()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"{APP_NAME}/{VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        # ---------- risposte ----------

        def _headers(self, code, ctype, length, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _send(self, code, body: bytes, ctype, extra=None):
            self._headers(code, ctype, len(body), extra)
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        # ---------- sicurezza ----------

        def _guard(self, api: bool, qs=None) -> bool:
            # Contro DNS rebinding e richieste di altri siti verso localhost.
            if self.headers.get("Host", "") not in allowed_hosts:
                self._json({"error": "host non consentito"}, 403)
                return False
            if not api:
                return True
            # <img>/<video> e sendBeacon non possono inviare header: per loro il token e' in ?t=
            token = self.headers.get("X-Token") or (qs or {}).get("t", "")
            if not secrets.compare_digest(token, app.token):
                self._json({"error": "token non valido: ricarica la pagina"}, 403)
                return False
            return True

        def _call(self, fn, arg):
            try:
                self._json(fn(arg))
            except AdbError as e:
                self._json({"error": str(e)}, 400)
            except KeyError as e:
                self._json({"error": f"parametro mancante: {e}"}, 400)
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        # ---------- GET ----------

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            if path == "/ping":  # usato per l'istanza unica
                if self._guard(False):
                    self._json({"app": APP_NAME, "version": VERSION})
                return
            if path.startswith("/api/"):
                if not self._guard(True, qs):
                    return
                app.touch()
                if path == "/api/remote/file":
                    return self._remote_file(qs)
                if path == "/api/local/file":
                    return self._local_file(qs)
                fn = api_get.get(path)
                return self._call(fn, qs) if fn else self._json({"error": "not found"}, 404)
            if self._guard(False):
                self._static(path)

        def _static(self, path):
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/").removeprefix("static/")
            root = os.path.realpath(STATIC_DIR)
            full = os.path.realpath(os.path.join(root, name))
            if not full.startswith(root + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            with open(full, "rb") as f:
                body = f.read()
            if name == "index.html":
                body = body.replace(b"__DROIDMAC_TOKEN__", app.token.encode())
            self._send(200, body, TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

        def _file_headers(self, name, size):
            """Header comuni per l'anteprima: Range per i video, sandbox per sicurezza."""
            rng = parse_range(self.headers.get("Range"), size)
            extra = {"Accept-Ranges": "bytes", "Content-Disposition": "inline"}
            ctype = file_type(name)
            if ctype != "application/pdf":  # il visore PDF non funziona in sandbox
                extra["Content-Security-Policy"] = "sandbox"
            if rng:
                extra["Content-Range"] = f"bytes {rng[0]}-{rng[1]}/{size}"
                self._headers(206, ctype, rng[1] - rng[0] + 1, extra)
            else:
                self._headers(200, ctype, size, extra)
            return rng

        def _local_file(self, qs):
            p = lnorm(qs.get("path", ""))
            if not os.path.isfile(p):
                return self._json({"error": "file non trovato"}, 404)
            size = os.path.getsize(p)
            rng = self._file_headers(p, size)
            if self.command == "HEAD":
                return
            start, end = rng or (0, size - 1)
            try:
                with open(p, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        b = f.read(min(left, 1 << 18))
                        if not b:
                            break
                        self.wfile.write(b)
                        left -= len(b)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _remote_file(self, qs):
            try:
                serial, p = qs["serial"], rnorm(qs["path"])
                size = adb.remote_sizes(serial, [p]).get(p)
            except (KeyError, AdbError) as e:
                return self._json({"error": str(e)}, 400)
            if size is None:
                return self._json({"error": "file non trovato"}, 404)
            rng = self._file_headers(p, size)
            if self.command == "HEAD" or size == 0:
                return
            if rng:
                script = f"tail -c +{rng[0] + 1} {q(p)} | head -c {rng[1] - rng[0] + 1}"
            else:
                script = f"cat {q(p)}"
            proc = adb.stream(serial, script)
            try:
                while True:
                    b = proc.stdout.read(1 << 18)
                    if not b:
                        break
                    self.wfile.write(b)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.kill()
                proc.wait()
                proc.stdout.close()
                # se adb si interrompe prima di Content-Length, il client non deve restare appeso
                self.close_connection = True

        # ---------- POST ----------

        def do_POST(self):
            url = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            if not self._guard(True, qs):
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if url.path == "/api/bye":  # pagina chiusa (sendBeacon)
                    app.bye_at = time.time()
                    return self._json({"ok": True})
                body = json.loads(raw or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json({"error": "JSON non valido"}, 400)
            app.touch()
            fn = api_post.get(url.path)
            return self._call(fn, body) if fn else self._json({"error": "not found"}, 404)

    return Handler


def create_server(port=DEFAULT_PORT, app_mode=False, tries=20):
    """Crea il server sulla prima porta libera da `port` in su (0 = qualunque)."""
    app = App(app_mode)
    for p in ([0] if port == 0 else range(port, port + tries)):
        try:
            srv = ThreadingHTTPServer((HOST, p), None)
            break
        except OSError:
            continue
    else:
        raise SystemExit("Nessuna porta libera trovata.")
    real = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(app, {f"127.0.0.1:{real}", f"localhost:{real}"})
    srv.daemon_threads = True
    app.server = srv
    return app, srv


def find_running(port, tries=20):
    """URL di un DroidMac gia' in esecuzione, se c'e'."""
    for p in range(port, port + tries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/ping", timeout=0.4) as r:
                if json.load(r).get("app") == APP_NAME:
                    return f"http://127.0.0.1:{p}/"
        except Exception:  # noqa: BLE001
            continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="droidmac", description=f"{APP_NAME}: file Android ⇄ Mac via ADB")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DROIDMAC_PORT", DEFAULT_PORT)))
    ap.add_argument("--no-browser", action="store_true", help="non aprire il browser")
    ap.add_argument("--app", action="store_true", help="modalità app: esce quando chiudi la finestra")
    args = ap.parse_args(argv)

    running = find_running(args.port)
    if running:
        print(f"{APP_NAME} è già aperto: {running}")
        if not args.no_browser:
            webbrowser.open(running)
        return

    adb.ADB = adb.find_adb()
    app, server = create_server(args.port, app_mode=args.app)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if adb.ADB:
        threading.Thread(target=lambda: adb.run_adb(["start-server"], timeout=30), daemon=True).start()
    if args.app:
        threading.Thread(target=app.watch, daemon=True).start()

    print(f"{APP_NAME} {VERSION} — {url}")
    print(f"adb: {adb.ADB or 'NON TROVATO (brew install android-platform-tools)'}")
    if not args.app:
        print("Ctrl+C per chiudere.")
    sys.stdout.flush()
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChiuso.")
    finally:
        for j in app.jobs.list():
            if j.active:
                j.cancel()


if __name__ == "__main__":
    main()
