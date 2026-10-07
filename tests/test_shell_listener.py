# ruff: noqa: RUF001  (sample jest output contains non-ASCII arrows verbatim)
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from contextbridge.core.shell_listener import (
    EXIT_NOT_FOUND,
    TAIL_LINES,
    CommandResult,
    guess_runner,
    parse_cargo,
    parse_go,
    parse_jest,
    parse_output,
    parse_pytest,
    parse_vitest,
    run_command,
    strip_ansi,
    unknown_output_failure,
)

PYTEST_Q = """\
..F.s                                                                    [100%]
=================================== FAILURES ===================================
____________________________________ test_b ____________________________________

    def test_b():
>       assert 1 == 2
E       assert 1 == 2

tests/test_x.py:5: AssertionError
=========================== short test summary info ============================
FAILED tests/test_x.py::test_b - assert 1 == 2
1 failed, 3 passed, 1 skipped in 0.05s
"""

PYTEST_VERBOSE = """\
tests/test_x.py::test_a PASSED
tests/test_x.py::test_c ERROR
=========================== short test summary info ============================
ERROR tests/test_x.py::test_c - fixture 'db' not found
============== 1 passed, 1 error, 2 xfailed, 3 warnings in 0.12s ===============
"""

JEST = """\
 FAIL  src/sum.test.js
  math
    ✓ subtracts (2 ms)
    ✕ adds numbers (3 ms)

  ● math › adds numbers

    expect(received).toBe(expected) // Object.is equality

Test Suites: 1 failed, 1 total
Tests:       1 failed, 1 skipped, 4 passed, 6 total
Snapshots:   0 total
Time:        0.5 s
"""

VITEST = """\
 FAIL  src/sum.test.ts > math > adds numbers
AssertionError: expected 3 to be 4 // Object.is equality

 Test Files  1 failed | 2 passed (3)
      Tests  1 failed | 7 passed | 1 skipped (9)
   Start at  10:00:00
"""

GO_VERBOSE = """\
=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSub
    math_test.go:12: expected 1, got 2
--- FAIL: TestSub (0.00s)
=== RUN   TestSkip
    math_test.go:20: skipping
--- SKIP: TestSkip (0.00s)
FAIL
FAIL\texample.com/math\t0.002s
FAIL
"""

GO_QUIET_PASS = "ok  \texample.com/math\t0.002s\n"

GO_QUIET_FAIL = """\
--- FAIL: TestSub (0.00s)
    math_test.go:12: expected 1, got 2
FAIL
FAIL\texample.com/math\t0.002s
"""

