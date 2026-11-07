"""Coda dei trasferimenti: un lavoro alla volta, in un thread dedicato."""
from __future__ import annotations

import os
import posixpath
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque

from . import adb
from .adb import AdbError, chunk_args, rnorm
from .localfs import fsize, lnorm, local_tree, unique_name

# Un "chunk" = una invocazione di adb pull/push con piu' file: meno invocazioni
# significa meno overhead, chunk non enormi significa progresso fluido.
CHUNK_FILES = 60
CHUNK_BYTES = 384 * 1024 * 1024

ACTIVE = ("queued", "scanning", "running")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, title, run, refresh):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title, self.run, self.refresh = kind, title, run, refresh
        self.status = "queued"
        self.message = ""
        self.total_bytes = self.done_bytes = 0
        self.total_files = self.done_files = self.skipped = 0
        self.errors: list[str] = []
        self.current = ""
        self.created = time.time()
        self.started = self.finished = None
        self.cancelled = False
        self.proc = None
        self._samples = deque(maxlen=24)

    @property
    def active(self):
        return self.status in ACTIVE

    def progress(self, done_bytes):
        self.done_bytes = done_bytes
        self._samples.append((time.time(), done_bytes))

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def cancel(self):
        self.cancelled = True
        if self.proc:
            self.proc.kill()

    def speed(self):
        now = time.time()
        pts = [s for s in self._samples if now - s[0] < 6]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 0.5:
            return 0
        return max(0, (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0]))

    def to_dict(self):
        speed = self.speed() if self.status == "running" else 0
        remaining = self.total_bytes - self.done_bytes
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "message": self.message, "current": self.current,
            "total_bytes": self.total_bytes, "done_bytes": self.done_bytes,
            "total_files": self.total_files, "done_files": self.done_files,
            "skipped": self.skipped, "errors": self.errors[:200], "error_count": len(self.errors),
            "speed": speed, "eta": remaining / speed if speed > 0 else None,
            "created": self.created, "started": self.started, "finished": self.finished,
            "refresh": self.refresh,
        }


class JobQueue:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.q: "queue.Queue[Job]" = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="jobs").start()

    def submit(self, job: Job) -> Job:
        with self.lock:
            self.jobs[job.id] = job
        self.q.put(job)
        return job

    def list(self):
        with self.lock:
            return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def get(self, job_id):
        return self.jobs.get(job_id)

    def busy(self):
        return any(j.active for j in self.list())

    def clear_finished(self):
        with self.lock:
            for jid in [j.id for j in self.jobs.values() if j.finished]:
                del self.jobs[jid]

    def _worker(self):
        while True:
            job = self.q.get()
            if job.cancelled:
                job.status, job.finished = "cancelled", time.time()
                continue
            job.status, job.started = "running", time.time()
            try:
                job.run(job)
                job.status = "cancelled" if job.cancelled else ("partial" if job.errors else "done")
            except Cancelled:
                job.status = "cancelled"
            except Exception as e:  # noqa: BLE001 - l'errore va mostrato nella UI
                job.status = "error"
                job.message = str(e)
            finally:
                job.proc = None
                job.current = ""
                job.finished = time.time()


def run_proc(job: Job, cmd, measure=None, base=0, every=0.4):
    """Lancia adb pull/push e aggiorna il progresso finche' non termina."""
    with tempfile.TemporaryFile() as errf:
        job.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errf)
        last = 0.0
        while True:
            try:
                rc = job.proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                pass
            if job.cancelled:
                job.proc.kill()
            elif measure and time.time() - last >= every:
                last = time.time()
                try:
                    job.progress(base + measure())
                except Exception:  # noqa: BLE001 - la misura e' solo indicativa
                    pass
        job.proc = None
        errf.seek(0)
        err = adb.dec(errf.read()).strip()
    return rc, (err.splitlines()[-1] if err else "")


