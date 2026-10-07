"""Run a command, stream its output unchanged, and parse test results.

Used by ``cb run -- <cmd>``. Recognizes pytest, jest, vitest, ``go test`` and
``cargo test``; for anything else only the exit code and the last 20 lines are kept.
Callers must sanitize before storing (the database layer does so too).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

TAIL_LINES = 20
MAX_FAILURES = 10
MAX_MESSAGE_CHARS = 300
# Output kept in memory for parsing; older lines are dropped first.
MAX_CAPTURED_LINES = 5000
EXIT_NOT_FOUND = 127

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


@dataclass
class TestReport:
    __test__ = False  # Not a pytest test class.

    runner: str  # "pytest" | "jest" | "vitest" | "go" | "cargo" | "unknown"
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def recognized(self) -> bool:
        return self.runner != "unknown"


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    output: list[str]  # Captured lines (ANSI stripped), at most MAX_CAPTURED_LINES.

    @property
    def tail(self) -> list[str]:
        return self.output[-TAIL_LINES:]


# ----- running -----------------------------------------------------------------------


def _pump(src: BinaryIO, dst: BinaryIO | None, sink: deque[str], lock: threading.Lock) -> None:
    """Copy ``src`` to ``dst`` byte-for-byte while collecting decoded lines in ``sink``."""
    pending = b""
    while True:
        chunk = src.read1(65536) if hasattr(src, "read1") else src.read(65536)
        if not chunk:
            break
        if dst is not None:
            try:
                dst.write(chunk)
                dst.flush()
            except (OSError, ValueError):
                dst = None  # Our stdout went away; keep draining the child.
        pending += chunk
        *complete, pending = pending.split(b"\n")
        if complete:
            with lock:
                sink.extend(strip_ansi(c.decode("utf-8", "replace")).rstrip("\r") for c in complete)
    if pending:
        with lock:
            sink.append(strip_ansi(pending.decode("utf-8", "replace")).rstrip("\r"))


def _binary_stream(stream: object) -> BinaryIO | None:
    buffer = getattr(stream, "buffer", None)
    return buffer if buffer is not None else None


def _color_env(stdout_is_tty: bool) -> dict[str, str]:
    env = dict(os.environ)
    if stdout_is_tty:
        # Output goes through a pipe; ask common tools to keep their colors anyway.
        env.setdefault("PY_COLORS", "1")
        env.setdefault("FORCE_COLOR", "1")
        env.setdefault("CARGO_TERM_COLOR", "always")
    return env


def run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    stdout: object | None = None,
    stderr: object | None = None,
) -> CommandResult:
    """Run ``argv``, streaming stdout/stderr to ours unchanged, and capture the output."""
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    out_bin, err_bin = _binary_stream(out_stream), _binary_stream(err_stream)
    is_tty = bool(getattr(out_stream, "isatty", lambda: False)())
    for stream in (out_stream, err_stream):
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()

    sink: deque[str] = deque(maxlen=MAX_CAPTURED_LINES)
    lock = threading.Lock()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_color_env(is_tty),
        )
    except FileNotFoundError:
        msg = f"cb run: command not found: {argv[0]}\n"
        if err_bin is not None:
            err_bin.write(msg.encode())
            err_bin.flush()
        return CommandResult(argv=argv, exit_code=EXIT_NOT_FOUND, output=[msg.strip()])
    except PermissionError:
        msg = f"cb run: permission denied: {argv[0]}\n"
        if err_bin is not None:
            err_bin.write(msg.encode())
            err_bin.flush()
        return CommandResult(argv=argv, exit_code=126, output=[msg.strip()])

    assert proc.stdout is not None and proc.stderr is not None
    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, out_bin, sink, lock), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err_bin, sink, lock), daemon=True),
    ]
    for t in threads:
        t.start()
    returncode: int | None = None
    while returncode is None:
        try:
            returncode = proc.wait()
        except KeyboardInterrupt:
            # The child got the same SIGINT (same process group); wait for it to exit.
            continue
    for t in threads:
        t.join()
    if returncode < 0:  # Killed by a signal: report like a shell does.
        returncode = 128 + abs(returncode)
    return CommandResult(argv=argv, exit_code=returncode, output=list(sink))


# ----- parsing -----------------------------------------------------------------------------


def _msg(text: str) -> str:
    text = " ".join(text.split())
    return text[: MAX_MESSAGE_CHARS - 3] + "..." if len(text) > MAX_MESSAGE_CHARS else text


def _add_failure(failures: list[dict[str, str]], test: str, message: str = "") -> None:
    if len(failures) < MAX_FAILURES and all(f["test"] != test for f in failures):
        failures.append({"test": test.strip(), "message": _msg(message)})


_PYTEST_COUNT = re.compile(
    r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)"
)
_PYTEST_SUMMARY = re.compile(r"^=*\s*(?:\d+ \w+(?:, )?)+.* in [\d.]+s")
_PYTEST_NO_TESTS = re.compile(r"no tests ran in [\d.]+s")
_PYTEST_FAILED = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")


def parse_pytest(lines: list[str]) -> TestReport | None:
    summary = None
    for line in reversed(lines):
        stripped = line.strip().strip("=").strip()
        if _PYTEST_SUMMARY.match(stripped) and _PYTEST_COUNT.search(stripped):
            summary = stripped
            break
        if _PYTEST_NO_TESTS.search(stripped):
            return TestReport("pytest", 0, 0, 0)
    if summary is None:
        return None
    counts: dict[str, int] = {}
    for n, word in _PYTEST_COUNT.findall(summary):
        key = word.rstrip("s") if word.startswith(("error", "warning")) else word
        counts[key] = counts.get(key, 0) + int(n)
    report = TestReport(
        "pytest",
        passed=counts.get("passed", 0) + counts.get("xpassed", 0),
        failed=counts.get("failed", 0) + counts.get("error", 0),
        skipped=counts.get("skipped", 0) + counts.get("xfailed", 0),
    )
    for line in lines:
        m = _PYTEST_FAILED.match(line.strip())
        if m:
            _add_failure(report.failures, m.group(2), m.group(3) or m.group(1).lower())
    return report


_JEST_TESTS = re.compile(r"^Tests:\s+(.*\d+ total)")
_JEST_BULLET = re.compile(r"^\s*● (.+)$")
_VITEST_TESTS = re.compile(r"^\s*Tests\s+(.*)\(\d+\)")
_VITEST_FAIL = re.compile(r"^\s*(?:FAIL|\u00d7|\u2717)\s+(.+ > .+?)(?:\s+\d+m?s)?$")
_ERROR_PREFIXES = ("Assert", "Error", "Type")
_COUNT_WORD = re.compile(r"(\d+) (passed|failed|skipped|todo|pending)")


def _counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for n, word in _COUNT_WORD.findall(text):
        out[word] = out.get(word, 0) + int(n)
    return out


def parse_jest(lines: list[str]) -> TestReport | None:
    summary = next((m.group(1) for ln in reversed(lines) if (m := _JEST_TESTS.match(ln))), None)
    if summary is None:
        return None
    c = _counts(summary)
    report = TestReport(
        "jest",
        passed=c.get("passed", 0),
        failed=c.get("failed", 0),
        skipped=c.get("skipped", 0) + c.get("todo", 0) + c.get("pending", 0),
    )
    for i, line in enumerate(lines):
        m = _JEST_BULLET.match(line)
        if m and "Test suite failed to run" not in m.group(1):
            message = next((ln.strip() for ln in lines[i + 1 : i + 6] if ln.strip()), "")
            _add_failure(report.failures, m.group(1), message)
    return report


def parse_vitest(lines: list[str]) -> TestReport | None:
    summary = next((m.group(1) for ln in reversed(lines) if (m := _VITEST_TESTS.match(ln))), None)
    if summary is None:
        return None
    c = _counts(summary)
    report = TestReport(
        "vitest",
        passed=c.get("passed", 0),
        failed=c.get("failed", 0),
        skipped=c.get("skipped", 0) + c.get("todo", 0),
    )
    for i, line in enumerate(lines):
        m = _VITEST_FAIL.match(line)
        if m:
            following = (ln.strip() for ln in lines[i + 1 : i + 4])
            message = next((ln for ln in following if ln.startswith(_ERROR_PREFIXES)), "")
            _add_failure(report.failures, m.group(1).strip(), message)
    return report


_GO_RESULT = re.compile(r"^\s*--- (PASS|FAIL|SKIP): (\S+)")
_GO_PKG = re.compile(r"^(ok|FAIL)\s+\S+\s+(\(cached\)|[\d.]+s|\[)")


def _go_failure_message(lines: list[str], idx: int) -> str:
    """Log line for a failed test: after `--- FAIL` normally, before it with `-v`."""
    after: list[str] = []
    for ln in lines[idx + 1 : idx + 5]:
        if ln.startswith("=== RUN") or _GO_RESULT.match(ln):
            break
        after.append(ln)
    before: list[str] = []
    for ln in reversed(lines[max(idx - 20, 0) : idx]):
        if ln.startswith("=== RUN") or _GO_RESULT.match(ln):
            break
        before.insert(0, ln)
    for ln in [*after, *before]:
        if ln.startswith("    ") and not _GO_RESULT.match(ln):
            return ln.strip()
    return ""


def parse_go(lines: list[str]) -> TestReport | None:
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    seen_pkg = False
    report = TestReport("go")
    for i, line in enumerate(lines):
        m = _GO_RESULT.match(line)
        if m:
            counts[m.group(1)] += 1
            if m.group(1) == "FAIL":
                message = _go_failure_message(lines, i)
                _add_failure(report.failures, m.group(2), message)
        elif _GO_PKG.match(line):
            seen_pkg = True
    if not seen_pkg and not any(counts.values()):
        return None
    # Without -v, go test prints only failures, so the pass count is unknown.
    verbose = any(ln.startswith("=== RUN") for ln in lines)
    report.passed = counts["PASS"] if counts["PASS"] or verbose else None
    report.failed = counts["FAIL"]
    report.skipped = counts["SKIP"]
    return report


_CARGO_RESULT = re.compile(r"^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored")
_CARGO_FAILED_TEST = re.compile(r"^test (\S+) \.\.\. FAILED$")
_CARGO_STDOUT = re.compile(r"^---- (\S+) stdout ----$")


def parse_cargo(lines: list[str]) -> TestReport | None:
    results = [m for ln in lines if (m := _CARGO_RESULT.match(ln.strip()))]
    if not results:
        return None
    report = TestReport(
        "cargo",
        passed=sum(int(m.group(1)) for m in results),
        failed=sum(int(m.group(2)) for m in results),
        skipped=sum(int(m.group(3)) for m in results),
    )
    messages: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = _CARGO_STDOUT.match(line.strip())
        if m:
            body = [ln.strip() for ln in lines[i + 1 : i + 6] if ln.strip()]
            if body and body[0].startswith("thread '") and len(body) > 1:
                body = [body[1]]
            messages[m.group(1)] = body[0] if body else ""
    for line in lines:
        m = _CARGO_FAILED_TEST.match(line.strip())
        if m:
            _add_failure(report.failures, m.group(1), messages.get(m.group(1), ""))
    return report


Parser = Callable[[list[str]], TestReport | None]
PARSERS: dict[str, Parser] = {
    "pytest": parse_pytest,
    "jest": parse_jest,
    "vitest": parse_vitest,
    "go": parse_go,
    "cargo": parse_cargo,
}


def guess_runner(argv: list[str]) -> str | None:
    """Best guess at the test runner from the command line (e.g. `uv run pytest`)."""
    names = [Path(a).name for a in argv]
    for i, name in enumerate(names):
        if name in ("pytest", "py.test") or (name == "-m" and names[i + 1 : i + 2] == ["pytest"]):
            return "pytest"
        if name in ("jest", "vitest"):
            return name
        if name == "go" and "test" in names[i + 1 : i + 2]:
            return "go"
        if name == "cargo" and any(n in ("test", "nextest") for n in names[i + 1 : i + 3]):
            return "cargo"
    return None


def parse_output(argv: list[str], lines: list[str]) -> TestReport:
    """Parse test results, trying the runner guessed from ``argv`` first."""
    hint = guess_runner(argv)
    order = [hint] if hint else []
    order += [name for name in PARSERS if name != hint]
    for name in order:
        try:
            report = PARSERS[name](lines)
        except Exception:  # pragma: no cover - a parser bug must not lose the run
            report = None
        if report is not None:
            return report
    return TestReport("unknown")


def unknown_output_failure(result: CommandResult) -> list[dict[str, str]]:
    """For unrecognized commands: the last lines of output, stored as one entry."""
    tail = "\n".join(result.tail).strip()
    return [{"test": "(output tail)", "message": tail}] if tail else []