CARGO = """\
running 3 tests
test tests::adds ... ok
test tests::fails ... FAILED
test tests::ignored ... ignored

failures:

---- tests::fails stdout ----
thread 'tests::fails' panicked at src/lib.rs:12:9:
assertion `left == right` failed
  left: 1
 right: 2

failures:
    tests::fails

test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s

running 2 tests
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""


def L(text: str) -> list[str]:
    return text.splitlines()


def test_pytest_quiet() -> None:
    r = parse_pytest(L(PYTEST_Q))
    assert r is not None
    assert (r.runner, r.passed, r.failed, r.skipped) == ("pytest", 3, 1, 1)
    assert r.failures == [{"test": "tests/test_x.py::test_b", "message": "assert 1 == 2"}]


def test_pytest_verbose_errors_and_xfail() -> None:
    r = parse_pytest(L(PYTEST_VERBOSE))
    assert r is not None
    assert (r.passed, r.failed, r.skipped) == (1, 1, 2)
    assert r.failures[0]["test"] == "tests/test_x.py::test_c"


def test_pytest_no_tests() -> None:
    r = parse_pytest(["", "no tests ran in 0.01s"])
    assert r is not None and (r.passed, r.failed) == (0, 0)


def test_jest() -> None:
    r = parse_jest(L(JEST))
    assert r is not None
    assert (r.runner, r.passed, r.failed, r.skipped) == ("jest", 4, 1, 1)
    assert r.failures == [
        {
            "test": "math › adds numbers",
            "message": "expect(received).toBe(expected) // Object.is equality",
        }
    ]


def test_vitest() -> None:
    r = parse_vitest(L(VITEST))
    assert r is not None
    assert (r.runner, r.passed, r.failed, r.skipped) == ("vitest", 7, 1, 1)
    assert r.failures[0]["test"] == "src/sum.test.ts > math > adds numbers"
    assert r.failures[0]["message"].startswith("AssertionError: expected 3 to be 4")


def test_go_verbose() -> None:
    r = parse_go(L(GO_VERBOSE))
    assert r is not None
    assert (r.passed, r.failed, r.skipped) == (1, 1, 1)
    assert r.failures == [{"test": "TestSub", "message": "math_test.go:12: expected 1, got 2"}]


def test_go_quiet() -> None:
    passing = parse_go(L(GO_QUIET_PASS))
    assert passing is not None and passing.failed == 0
    assert passing.passed is None  # unknown without -v; never invented
    failing = parse_go(L(GO_QUIET_FAIL))
    assert failing is not None and failing.failed == 1
    assert failing.failures[0]["message"] == "math_test.go:12: expected 1, got 2"


def test_cargo_sums_multiple_binaries() -> None:
    r = parse_cargo(L(CARGO))
    assert r is not None
    assert (r.passed, r.failed, r.skipped) == (3, 1, 1)
    assert r.failures == [{"test": "tests::fails", "message": "assertion `left == right` failed"}]


@pytest.mark.parametrize(
    ("argv", "runner"),
    [
        (["pytest", "-q"], "pytest"),
        (["uv", "run", "pytest"], "pytest"),
        (["python", "-m", "pytest"], "pytest"),
        (["npx", "jest"], "jest"),
        (["npx", "vitest", "run"], "vitest"),
        (["go", "test", "./..."], "go"),
        (["cargo", "test"], "cargo"),
        (["make", "test"], None),
    ],
)
def test_guess_runner(argv: list[str], runner: str | None) -> None:
    assert guess_runner(argv) == runner


@pytest.mark.parametrize(
    ("output", "runner"),
    [
        (PYTEST_Q, "pytest"),
        (JEST, "jest"),
        (VITEST, "vitest"),
        (GO_VERBOSE, "go"),
        (CARGO, "cargo"),
    ],
)
def test_parse_output_sniffs_runner_from_output(output: str, runner: str) -> None:
    # e.g. `npm test` or `make test`: the runner is only visible in the output.
    assert parse_output(["make", "test"], L(output)).runner == runner


def test_unknown_command_keeps_only_tail() -> None:
    lines = [f"line {i}" for i in range(50)]
    report = parse_output(["./build.sh"], lines)
    assert not report.recognized
    assert report.passed is None and report.failed is None
    result = CommandResult(argv=["./build.sh"], exit_code=2, output=lines)
    [entry] = unknown_output_failure(result)
    kept = entry["message"].splitlines()
    assert len(kept) == TAIL_LINES and kept[-1] == "line 49" and kept[0] == "line 30"


def test_strip_ansi() -> None:
    assert strip_ansi("\x1b[31mFAILED\x1b[0m x") == "FAILED x"


# ----- running ------------------------------------------------------------------------


class _Capture(io.TextIOWrapper):
    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="utf-8")

    def value(self) -> bytes:
        self.flush()
        buf = self.buffer
        assert isinstance(buf, io.BytesIO)
        return buf.getvalue()


def test_run_command_streams_unchanged_and_captures() -> None:
    out, err = _Capture(), _Capture()
    script = "import sys; print('to-out'); print('to-err', file=sys.stderr); sys.exit(3)"
    result = run_command([sys.executable, "-c", script], stdout=out, stderr=err)
    assert result.exit_code == 3
    assert out.value() == b"to-out\n"
    assert err.value() == b"to-err\n"
    assert sorted(result.output) == ["to-err", "to-out"]


def test_run_command_not_found(tmp_path: Path) -> None:
    err = _Capture()
    result = run_command(["definitely-not-a-command-xyz"], stdout=_Capture(), stderr=err)
    assert result.exit_code == EXIT_NOT_FOUND
    assert b"command not found" in err.value()
