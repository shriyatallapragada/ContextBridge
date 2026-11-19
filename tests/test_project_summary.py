"""Offline analysis, grounded chunk summaries, bounded retries and persistent reuse."""

import json
from dataclasses import dataclass, field

import pytest
from typer.testing import CliRunner

from contextbridge.config import Config
from contextbridge.main import app
from contextbridge.providers.base import ProviderRateLimited, ProviderUnavailable
from contextbridge.storage.db import Database
from contextbridge.synthesis.project_summary import ProjectSummarizer, chunks
from contextbridge.synthesis.token_counter import count_tokens


@dataclass
class FakeProvider:
    provider: str = "openai-compatible"
    model: str = "openai/gpt-oss-120b"
    endpoint: str = "https://example.test/v1"
    is_cloud: bool = True
    responses: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def generate(self, prompt, schema=None):
        self.calls.append((prompt, schema))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            if response is not None:
                return response
        data = json.loads(prompt)
        return json.dumps(
            {
                "purpose": data["purpose_options"][0],
                "key_symbols": data["key_symbols"],
                "depends_on": data["depends_on"],
            }
        )


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text(
        "# Lab helper\n\nCreates short Flutter lab observations and code examples.\n"
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name="lab-helper"\n'
        'description="Builds Flutter lab reports."\n'
        'dependencies=["rich>=13"]\n'
        "[project.optional-dependencies]\n"
        'gui=["PySide6>=6"]\n'
        "[project.scripts]\n"
        'lab="runner:main"\n'
    )
    (root / "runner.py").write_text(
        '"""Create a lab report from exercises."""\n'
        "from core.report import Report\n"
        "def main():\n"
        "    return Report()\n"
    )
    (root / "core").mkdir()
    (root / "core/report.py").write_text(
        '"""Store and render lab observations."""\n'
        "class Report:\n"
        "    def render(self):\n"
        '        return "report"\n'
    )
    return root


def analyze(root, db, provider=None, **kwargs):
    cfg = kwargs.pop("config", Config())
    return ProjectSummarizer(
        root,
        db,
        cfg,
        provider=provider,
        sleep=kwargs.pop("sleep", lambda seconds: None),
        notify=kwargs.pop("notify", None),
    ).summarize(**kwargs)


def test_offline_summary_without_logs_uses_readme_manifests_symbols_and_entrypoints(
    project, tmp_path
):
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, no_llm=True)
        assert result.goal == "Builds Flutter lab reports."
        assert "rich" in result.dependencies and "PySide6" in result.dependencies
        assert "runner.py" in result.entrypoints
        assert result.languages == {"python": 2}
        assert result.files[0].path == "runner.py"
        assert "Create a lab report" in result.files[0].purpose
        assert result.files[0].key_symbols == ["main"]
        assert result.files[0].depends_on == ["core.report"]
        assert result.requests == 0
        rendered = result.render(600)
        assert count_tokens(rendered) <= 600
        assert "What each part does" in rendered and "name-based" not in rendered
        assert "No git history available" in rendered
        rows = db.file_summaries()
        assert any(r["path"] == "runner.py" and len(r["content_hash"]) == 64 for r in rows)


def test_no_llm_overrides_even_an_injected_provider(project, tmp_path):
    provider = FakeProvider()
    with Database.open(tmp_path / "cache.db") as db:
        analyze(project, db, provider, no_llm=True)
    assert provider.calls == []


def test_chunks_and_prompts_are_bounded_and_merged_locally(project, tmp_path):
    source = (project / "runner.py").read_text() + "\n# explanatory comment for this report\n" * 60
    (project / "runner.py").write_text(source)
    cfg = Config()
    cfg.summary.chunk_chars = 512
    provider = FakeProvider()
    notices = []
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider, config=cfg, notify=notices.append)
    assert result.requests <= cfg.summary.max_requests
    payloads = [json.loads(p) for p, _ in provider.calls]
    assert all(len(p["code"]) <= 512 for p in payloads)
    assert all(count_tokens(p) <= 2500 for p, _ in provider.calls)
    assert len([p for p in payloads if p["path"] == "runner.py"]) > 1
    assert all(p["path"] in {"runner.py", "core/report.py"} for p in payloads)
    assert sum("hosted provider" in n for n in notices) == 1
    assert notices.index(next(n for n in notices if "hosted provider" in n)) < notices.index(
        "Model request 1/12"
    )
    assert all(f.method == "model" for f in result.files if f.language == "python")
    assert all(len(part) <= 512 for part in chunks("x" * 4000, 512))


