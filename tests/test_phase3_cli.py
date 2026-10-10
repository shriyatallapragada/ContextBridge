"""CLI tests for Phase 3: `cb handoff --dry-run`, the full `cb status`, and `cb search`.

No network: Ollama points at a closed port (see conftest) and LLMs are mocked.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from contextbridge.main import app
from contextbridge.synthesis import compressor
from contextbridge.synthesis.token_counter import count_tokens
from tests.helpers import commit_all, write

runner = CliRunner()

CART = "class Cart:\n    def total(self):\n        return sum(self.items)\n"


def cb(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


@pytest.fixture
def session_repo(git_repo: Path) -> Path:
    write(git_repo, "cart.py", CART)
    commit_all(git_repo, "add cart")
    assert cb("init", "--no-hooks")[0] == 0
    commit_all(git_repo, "ignore .context/")
    cb("hook", "post-commit")
    cb("log", "Prices are integer cents", "--tag", "architecture")
    cb("log", "Checkout 500s on an empty cart", "--tag", "blocker")
    cb("log", "Add discount codes", "--tag", "todo")
    write(git_repo, "cart.py", CART.replace("sum(self.items)", "sum(i.cents for i in self.items)"))
    commit_all(git_repo, "integer cents")
    cb("hook", "post-commit")
    write(git_repo, "discount.py", "def apply(code):\n    return code\n")  # untracked
    return git_repo


def _payload(output: str) -> str:
    start = output.index("# ContextBridge handoff")
    return output[start : output.index("next steps above.") + len("next steps above.")]


# ----- acceptance: dry run works with no Ollama ------------------------------------------


def test_acceptance_dry_run_without_ollama_uses_fallback(session_repo: Path) -> None:
    code, out = cb("handoff", "--dry-run", "--from", "claude", "--to", "cursor")
    assert code == 0, out
    assert "deterministic" in out and "Ollama is not reachable" in out
    for needle in (
        "Cart.total",
        "Prices are integer cents",
        "Checkout 500s on an empty cart",
        "Add discount codes",
        "discount.py",
        "integer cents",  # commit subject
    ):
        assert needle in out, needle
    # Raw vs compressed counts are both shown.
    assert "Raw context" in out and "Payload" in out and "Compression" in out
    assert "approx." in out
    # Nothing was written.
    assert not (session_repo / "CLAUDE.md").exists()
    assert (
        json.loads((session_repo / ".context" / "state.json").read_text())["last_handoff"] is None
    )


def test_acceptance_print_payload_respects_hard_cap(session_repo: Path) -> None:
    cfg_path = session_repo / ".context" / "config.json"
    cfg_path.write_text(json.dumps({"handoff": {"target_tokens": 150, "hard_cap_tokens": 200}}))
    # A big session: many files and long diffs.
    for i in range(40):
        write(
            session_repo,
            f"mod_{i}.py",
            "".join(f"def f{i}_{j}():\n    return {j}\n" for j in range(30)),
        )
    commit_all(session_repo, "bulk")
    cb("hook", "post-commit")
    result = runner.invoke(app, ["handoff", "--dry-run", "--print", "--no-llm"])
    assert result.exit_code == 0, result.output
    payload = result.stdout
    assert payload.startswith("# ContextBridge handoff")
    assert count_tokens(payload) <= 200
    assert "hard cap 200" in result.stderr


def test_handoff_uses_mocked_llm_and_reports_counts(
    session_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Fake:
        provider = "ollama"
        model = "fake"
        is_cloud = False

        def complete(self, system: str, user: str, *, max_tokens: int) -> str:
            calls.append(user)
            return "Here is the summary:\n- Cart.total sums integer cents\n- Blocker: empty cart"

    monkeypatch.setattr(compressor, "candidates", lambda cfg, no_llm=False: [Fake()])
    result = runner.invoke(app, ["handoff", "--dry-run", "--print"])
    assert result.exit_code == 0, result.output
    assert "- Cart.total sums integer cents" in result.stdout
    assert "Here is the summary" not in result.stdout  # preamble stripped
    assert "Method" in result.stderr and "ollama" in result.stderr
    assert "Prices are integer cents" in calls[0]


def test_cloud_fallback_prints_notice(session_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Cloud:
        provider = "groq"
        model = "llama"
        is_cloud = True

        def complete(self, system: str, user: str, *, max_tokens: int) -> str:
            return "- cloud summary"

    monkeypatch.setattr(compressor, "candidates", lambda cfg, no_llm=False: [Cloud()])
    result = runner.invoke(app, ["handoff", "--dry-run", "--print"])
    assert result.exit_code == 0
    assert "Sending sanitized context to groq" in result.stderr


def test_handoff_without_dry_run_needs_from_and_to(session_repo: Path) -> None:
    code, out = cb("handoff")
    assert code == 1
    assert "--from and --to" in out
    assert cb("handoff", "--to", "cursor")[0] == 1
    assert not (session_repo / ".cursor").exists()


def test_handoff_secrets_never_reach_payload(session_repo: Path) -> None:
    cb("log", "staging key is AKIAIOSFODNN7EXAMPLE", "--tag", "blocker")
    write(session_repo, "config.py", 'API_TOKEN = "ghp_' + "Z9y8X7w6V5" * 4 + '"\n')
    result = runner.invoke(app, ["handoff", "--dry-run", "--print", "--no-llm"])
    assert "AKIAIOSFODNN7EXAMPLE" not in result.stdout
    assert "ghp_" not in result.stdout


def test_handoff_requires_init(git_repo: Path) -> None:
    code, _ = cb("handoff", "--dry-run")
    assert code == 2


# ----- status ----------------------------------------------------------------------------


def test_status_dashboard(session_repo: Path) -> None:
    cb("run", "--", sys.executable, "-c", "import sys; sys.exit(0)")
    code, out = cb("status")
    assert code == 0, out
    for needle in (
        "Branch",
        "main",
        "Commits",
        "Changed files",
        "cart.py",
        "Cart.total",
        "discount.py",
        "uncommitted",
        "Last test run",
        "exited 0",
        "Context size",
        "tokens raw",
        "Recent decisions",
        "Checkout 500s",
    ):
        assert needle in out, needle


def test_status_shows_failing_tests(session_repo: Path) -> None:
    write(session_repo, "tests/test_x.py", "def test_bad():\n    assert 0, 'boom'\n")
    cb("run", "--", sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests")
    code, out = cb("status")
    assert code == 0
    assert "Failing tests" in out and "test_bad" in out


def test_status_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cb("init")[0] == 0
    cb("log", "plain directory note")
    code, out = cb("status")
    assert code == 0, out
    assert "plain directory note" in out


# ----- search ----------------------------------------------------------------------------


def test_keyword_search(session_repo: Path) -> None:
    result = runner.invoke(app, ["search", "cents"])
    assert result.exit_code == 0
    assert "Keyword search" in result.stderr
    assert "Prices are integer cents" in result.stdout
    assert "cart.py" in result.stdout  # the diff snippet matched too


def test_keyword_search_escapes_wildcards(session_repo: Path) -> None:
    cb("log", "coverage is 100% now")
    assert "coverage" in runner.invoke(app, ["search", "100%"]).stdout
    assert "No results" in runner.invoke(app, ["search", "zz%zz"]).stdout


def test_vector_enabled_but_unavailable_falls_back(
    session_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextbridge.cli.search_cmd as search_cmd

    (session_repo / ".context" / "config.json").write_text(
        json.dumps({"vector": {"enabled": True}})
    )

    def broken(ws: Any, query: str, limit: int) -> Any:
        from contextbridge.storage.vector_store import VectorUnavailableError

        raise VectorUnavailableError("chromadb is not installed")

    monkeypatch.setattr(search_cmd, "_semantic", broken)
    result = runner.invoke(app, ["search", "cents"])
    assert result.exit_code == 0
    assert "Semantic search unavailable" in result.stderr
    assert "Prices are integer cents" in result.stdout


def _hash_embedding() -> Any:
    """Deterministic, offline stand-in for Chroma's ONNX embedder (bag of words)."""
    from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

    class HashEmbedding(EmbeddingFunction[Documents]):
        def __init__(self) -> None:
            pass

        def __call__(self, input: Documents) -> Embeddings:
            vectors = []
            for doc in input:
                vec = [0.0] * 64
                for word in doc.lower().split():
                    vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
                vectors.append(vec)
            return vectors  # type: ignore[return-value]

        @staticmethod
        def name() -> str:
            return "hash"

        def get_config(self) -> dict[str, Any]:
            return {}

        @staticmethod
        def build_from_config(config: dict[str, Any]) -> HashEmbedding:
            return HashEmbedding()

    return HashEmbedding()


def test_semantic_search_with_vector_store(
    session_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("chromadb")
    import contextbridge.storage.vector_store as vs

    real_init = vs.VectorStore.__init__

    def init(self: Any, path: Path, *, embedding_function: Any = None) -> None:
        real_init(self, path, embedding_function=_hash_embedding())

    monkeypatch.setattr(vs.VectorStore, "__init__", init)
    (session_repo / ".context" / "config.json").write_text(
        json.dumps({"vector": {"enabled": True}})
    )
    result = runner.invoke(app, ["search", "empty cart checkout"])
    assert result.exit_code == 0, result.output
    assert "Semantic results" in result.stdout
    first_row = next(ln for ln in result.stdout.splitlines() if "decision" in ln)
    assert "Checkout 500s" in first_row
    assert (session_repo / ".context" / "vector_db").is_dir()

    # Lazy, incremental indexing: only new rows are embedded on the next search.
    from contextbridge.storage.workspace import Workspace

    ws = Workspace.locate(session_repo)
    store = vs.VectorStore(ws.context_dir / "vector_db")
    with ws.open_db() as db:
        assert store.sync(db) == 0
        db.add_decision(db.find_active_session("main").id, "new idea", "note")  # type: ignore[union-attr]
        assert store.sync(db) == 1
