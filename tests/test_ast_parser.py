from __future__ import annotations

import difflib

import pytest

from contextbridge.core.ast_parser import (
    detect_language,
    extract_symbols,
    parse_hunks,
    symbols_for_change,
    symbols_from_hunk_context,
)


def udiff(old: str, new: str, path: str = "f") -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{path}", f"b/{path}"
    )
    return "".join(lines)


def changes(path: str, old: str, new: str) -> list[tuple[str, str, str]]:
    result = symbols_for_change(path, old, new, udiff(old, new, path))
    return [(s["name"], s["kind"], s["change"]) for s in result]


# ----- diff parsing ------------------------------------------------------------------


def test_parse_hunks_line_numbers() -> None:
    patch = (
        "--- a/x\n+++ b/x\n"
        "@@ -1,4 +1,4 @@ def top():\n a\n-b\n+B\n c\n d\n"
        "@@ -10,2 +10,3 @@\n x\n+y\n z\n\\ No newline at end of file\n"
    )
    first, second = parse_hunks(patch)
    assert (first.removed, first.added, first.context) == ([2], [2], "def top():")
    assert (second.removed, second.added) == ([], [11])


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("a.py", "python"),
        ("a.js", "javascript"),
        ("a.jsx", "javascript"),
        ("a.ts", "typescript"),
        ("a.tsx", "tsx"),
        ("a.go", "go"),
        ("a.rs", "rust"),
        ("a.rb", None),
        ("Makefile", None),
    ],
)
def test_detect_language(path: str, lang: str | None) -> None:
    assert detect_language(path) == lang


# ----- Python ----------------------------------------------------------------------------

PY_OLD = """\
import os


def alpha(x):
    return x + 1


def beta(y):
    return y * 2


class Cart:
    def __init__(self):
        self.items = []

    @property
    def total(self):
        return sum(self.items)

    def add(self, item):
        self.items.append(item)
"""


def test_python_extract_symbols() -> None:
    syms = {s.name: s for s in extract_symbols(PY_OLD, "python")}
    assert set(syms) == {"alpha", "beta", "Cart", "Cart.__init__", "Cart.total", "Cart.add"}
    assert syms["Cart.total"].kind == "method"
    assert syms["Cart"].kind == "class"
    assert syms["alpha"].signature == "def alpha(x)"
    assert syms["Cart.total"].start_line == 16  # decorator line included


def test_python_edit_one_function_reports_only_it() -> None:
    new = PY_OLD.replace("return x + 1", "return x + 100")
    assert changes("m.py", PY_OLD, new) == [("alpha", "function", "modified")]


def test_python_edit_method_reports_method_not_class() -> None:
    new = PY_OLD.replace("return sum(self.items)", "return sum(i.price for i in self.items)")
    assert changes("m.py", PY_OLD, new) == [("Cart.total", "method", "modified")]


def test_python_decorator_change_counts_as_method_change() -> None:
    new = PY_OLD.replace("    @property\n", "    @cached_property\n")
    assert changes("m.py", PY_OLD, new) == [("Cart.total", "method", "modified")]


def test_python_added_and_removed() -> None:
    new = PY_OLD.replace("def beta(y):\n    return y * 2\n", "def gamma(z):\n    return z - 1\n")
    assert changes("m.py", PY_OLD, new) == [
        ("gamma", "function", "added"),
        ("beta", "function", "removed"),
    ]


def test_python_new_class_reported_once() -> None:
    new = PY_OLD + "\n\nclass Shop:\n    def open(self):\n        pass\n"
    assert changes("m.py", PY_OLD, new) == [("Shop", "class", "added")]


def test_python_shifted_but_unchanged_functions_not_reported() -> None:
    new = PY_OLD.replace("import os\n", "import os\nimport sys\nimport re\n")
    assert changes("m.py", PY_OLD, new) == []


def test_added_and_deleted_files_list_top_level_symbols() -> None:
    added = symbols_for_change("m.py", None, PY_OLD, udiff("", PY_OLD))
    assert [(s["name"], s["change"]) for s in added] == [
        ("alpha", "added"),
        ("beta", "added"),
        ("Cart", "added"),
    ]
    deleted = symbols_for_change("m.py", PY_OLD, None, udiff(PY_OLD, ""))
    assert {s["change"] for s in deleted} == {"removed"}