def test_cache_reuses_unchanged_files_and_invalidates_changed_file(project, tmp_path):
    with Database.open(tmp_path / "cache.db") as db:
        first = FakeProvider()
        analyze(project, db, first)
        assert len(first.calls) == 2
        second = FakeProvider()
        result = analyze(project, db, second)
        assert not second.calls and result.cache_hits >= 2
        (project / "core/report.py").write_text(
            '"""Render reports as Markdown."""\nclass Report: pass\n'
        )
        third = FakeProvider()
        analyze(project, db, third)
        assert [json.loads(p)["path"] for p, _ in third.calls] == ["core/report.py"]
        refreshed = FakeProvider()
        analyze(project, db, refreshed, refresh=True)
        assert len(refreshed.calls) == 2
        different_model = FakeProvider(model="other-model")
        analyze(project, db, different_model)
        assert len(different_model.calls) == 2


def test_invalid_json_retries_once_and_success_is_cached(project, tmp_path):
    provider = FakeProvider(responses=["invalid JSON", None])
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider)
    assert len(provider.calls) == 3
    assert result.files[0].method == "model"


@pytest.mark.parametrize(
    "bad",
    [
        '{"purpose":"Invented payment gateway.","key_symbols":[],"depends_on":[]}',
        '{"purpose":"Create a lab report from exercises.",'
        '"key_symbols":["invented_function"],"depends_on":[]}',
    ],
)
def test_ungrounded_model_facts_are_rejected(project, tmp_path, bad):
    provider = FakeProvider(responses=[bad, bad])
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider)
    assert result.files[0].method == "offline"
    assert "Invented" not in result.render(1000)
    assert any("ungrounded" in message for message in result.warnings)


def test_rate_limit_backoff_stops_and_falls_back(project, tmp_path):
    provider = FakeProvider(responses=[ProviderRateLimited("rate", 2)] * 4)
    delays = []
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider, sleep=delays.append)
    assert len(provider.calls) == 3
    assert 2 in delays
    assert all(f.method == "offline" for f in result.files)
    assert any("Rate limit" in w for w in result.warnings)


def test_long_retry_after_is_not_ignored(project, tmp_path):
    provider = FakeProvider(responses=[ProviderRateLimited("rate", 600)])
    delays = []
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider, sleep=delays.append)
    assert len(provider.calls) == 1 and delays == []
    assert result.warnings


def test_transport_failure_falls_back_without_repeated_requests(project, tmp_path):
    provider = FakeProvider(responses=[ProviderUnavailable("no connection")])
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider)
    assert len(provider.calls) == 1 and all(f.method == "offline" for f in result.files)


def test_request_bound_and_partial_chunk_cache_resume(project, tmp_path):
    (project / "runner.py").write_text(
        (project / "runner.py").read_text() + "# more code details\n" * 70
    )
    cfg = Config()
    cfg.summary.max_requests = 2
    cfg.summary.chunk_chars = 512
    with Database.open(tmp_path / "cache.db") as db:
        first = FakeProvider()
        result = analyze(project, db, first, config=cfg)
        assert result.requests == 2
        sent = [json.loads(p)["chunk"] for p, _ in first.calls]
        second = FakeProvider()
        analyze(project, db, second, config=cfg)
        assert len(second.calls) <= 2
        assert json.loads(second.calls[0][0])["chunk"] > max(sent)


def test_secret_and_ignored_files_never_enter_prompts_or_cache(project, tmp_path):
    secret = "AKIAIOSFODNN7EXAMPLE"
    for filename in (".env", "private.pem", "credentials.json"):
        (project / filename).write_text(secret)
    (project / "ignored").mkdir()
    (project / "ignored/evil.py").write_text("EVIL_SECRET_SOURCE")
    (project / ".gitignore").write_text("ignored/\n")
    (project / "core/.gitignore").write_text("private.py\n")
    (project / "core/private.py").write_text("NESTED_PRIVATE_SOURCE")
    (project / "outside.py").symlink_to(tmp_path / "private.py")
    (tmp_path / "private.py").write_text("OUTSIDE_SECRET_SOURCE")
    with (project / "runner.py").open("a") as stream:
        stream.write(f'\napi_key = "{secret}"\n')
    provider = FakeProvider()
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider)
        stored = str(db.file_summaries())
    prompts = " ".join(p for p, _ in provider.calls)
    assert secret not in prompts + stored
    assert "EVIL_SECRET_SOURCE" not in prompts and "NESTED_PRIVATE_SOURCE" not in prompts
    assert "OUTSIDE_SECRET_SOURCE" not in prompts
    assert all(".env" not in path and "ignored/" not in path for path in result.tree)
    assert "REDACTED" in prompts