def make_chunks(tasks, dir_of):
    """Raggruppa i file per cartella di destinazione; i file rinominati vanno da soli."""
    groups: dict[str, list] = {}
    singles = []
    for t in tasks:
        if t["single"]:
            singles.append([t])
        else:
            groups.setdefault(dir_of(t), []).append(t)
    chunks = []
    for items in groups.values():
        cur, size = [], 0
        for t in items:
            if cur and (len(cur) >= CHUNK_FILES or size + t["size"] > CHUNK_BYTES):
                chunks.append(cur)
                cur, size = [], 0
            cur.append(t)
            size += t["size"]
        if cur:
            chunks.append(cur)
    return chunks + singles


def _title(verb, names, where):
    return f"{verb} {len(names)} elementi → {where}" if len(names) > 1 else f"{verb} {names[0]} → {where}"


def pull_job(serial, sources, dest, move=False, skip=True):
    """Telefono -> Mac. Con move=True un file viene eliminato dal telefono solo dopo
    aver verificato che la copia sul Mac abbia la stessa dimensione."""
    sources = [rnorm(s) for s in sources]
    if move:
        for s in sources:
            adb.assert_deletable(s)

    def run(job: Job):
        dest_abs = lnorm(dest)
        if not os.path.isdir(dest_abs):
            raise AdbError(f"Cartella di destinazione inesistente: {dest_abs}")
        job.status, job.message = "scanning", "Analisi dei file sul telefono…"
        mkdirs, raw, src_dirs = set(), [], []
        for src in sources:
            job.check()
            base = posixpath.dirname(src)
            dirs, files = adb.remote_tree(serial, src)
            if not dirs and not files:
                job.errors.append(f"{src}: non trovato o non leggibile")
                continue
            for d in dirs:
                mkdirs.add(os.path.join(dest_abs, *posixpath.relpath(d, base).split("/")))
            src_dirs += dirs
            for r, size in files:
                raw.append((r, size, os.path.join(dest_abs, *posixpath.relpath(r, base).split("/"))))

        planned, tasks, skipped = set(), [], []

        def taken(p):  # il Mac di solito non distingue maiuscole/minuscole
            return os.path.exists(p) or p.lower() in planned

        for r, size, local in raw:
            if taken(local):
                if skip and local.lower() not in planned and os.path.isfile(local) and fsize(local) == size:
                    skipped.append(r)
                    continue
                local = unique_name(local, taken, os.path.join, os.path.split)
            planned.add(local.lower())
            tasks.append({"remote": r, "local": local, "size": size,
                          "single": os.path.basename(local) != posixpath.basename(r)})

        job.total_files = len(tasks)
        job.total_bytes = sum(t["size"] for t in tasks)
        job.skipped = len(skipped)
        for d in sorted(mkdirs):
            os.makedirs(d, exist_ok=True)
        job.status, job.message = "running", ""

        to_delete = list(skipped) if move else []  # gia' presenti e identici sul Mac
        done = 0
        for chunk in make_chunks(tasks, lambda t: os.path.dirname(t["local"])):
            job.check()
            first = chunk[0]
            if first["single"]:
                cmd = adb.base_cmd(serial) + ["pull", "-a", first["remote"], first["local"]]
            else:
                cmd = adb.base_cmd(serial) + ["pull", "-a"] + [t["remote"] for t in chunk] + [os.path.dirname(first["local"])]
            job.current = first["remote"] if len(chunk) == 1 else f'{posixpath.dirname(first["remote"])}  ({len(chunk)} file)'
            _, err = run_proc(job, cmd, measure=lambda: sum(max(0, fsize(t["local"])) for t in chunk), base=done)
            for t in chunk:
                if fsize(t["local"]) == t["size"]:
                    done += t["size"]
                    job.done_files += 1
                    if move:
                        to_delete.append(t["remote"])
                    continue
                if os.path.exists(t["local"]):
                    try:
                        os.remove(t["local"])  # niente file a meta'
                    except OSError:
                        pass
                if not job.cancelled:
                    job.errors.append(f'{t["remote"]}: {err or "copia incompleta"}')
            job.progress(done)
            if to_delete:
                job.errors += adb.remote_rm(serial, to_delete)
                to_delete = []
        if to_delete:
            job.errors += adb.remote_rm(serial, to_delete)
        if move and not job.cancelled:
            adb.remote_rmdirs(serial, [d for d in src_dirs if not adb.keep_dir(d)])

    title = _title("Sposta" if move else "Copia", [posixpath.basename(s) for s in sources], "Mac")
    return Job("move" if move else "pull", title, run, ["mac", "phone"] if move else ["mac"])


