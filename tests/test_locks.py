from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from voicepipe.locks import LockHeld, PidFileLock


def test_pidfilelock_allows_existing_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "voicepipe.lock"
    lock_path.write_text("stale\n", encoding="utf-8")

    with PidFileLock(lock_path):
        pass


def test_pidfilelock_blocks_other_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "voicepipe.lock"
    ready_path = tmp_path / "ready.txt"
    script_path = tmp_path / "child_lock.py"
    repo_root = Path(__file__).resolve().parents[1]

    code = textwrap.dedent(
        """
        from __future__ import annotations

        import sys
        from pathlib import Path

        from voicepipe.locks import PidFileLock

        lock_path = Path(sys.argv[1])
        ready_path = Path(sys.argv[2])

        with PidFileLock(lock_path):
            ready_path.write_text("ready\\n", encoding="utf-8")
            sys.stdout.write("LOCKED\\n")
            sys.stdout.flush()
            sys.stdin.read(1)
        """
    ).strip()
    script_path.write_text(code, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{env.get('PYTHONPATH', '')}"

    proc = subprocess.Popen(
        [sys.executable, str(script_path), str(lock_path), str(ready_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if ready_path.exists():
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)

        if not ready_path.exists():
            out, err = proc.communicate(timeout=5)
            raise AssertionError(
                f"child did not acquire lock (rc={proc.returncode}):\nstdout:\n{out}\nstderr:\n{err}"
            )

        with pytest.raises(LockHeld):
            PidFileLock(lock_path).acquire()
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    with PidFileLock(lock_path):
        pass
