"""Next app-router labels, local aliases, grounded component reduction and budgeted resume."""

import json
import shutil
from pathlib import Path

import pytest

from contextbridge.config import Config
from contextbridge.core.web_facts import read_json_config
from contextbridge.project_map import graph_view, scan_project
from contextbridge.providers.base import ProviderRateLimited
from contextbridge.storage.db import Database
from contextbridge.synthesis.project_summary import ProjectSummarizer
from tests.test_project_summary import FakeProvider


@pytest.fixture
def next_project(tmp_path):
    root = tmp_path / "next"
    shutil.copytree(Path(__file__).parent / "fixtures/next_project", root)
    return root


class Explainer(FakeProvider):
    def generate(self, prompt, schema=None):
        data = json.loads(prompt)
        self.calls.append(data)
        if "file_explanations" in data:
            assert "code" not in data and "folder" not in data
            assert all("explanation" in f for f in data["file_explanations"])
            return json.dumps(
                {
                    "explanation": "These files prepare reusable parts of the application.",
                    "references": [],
                }
            )
        return json.dumps(
            {
                "explanation": "This file prepares a reusable part of the application.",
                "references": [],
            }
        )


def config(requests=100):
    result = Config()
    result.summary.max_requests = requests
    return result


def test_root_and_components_have_no_name_based_description(next_project):
    graph = graph_view(scan_project(next_project))
    root = next(n for n in graph["nodes"] if n["path"] == ".")
    assert root["label"] == "Project root"
    assert all(
        n["purpose"] == "Not explained yet" and not n["ai_explanation"] for n in graph["nodes"]
    )
    assert "explain a file for details" not in json.dumps(graph)
    assert "Files for" not in json.dumps(graph)


def test_next_route_labels_and_alias_imports_with_index_files(next_project):
    model = scan_project(next_project)
    files = {n.path: n for n in model.graph_nodes.values() if n.kind == "file"}
    assert files["app/admin/page.tsx"].label == "Admin page (/admin)"
    assert files["app/page.tsx"].label == "Home page (/)"
    assert files["app/layout.tsx"].label == "Root layout (/)"
    assert files["app/api/events/route.ts"].label == "API route (/api/events)"
    assert "components/index.tsx" in files["app/admin/page.tsx"].depends_on
    assert "app/admin/page.tsx" in files["components/index.tsx"].used_by
    assert "lib/events.ts" in files["app/api/events/route.ts"].depends_on
    assert files["server.js"].routes == ["GET /health"]
    assert "GET /health" in files["server.js"].label
    assert files["app/admin/page.tsx"].id in model.entrypoints
    assert "aliases" in graph_view(model)["meta"]["import_support"]


def test_jsconfig_local_extends_and_alias_target_fallbacks(next_project):
    (next_project / "tsconfig.json").unlink()
    (next_project / "base.json").write_text(
        '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["missing/*","./*"]}}}'
    )
    (next_project / "jsconfig.json").write_text('{"extends":"./base.json"}')
    model = scan_project(next_project)
    node = next(n for n in model.graph_nodes.values() if n.path == "app/admin/page.tsx")
    assert "components/index.tsx" in node.depends_on
    (next_project / "jsconfig.json").write_text('{"extends":"../../outside.json"}')
    assert not read_json_config(next_project / "jsconfig.json", next_project)["compilerOptions"]


def test_flask_routes_are_parsed_from_real_decorators(next_project):
    (next_project / "api.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n@app.get("/health")\n'
        'def health(): return "ok"\n# @app.post("/imaginary")\n'
    )
    node = next(
        n
        for n in scan_project(next_project).graph_nodes.values()
        if n.path == "api.py" and n.kind == "module"
    )
    assert node.routes == ["GET /health"]