# ----- TypeScript / JavaScript --------------------------------------------------------------

TS_OLD = """\
import { x } from "./x";

export function add(a: number, b: number): number {
  return a + b;
}

export const double = (n: number) => n * 2;

export class Calculator {
  private total = 0;

  push(n: number): void {
    this.total += n;
  }

  reset = () => {
    this.total = 0;
  };
}

interface Shape {
  area(): number;
}

export type Id = string;
"""


def test_typescript_extract_symbols() -> None:
    syms = {s.name: s.kind for s in extract_symbols(TS_OLD, "typescript")}
    assert syms == {
        "add": "function",
        "double": "function",
        "Calculator": "class",
        "Calculator.push": "method",
        "Calculator.reset": "method",
        "Shape": "class",
        "Id": "export",
    }


def test_typescript_edit_one_method() -> None:
    new = TS_OLD.replace("this.total += n;", "this.total += n * 1;")
    assert changes("calc.ts", TS_OLD, new) == [("Calculator.push", "method", "modified")]


def test_typescript_edit_arrow_function() -> None:
    new = TS_OLD.replace("n * 2", "n + n")
    result = symbols_for_change("calc.ts", TS_OLD, new, udiff(TS_OLD, new))
    assert result == [
        {
            "name": "double",
            "kind": "function",
            "change": "modified",
            "signature": "const double = (n: number) => n + n;",
        }
    ]


def test_tsx_component() -> None:
    old = "export function Button(props: Props) {\n  return <button>{props.label}</button>;\n}\n"
    new = old.replace("{props.label}", "{props.label.toUpperCase()}")
    assert changes("Button.tsx", old, new) == [("Button", "function", "modified")]


def test_javascript_class_and_default_export() -> None:
    old = (
        "class Store {\n  get(k) {\n    return this.m[k];\n  }\n}\n\n"
        "export default function () {\n  return 1;\n}\n"
    )
    new = old.replace("return 1;", "return 2;")
    assert changes("s.js", old, new) == [("default", "export", "modified")]
    new2 = old.replace("return this.m[k];", "return this.m.get(k);")
    assert changes("s.js", old, new2) == [("Store.get", "method", "modified")]


# ----- Go ---------------------------------------------------------------------------------

GO_OLD = """\
package server

type Server struct {
\tport int
}

func (s *Server) Start() error {
\treturn nil
}

func (s *Server) Stop() {
}

func New(port int) *Server {
\treturn &Server{port: port}
}
"""


def test_go_symbols_and_method_edit() -> None:
    syms = {s.name: s.kind for s in extract_symbols(GO_OLD, "go")}
    assert syms == {
        "Server": "class",
        "Server.Start": "method",
        "Server.Stop": "method",
        "New": "function",
    }
    new = GO_OLD.replace("\treturn nil", "\treturn listen(s.port)")
    assert changes("server.go", GO_OLD, new) == [("Server.Start", "method", "modified")]


def test_go_struct_field_change() -> None:
    new = GO_OLD.replace("\tport int\n", "\tport int\n\thost string\n")
    assert changes("server.go", GO_OLD, new) == [("Server", "class", "modified")]


# ----- Rust -------------------------------------------------------------------------------

RS_OLD = """\
pub struct Config {
    pub name: String,
}

impl Config {
    pub fn new(name: &str) -> Self {
        Config { name: name.to_string() }
    }

    pub fn shout(&self) -> String {
        self.name.to_uppercase()
    }
}

pub fn helper() -> u32 {
    1
}

mod util {
    pub fn inner() {}
}
"""


def test_rust_symbols_and_method_edit() -> None:
    syms = {s.name: s.kind for s in extract_symbols(RS_OLD, "rust")}
    assert syms == {
        "Config": "class",
        "Config::new": "method",
        "Config::shout": "method",
        "helper": "function",
        "util::inner": "function",
    }
    new = RS_OLD.replace("self.name.to_uppercase()", 'format!("{}!", self.name.to_uppercase())')
    assert changes("lib.rs", RS_OLD, new) == [("Config::shout", "method", "modified")]


