"""One shared, persistent request spacing budget for all Massive workers."""
import fcntl
import math
import time
from pathlib import Path


def wait_for_slot(root: Path) -> None:
    folder = root / "manifests"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "massive-rate.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = folder / "massive-last-request.txt"
        try:
            previous = float(stamp.read_text()) if stamp.exists() else 0.0
            if not math.isfinite(previous) or previous < 0:
                previous = time.time()
        except (OSError, ValueError):
            # A broken stamp must not silently remove the throttle.
            previous = time.time()
        remaining = 16 - (time.time() - previous)
        if remaining > 0:
            time.sleep(min(remaining, 16))
        stamp.write_text(str(time.time()))
