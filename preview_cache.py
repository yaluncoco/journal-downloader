"""Bounded, in-memory validation reuse for previews only, never download resume."""
from collections import OrderedDict
from pathlib import Path

from library_store import existing_sidecar


def fingerprint(path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


class PreviewCache:
    def __init__(self, limit=4096):
        self.limit = limit
        self.entries = OrderedDict()
        self.checked = 0

    def read(self, path, reader):
        self.checked += 1
        path = Path(path)
        sidecar = existing_sidecar(path)
        stamp = fingerprint(path), str(sidecar), fingerprint(sidecar)
        key = str(path.absolute())
        entry = self.entries.pop(key, None)
        if entry and entry[0] == stamp:
            self.entries[key] = entry
            return dict(entry[1])
        value = reader(path)
        # Don't retain a result if either file changed during validation.
        if stamp == (fingerprint(path), str(existing_sidecar(path)), fingerprint(sidecar)):
            self.entries[key] = (stamp, dict(value))
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return value
