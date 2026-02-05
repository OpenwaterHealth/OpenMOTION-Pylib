from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DFUProgress:
    phase: str
    percent: int | None
    bytes_written: int | None
    message: str
    elapsed_s: float


@dataclass(frozen=True)
class DFUResult:
    command: list[str]
    returncode: int
    stdout: str
    success: bool


ProgressCallback = Callable[[DFUProgress], None]
LineCallback = Callable[[str], None]


class DFUProgrammer:
    """Erase/program a DFU-mode device using the repo-bundled dfu-util.

    This class is designed to be UI/script friendly:
    - streams dfu-util output line-by-line
    - parses Erase/Download progress (percent + bytes)
    - reports progress via an optional callback

    Note: dfu-util typically performs an erase step automatically before download
    when programming flash in DfuSe mode, so a separate explicit erase command is
    not always required.
    """

    DEFAULT_ADDRESS = "0x08000000"

    def __init__(
        self,
        *,
        dfu_util_path: Path | None = None,
        repo_root: Path | None = None,
        vidpid: str | None = None,
    ):
        self.repo_root = repo_root or Path(__file__).resolve().parents[1]
        self.dfu_util_path = Path(dfu_util_path) if dfu_util_path else self._locate_dfu_util(self.repo_root)
        self.dfu_dir = self.dfu_util_path.parent
        self.vidpid = vidpid

    @staticmethod
    def _platform_subdir() -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()

        if system.startswith("darwin"):
            return "darwin-x86_64"
        if system.startswith("linux"):
            return "linux-amd64"
        if system.startswith("windows"):
            return "win64" if "64" in machine else "win32"

        raise RuntimeError(f"Unsupported OS: {platform.system()}")

    @classmethod
    def _locate_dfu_util(cls, repo_root: Path) -> Path:
        subdir = cls._platform_subdir()
        dfu_dir = repo_root / "dfu-util" / subdir
        exe = "dfu-util.exe" if platform.system().lower().startswith("windows") else "dfu-util"
        dfu_path = dfu_dir / exe
        if not dfu_path.is_file():
            raise FileNotFoundError(f"dfu-util binary not found: {dfu_path}")
        return dfu_path

    def _dfu_util_base_args(self) -> list[str]:
        args = [str(self.dfu_util_path)]
        if self.vidpid:
            args += ["-d", self.vidpid]
        return args

    def list_devices(self) -> str:
        cmd = self._dfu_util_base_args() + ["-l"]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        return r.stdout or ""

    def wait_for_dfu_device(
        self,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.5,
        require_found_dfu: bool = True,
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            out = self.list_devices()
            if self.vidpid and self.vidpid.lower() in out.lower():
                return True
            if require_found_dfu and "Found DFU" in out:
                return True
            time.sleep(poll_interval_s)
        return False

    @staticmethod
    def _parse_percent(line: str) -> int | None:
        m = re.search(r"(\d{1,3})%", line)
        if not m:
            return None
        try:
            p = int(m.group(1))
        except ValueError:
            return None
        return p if 0 <= p <= 100 else None

    @staticmethod
    def _parse_bytes(line: str) -> int | None:
        m = re.search(r"(\d+)\s+bytes", line)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _phase_from_line(line: str) -> str | None:
        trimmed = line.strip()
        if trimmed.startswith("Erase"):
            return "erase"
        if trimmed.startswith("Download"):
            return "download"
        return None

    @staticmethod
    def _has_dfu_suffix(path: Path) -> bool:
        """Detects a DFU suffix (16 bytes) by checking for the 'UFD' signature."""
        suffix_len = 16
        try:
            data = path.read_bytes()
        except OSError:
            return False
        if len(data) < suffix_len:
            return False
        suffix = data[-suffix_len:]
        return suffix[8:11] == b"UFD" and suffix[11] == suffix_len

    def strip_dfu_suffix_to_temp(self, bin_path: Path) -> Path:
        """If `bin_path` has a DFU suffix, write a suffix-free temp file and return it."""
        if not self._has_dfu_suffix(bin_path):
            return bin_path

        data = bin_path.read_bytes()
        suffix_free = data[:-16]
        fd, out_path = tempfile.mkstemp(prefix=bin_path.stem + "-nosuffix-", suffix=".bin")
        os.close(fd)
        Path(out_path).write_bytes(suffix_free)
        return Path(out_path)

    def flash_bin(
        self,
        bin_path: Path,
        *,
        address: str = DEFAULT_ADDRESS,
        alt: int = 0,
        leave: bool = True,
        usb_reset: bool = True,
        transfer_size: int | None = None,
        verbose: int = 0,
        normalize_dfu_suffix: bool = True,
        progress: ProgressCallback | None = None,
        line_callback: LineCallback | None = None,
        echo_output: bool = False,
        echo_progress_lines: bool = False,
    ) -> DFUResult:
        """Program flash (erase + download) using dfu-util.

        Returns DFUResult with stdout and success flag.
        """
        bin_path = Path(bin_path)
        if not bin_path.is_file():
            raise FileNotFoundError(f"Firmware file not found: {bin_path}")

        if normalize_dfu_suffix:
            bin_path = self.strip_dfu_suffix_to_temp(bin_path)

        s_opts = f"{address}:leave" if leave else address

        cmd: list[str] = self._dfu_util_base_args()
        if verbose:
            cmd += ["-v"] * verbose
        if transfer_size is not None:
            cmd += ["-t", str(int(transfer_size))]

        cmd += ["-a", str(int(alt)), "-s", s_opts, "-D", str(bin_path)]
        if usb_reset:
            cmd += ["-R"]

        stdout = self._run_streaming(
            cmd,
            progress=progress,
            line_callback=line_callback,
            echo_output=echo_output,
            echo_progress_lines=echo_progress_lines,
        )

        # dfu-util often reports success via this string even if it exits non-zero
        success = "File downloaded successfully" in stdout
        if not success:
            # fallback: clean exit
            # returncode is filled in by _run_streaming via CompletedProcess-like return
            pass

        # _run_streaming returns stdout only; we need the rc too.
        # Re-run proc? no. _run_streaming stores rc on self for last call.
        rc = getattr(self, "_last_returncode", 1)
        if not success:
            success = rc == 0

        return DFUResult(command=cmd, returncode=rc, stdout=stdout, success=success)

    def _run_streaming(
        self,
        cmd: list[str],
        *,
        progress: ProgressCallback | None,
        line_callback: LineCallback | None,
        echo_output: bool,
        echo_progress_lines: bool,
    ) -> str:
        start = time.monotonic()
        out_lines: list[str] = []

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            out_lines.append(line)

            phase = self._phase_from_line(line)
            percent = self._parse_percent(line)
            bytes_written = self._parse_bytes(line)

            if progress is not None:
                progress(
                    DFUProgress(
                        phase=phase or "output",
                        percent=percent,
                        bytes_written=bytes_written,
                        message=line.rstrip("\n"),
                        elapsed_s=time.monotonic() - start,
                    )
                )

            is_progress_line = (phase in {"erase", "download"}) and (percent is not None)
            should_echo = (echo_progress_lines or not is_progress_line)
            if should_echo and line_callback is not None:
                line_callback(line.rstrip("\n"))
            if echo_output and should_echo:
                print(line.rstrip("\n"))

        proc.wait()
        self._last_returncode = proc.returncode
        return "".join(out_lines)

    # Optional explicit erase helpers (best-effort; depends on device/bootloader)
    def mass_erase(
        self,
        *,
        address: str = DEFAULT_ADDRESS,
        alt: int = 0,
        force: bool = True,
        verbose: int = 0,
        progress: ProgressCallback | None = None,
        line_callback: LineCallback | None = None,
        echo_output: bool = False,
        echo_progress_lines: bool = False,
    ) -> DFUResult:
        """Attempt a DfuSe mass-erase using dfu-util.

        Many STM32 ROM DFU workflows don't require an explicit erase because dfu-util
        will erase as part of a download. This method is provided for cases where
        you want an explicit erase step.
        """
        opts = f"{address}:mass-erase" + (":force" if force else "")

        cmd: list[str] = self._dfu_util_base_args()
        if verbose:
            cmd += ["-v"] * verbose
        cmd += ["-a", str(int(alt)), "-s", opts, "-D", "-"]

        stdout = self._run_streaming(
            cmd,
            progress=progress,
            line_callback=line_callback,
            echo_output=echo_output,
            echo_progress_lines=echo_progress_lines,
        )
        rc = getattr(self, "_last_returncode", 1)
        return DFUResult(command=cmd, returncode=rc, stdout=stdout, success=(rc == 0))