def push_job(serial, sources, dest, skip=True):
    """Mac -> Telefono."""
    sources = [lnorm(s) for s in sources]
    dest = rnorm(dest)

    def run(job: Job):
        job.status, job.message = "scanning", "Analisi dei file sul Mac…"
        mkdirs, raw, existing = set(), [], {}
        for src in sources:
            job.check()
            base = os.path.dirname(src)
            dirs, files = local_tree(src)
            for d in dirs:
                mkdirs.add(posixpath.join(dest, *os.path.relpath(d, base).split(os.sep)))
            for loc, size in files:
                raw.append((loc, size, posixpath.join(dest, *os.path.relpath(loc, base).split(os.sep))))
            _, rfiles = adb.remote_tree(serial, posixpath.join(dest, os.path.basename(src)))
            existing.update((p.lower(), s) for p, s in rfiles)

        planned, tasks = set(), []

        def taken(p):  # anche /sdcard non distingue maiuscole/minuscole
            return p.lower() in existing or p.lower() in planned

        for loc, size, remote in raw:
            if taken(remote):
                if skip and remote.lower() not in planned and existing.get(remote.lower()) == size:
                    job.skipped += 1
                    continue
                remote = unique_name(remote, taken, posixpath.join, posixpath.split)
            planned.add(remote.lower())
            tasks.append({"local": loc, "remote": remote, "size": size,
                          "single": posixpath.basename(remote) != os.path.basename(loc)})

        job.total_files = len(tasks)
        job.total_bytes = sum(t["size"] for t in tasks)
        if mkdirs:
            adb.remote_mkdirs(serial, mkdirs)
        job.status, job.message = "running", ""

        done = 0
        for chunk in make_chunks(tasks, lambda t: posixpath.dirname(t["remote"])):
            job.check()
            first = chunk[0]
            if first["single"]:
                cmd = adb.base_cmd(serial) + ["push", first["local"], first["remote"]]
            else:
                cmd = adb.base_cmd(serial) + ["push"] + [t["local"] for t in chunk] + [posixpath.dirname(first["remote"]) + "/"]
            job.current = first["local"] if len(chunk) == 1 else f'{os.path.dirname(first["local"])}  ({len(chunk)} file)'
            remotes = [t["remote"] for t in chunk]
            _, err = run_proc(job, cmd, measure=lambda: sum(adb.remote_sizes(serial, remotes).values()),
                              base=done, every=1.0)
            sizes = adb.remote_sizes(serial, remotes)
            partial = []
            for t in chunk:
                if sizes.get(t["remote"]) == t["size"]:
                    done += t["size"]
                    job.done_files += 1
                    continue
                if t["remote"] in sizes:
                    partial.append(t["remote"])
                if not job.cancelled:
                    job.errors.append(f'{t["local"]}: {err or "copia incompleta"}')
            if partial:
                adb.remote_rm(serial, partial)
            job.progress(done)

    return Job("push", _title("Copia", [os.path.basename(s) for s in sources], "Telefono"), run, ["phone"])


def delete_job(serial, paths):
    paths = [rnorm(p) for p in paths]
    for p in paths:
        adb.assert_deletable(p)

    def run(job: Job):
        job.total_files = len(paths)
        for part in chunk_args(paths, budget=4000):
            job.check()
            job.current = part[0]
            job.errors += adb.remote_rm(serial, part, recursive=True)
            job.done_files += len(part)

    names = [posixpath.basename(p) for p in paths]
    title = f"Elimina {len(paths)} elementi dal telefono" if len(paths) > 1 else f"Elimina {names[0]}"
    return Job("delete", title, run, ["phone"])


__all__ = ["Job", "JobQueue", "pull_job", "push_job", "delete_job"]