def test_summarize_cli_works_without_init_or_logged_notes(git_repo):
    (git_repo / "main.py").write_text('"""Run the fresh report builder."""\ndef run(): pass\n')
    runner = CliRunner()
    result = runner.invoke(app, ["summarize", "--no-llm", "--budget", "600"])
    assert result.exit_code == 0, result.output
    assert "Run the fresh report builder" in result.stdout
    assert (git_repo / ".contextbridge/state.db").is_file()
    handoff = runner.invoke(app, ["handoff", "--to", "codex", "--no-llm", "--dry-run"])
    assert handoff.exit_code == 0, handoff.output
    assert "What each part does" in handoff.output and "main.py" in handoff.output
    assert "name-based description" not in handoff.output


def test_offline_cache_persists_and_deleted_paths_are_pruned(project, tmp_path):
    cache = tmp_path / "cache.db"
    with Database.open(cache) as db:
        analyze(project, db, no_llm=True)
    with Database.open(cache) as db:
        second = analyze(project, db, no_llm=True)
        assert second.cache_hits == len(second.files)
        (project / "core/report.py").unlink()
        analyze(project, db, no_llm=True)
        assert not any(row["path"] == "core/report.py" for row in db.file_summaries())


def test_local_ast_extraction_survives_secret_redaction_that_changes_syntax(project, tmp_path):
    (project / "runner.py").write_text(
        '"""Run the protected project summary."""\n'
        'API_KEY = "AKIAIOSFODNN7EXAMPLE"\ndef launch(): pass\n'
    )
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, no_llm=True)
    entry = next(f for f in result.files if f.path == "runner.py")
    assert entry.purpose == "Run the protected project summary."
    assert "launch" in entry.key_symbols


def test_request_interval_and_transient_rate_limit_are_respected(project, tmp_path):
    cfg = Config()
    cfg.summary.request_interval = 0.5
    provider = FakeProvider(responses=[ProviderRateLimited("retry", 1), None])
    delays = []
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider, config=cfg, sleep=delays.append)
    assert result.requests == 3 and 1 in delays
    assert any(0 < delay <= 0.5 for delay in delays)
    assert not result.warnings


def test_js_dependencies_entrypoint_and_tree_sitter_symbols(tmp_path):
    root = tmp_path / "js-project"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps(
            {
                "description": "Creates a calendar service.",
                "main": "server.js",
                "dependencies": {"express": "1"},
            }
        )
    )
    (root / "server.js").write_text(
        'import express from "express";\nexport function createApp() { return express(); }'
    )
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(root, db, no_llm=True)
    assert result.goal == "Creates a calendar service."
    assert result.entrypoints == ["server.js"] and result.dependencies == ["express"]
    assert "createApp" in result.files[0].key_symbols


def test_model_analysis_only_selects_eight_ranked_source_files(project, tmp_path):
    for i in range(15):
        (project / f"helper_{i}.py").write_text(f"def helper_{i}(): return {i}\n")
    provider = FakeProvider()
    with Database.open(tmp_path / "cache.db") as db:
        analyze(project, db, provider)
    assert len(provider.calls) == 8
    assert json.loads(provider.calls[0][0])["path"] == "runner.py"


def test_sparse_notes_still_get_parts_with_many_todos_and_no_readme(git_repo):
    from contextbridge.service import ProjectService

    (git_repo / "README.md").unlink()
    (git_repo / "main.py").write_text(
        '"""Create project reports from local code."""\ndef run(): pass\n'
    )
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    for i in range(6):
        service.add_note(f"Follow up on task {i}", "todo")
    handoff = service.prepare_handoff(to_agent="codex", use_llm=False).handoff.payload
    assert "What each part does" in handoff
    assert "Create project reports from local code" in handoff


def test_invalid_endpoint_is_not_printed_or_contacted(project, tmp_path):
    provider = FakeProvider(endpoint="https://user:private-key@example.test/v1")
    notices = []
    with Database.open(tmp_path / "cache.db") as db:
        result = analyze(project, db, provider, notify=notices.append)
    assert not provider.calls and result.warnings
    assert "private-key" not in str(notices) + result.render(1000)