def test_explain_project_over_eight_files_pacing_map_reduce_and_cache(next_project, tmp_path):
    provider, messages, sleeps = Explainer(), [], []
    with Database.open(tmp_path / "cache.db") as db:
        runner = ProjectSummarizer(
            next_project,
            db,
            config(),
            provider=provider,
            notify=messages.append,
            sleep=sleeps.append,
        )
        result = runner.summarize(explain_project=True, explain=True, token_budget=100000)
        file_calls = [c for c in provider.calls if "code" in c]
        reductions = [c for c in provider.calls if "file_explanations" in c]
        assert len(file_calls) == 12 > 8
        assert file_calls[0]["path"].startswith("app/")
        assert reductions and len(file_calls) + len(reductions) == result.requests
        assert result.tokens_reserved <= 100000
        assert sleeps and all(0 <= wait <= 0.5 for wait in sleeps)
        model = scan_project(
            next_project,
            explanations=db.file_explanations(),
            components=db.component_explanations(),
        )
        graph = graph_view(model)
        assert all(n["purpose_source"] == "AI-written" for n in graph["nodes"])
        assert all(
            n["ai_explanation"] and n["explained_files"] == n["file_count"] for n in graph["nodes"]
        )
        provider.calls.clear()
        resumed = ProjectSummarizer(next_project, db, config(), provider=provider).summarize(
            explain_project=True, explain=True, token_budget=100000
        )
        assert resumed.requests == 0 and not provider.calls
        # Updating one file invalidates both that file's explanation and its component reduction.
        changed = next_project / "middleware.ts"
        changed.write_text(changed.read_text() + "\nexport const changed = true;\n")
        updated = graph_view(
            scan_project(
                next_project,
                explanations=db.file_explanations(),
                components=db.component_explanations(),
            )
        )
        assert (
            next(n for n in updated["nodes"] if n["path"] == ".")["purpose"] == "Not explained yet"
        )
        ProjectSummarizer(
            next_project, db, config(), provider=provider, sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert len([c for c in provider.calls if "code" in c]) == 1


def test_budget_stop_and_resume_uses_cached_files(next_project, tmp_path):
    with Database.open(tmp_path / "cache.db") as db:
        first = Explainer()
        result = ProjectSummarizer(
            next_project, db, config(3), provider=first, sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert result.requests == 3 and any("Request budget" in w for w in result.warnings)
        done = {c["path"] for c in first.calls if "code" in c}
        second = Explainer()
        ProjectSummarizer(
            next_project, db, config(), provider=second, sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert not done & {c["path"] for c in second.calls if "code" in c}
        assert len(done | {c["path"] for c in second.calls if "code" in c}) == 12


def test_token_budget_stops_before_sending_a_request(next_project, tmp_path):
    with Database.open(tmp_path / "cache.db") as db:
        provider = Explainer()
        result = ProjectSummarizer(next_project, db, config(), provider=provider).summarize(
            explain_project=True, explain=True, token_budget=1000
        )
        assert result.requests == 0 and not provider.calls
        assert any("Token budget" in w for w in result.warnings)


def test_rate_limit_stops_clearly_without_retry_and_resumes(next_project, tmp_path):
    class Limited(Explainer):
        def generate(self, prompt, schema=None):
            if len(self.calls) == 1:
                raise ProviderRateLimited("rate limit", retry_after=7)
            return super().generate(prompt, schema)

    with Database.open(tmp_path / "cache.db") as db:
        notices, sleeps, provider = [], [], Limited()
        result = ProjectSummarizer(
            next_project,
            db,
            config(),
            provider=provider,
            notify=notices.append,
            sleep=sleeps.append,
            wall_clock=lambda: 1000,
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert result.requests == 2 and len(db.file_explanations()) == 1
        assert any("Rate limit hit: stopped" in n and "7s" in n for n in notices)
        assert not any(wait > 0.5 for wait in sleeps)
        resumed = Explainer()
        ProjectSummarizer(
            next_project,
            db,
            config(),
            provider=resumed,
            sleep=lambda _: None,
            wall_clock=lambda: 1010,
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert len([c for c in resumed.calls if "code" in c]) == 11


def test_folder_rejects_invented_names(next_project, tmp_path):
    class Invented(Explainer):
        def generate(self, prompt, schema=None):
            data = json.loads(prompt)
            if "file_explanations" in data:
                self.calls.append(data)
                return json.dumps(
                    {
                        "explanation": "The `GhostRouter` runs the application.",
                        "references": ["GhostRouter"],
                    }
                )
            return super().generate(prompt, schema)

    with Database.open(tmp_path / "cache.db") as db:
        result = ProjectSummarizer(
            next_project, db, config(), provider=Invented(), sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert any("invalid code references" in w for w in result.warnings)
        assert not db.component_explanations()


def test_component_reductions_resume_without_reexplaining_files(next_project, tmp_path):
    with Database.open(tmp_path / "cache.db") as db:
        ProjectSummarizer(
            next_project, db, config(12), provider=Explainer(), sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert len(db.file_explanations()) == 12
        middle = ProjectSummarizer(
            next_project, db, config(2), provider=Explainer(), sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert middle.requests == 2
        provider = Explainer()
        last = ProjectSummarizer(
            next_project, db, config(), provider=provider, sleep=lambda _: None
        ).summarize(explain_project=True, explain=True, token_budget=100000)
        assert not any("code" in c for c in provider.calls)
        graph = graph_view(
            scan_project(
                next_project,
                explanations=db.file_explanations(),
                components=db.component_explanations(),
            )
        )
        assert last.requests == len(graph["nodes"]) - 2
        assert all(n["ai_explanation"] for n in graph["nodes"])


def test_express_ignores_commented_and_quoted_route_examples(next_project):
    source = next_project / "server.js"
    source.write_text(
        source.read_text()
        + "\n// app.post('/fake', handler);\nconst example = \"app.get('/also-fake', handler)\";\n"
    )
    node = next(n for n in scan_project(next_project).graph_nodes.values() if n.path == "server.js")
    assert node.routes == ["GET /health"]


def test_explain_component_limits_uploads_to_its_files_and_reduces_them(next_project, monkeypatch):
    from contextbridge.config import save_config
    from contextbridge.service import ProjectService

    service = ProjectService(next_project)
    service.init(install_hook=False)
    save_config(
        {
            "llm": {
                "provider": "openai-compatible",
                "base_url": "https://example.test/v1",
                "model": "m",
            }
        },
        service.ws.context_dir / "config.json",
    )
    provider = Explainer()
    monkeypatch.setattr("contextbridge.providers.create_provider", lambda *a, **kw: provider)
    monkeypatch.setattr(
        "contextbridge.synthesis.project_summary.create_provider", lambda *a, **kw: provider
    )
    report = service.explain_project(component="components", max_requests=10, token_budget=20000)
    assert "Finished" in report and "2/10 requests" in report
    assert [c["path"] for c in provider.calls if "code" in c] == ["components/index.tsx"]
    graph = service.project_graph()
    assert next(n for n in graph["nodes"] if n["path"] == "components")["ai_explanation"]
    assert all(not n["ai_explanation"] for n in graph["nodes"] if n["path"] != "components")