# ----- fallbacks ------------------------------------------------------------------------------


def test_unsupported_language_uses_hunk_context() -> None:
    patch = "--- a/x.rb\n+++ b/x.rb\n@@ -10,3 +10,3 @@ def charge(amount)\n a\n-b\n+c\n"
    result = symbols_for_change("x.rb", "old", "new", patch)
    assert result == [
        {
            "name": "charge",
            "kind": "function",
            "change": "modified",
            "signature": "def charge(amount)",
        }
    ]


def test_hunk_context_kinds() -> None:
    hunks = parse_hunks(
        "@@ -1 +1 @@ class Billing:\n-a\n+b\n"
        "@@ -9 +9 @@ func (s *Server) Run() {\n-a\n+b\n"
        "@@ -20 +20 @@\n-a\n+b\n"
    )
    assert [(s["name"], s["kind"]) for s in symbols_from_hunk_context(hunks)] == [
        ("Billing", "class"),
        ("Run", "function"),
    ]


def test_no_context_falls_back_to_file_level() -> None:
    assert symbols_for_change("notes.txt", "a\n", "b\n", udiff("a\n", "b\n")) == []


def test_parser_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import contextbridge.core.ast_parser as ap

    def boom(source: str, language: str) -> list[ap.Symbol]:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(ap, "extract_symbols", boom)
    patch = "@@ -1,2 +1,2 @@ def alpha(x):\n-  a\n+  b\n"
    result = symbols_for_change("m.py", "x", "y", patch)
    assert [(s["name"], s["change"]) for s in result] == [("alpha", "modified")]


def test_syntax_errors_never_crash() -> None:
    broken = "def ok():\n    return 1\n\ndef broken(:\n    pass\n"
    fixed = broken.replace("return 1", "return 2")
    result = symbols_for_change("m.py", broken, fixed, udiff(broken, fixed))
    assert ("ok", "modified") in [(s["name"], s["change"]) for s in result]


# ----- review regressions -----------------------------------------------------------------

PY_PROPERTY = """\
class Cart:
    @property
    def total(self):
        return self._t

    @total.setter
    def total(self, v):
        self._t = v
"""


def test_setter_edit_is_reported_despite_shared_name() -> None:
    new = PY_PROPERTY.replace("self._t = v", "self._t = max(v, 0)")
    result = symbols_for_change("m.py", PY_PROPERTY, new, udiff(PY_PROPERTY, new))
    assert [(s["name"], s["change"], s["signature"]) for s in result] == [
        ("Cart.total", "modified", "def total(self, v)")
    ]


def test_js_accessor_pair() -> None:
    old = "class A {\n  get x() { return this._x; }\n  set x(v) { this._x = v; }\n}\n"
    new = old.replace("this._x = v", "this._x = v | 0")
    assert changes("a.js", old, new) == [("A.x", "method", "modified")]


def test_new_overload_counts_as_added() -> None:
    old = "def f(x):\n    return x\n"
    new = old + "\n\ndef f(x, y):\n    return x + y\n"
    assert changes("m.py", old, new) == [("f", "function", "added")]


SRC = "def f():\n    return 1\n\ndef g():\n    return 2\n"


@pytest.mark.parametrize("missing", ["old", "new"])
def test_modified_file_with_unavailable_source_is_not_added_or_removed(missing: str) -> None:
    new = SRC.replace("return 1", "return 3")
    patch = udiff(SRC, new).replace("@@ -1,5 +1,5 @@", "@@ -1,5 +1,5 @@ def f():")
    old_src, new_src = (None, new) if missing == "old" else (SRC, None)
    result = symbols_for_change("m.py", old_src, new_src, patch, change_type="modified")
    assert [(s["name"], s["change"]) for s in result] == [("f", "modified")]


def test_explicit_change_type_for_added_and_deleted() -> None:
    added = symbols_for_change("m.py", None, SRC, udiff("", SRC), change_type="added")
    assert {s["change"] for s in added} == {"added"}
    deleted = symbols_for_change("m.py", SRC, None, udiff(SRC, ""), change_type="deleted")
    assert {s["change"] for s in deleted} == {"removed"}
