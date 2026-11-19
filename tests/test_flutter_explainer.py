"""A useful Flutter overview and honest fallback, with on-demand private explanations."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from contextbridge.config import Config, save_config
from contextbridge.errors import UserError
from contextbridge.project_map import graph_view, scan_project
from contextbridge.service import ProjectService
from contextbridge.storage.db import Database
from contextbridge.synthesis.project_summary import ProjectSummarizer, static_file
from tests.test_project_summary import FakeProvider


@pytest.fixture
def flutter(tmp_path):
    root = tmp_path / "planner"
    shutil.copytree(Path(__file__).parent / "fixtures/flutter_project", root)
    return root


def test_flutter_components_imports_and_entrypoint(flutter):
    model = scan_project(flutter)
    paths = {n.id: n.path for n in model.graph_nodes.values()}
    imports = {(paths[e.source], paths[e.target]) for e in model.graph_edges}
    assert ("lib/main.dart", "lib/core/theme.dart") in imports
    assert ("lib/main.dart", "lib/features/calendar/calendar_page.dart") in imports
    assert ("lib/features/calendar/calendar_page.dart", "lib/data/calendar_store.dart") in imports
    assert not any("imaginary" in p for edge in imports for p in edge)
    assert [paths[i] for i in model.entrypoints] == ["lib/main.dart"]
    overview = graph_view(model)
    assert 5 <= len(overview["nodes"]) <= 8
    assert sum(n["file_count"] for n in overview["nodes"]) == model.scanned == 6
    assert all(n["purpose"] and len(n["label"]) < 70 for n in overview["nodes"])
    assert all(e["kind"] == "imports" for e in overview["edges"])
    # Every overview arrow is a projection of an actual parsed import.
    components = {p: n["id"] for n in overview["nodes"] for p in n["related_files"]}
    projected = {(components[a], components[b]) for a, b in imports}
    assert all((e["source"], e["target"]) in projected for e in overview["edges"])
    docs = next(n for n in overview["nodes"] if n["path"] == "docs/widgets")
    assert not any(docs["id"] in (e["source"], e["target"]) for e in overview["edges"])
    flow = graph_view(model, "flow")
    assert flow["nodes"] and any(n["path"] == "lib/data/calendar_store.dart" for n in flow["nodes"])
    assert "Dart/Flutter" in flow["meta"]["import_support"]
    assert "pubspec.yaml" in flow["meta"]["entrypoint_support"]


def test_cached_component_description_is_hash_matched_and_labeled(flutter):
    path = "lib/data/calendar_store.dart"
    digest = hashlib.sha256((flutter / path).read_bytes()).hexdigest()
    explanations = [
        {
            "path": path,
            "content_hash": digest,
            "explanation": "This part keeps calendar events together.",
        }
    ]
    model = scan_project(flutter, explanations=explanations)
    component = next(n for n in graph_view(model)["nodes"] if n["path"] == "lib/data")
    assert component["purpose_source"] == "automatic"
    assert "Flutter data" in component["purpose"]
    from contextbridge.project_map import _component_groups, component_digest

    members = _component_groups(
        [n for n in model.graph_nodes.values() if n.kind in {"file", "module"}]
    )["lib/data"]
    model.component_summaries = [
        {
            "path": "lib/data",
            "content_hash": component_digest(members, model.hashes),
            "explanation": "This part keeps calendar events together.",
        }
    ]
    component = next(n for n in graph_view(model)["nodes"] if n["path"] == "lib/data")
    assert component["purpose_source"] == "AI-written"
    assert component["purpose"] == component["ai_explanation"]["text"]
    (flutter / path).write_text("class CalendarStore {}\n")
    changed = scan_project(flutter, explanations=explanations)
    assert not any(n.ai_explanation for n in changed.graph_nodes.values())


def test_unsupported_connections_and_no_entrypoint_never_blank(tmp_path):
    (tmp_path / "widget.go").write_text("package widget\nfunc Render() {}\n")
    (tmp_path / "other.go").write_text("package widget\nfunc Other() {}\n")
    model = scan_project(tmp_path)
    graph = graph_view(model, "flow")
    assert graph["nodes"] and not graph["edges"]
    assert "No entry points detected" in graph["meta"]["flow_notice"]
    assert graph["meta"]["connections_notice"] == "connections not detected for this language"
    assert graph_view(model, "flow", "other.go")["nodes"][0]["path"] == "other.go"
    detail = graph_view(model, "module", "widget.go")
    assert detail["nodes"][0]["purpose"] == "Not explained yet"


def test_javascript_real_import_only_and_no_description(flutter):
    path = flutter / "docs/widgets/calendar.js"
    source = path.read_text()
    model = scan_project(flutter)
    detail = graph_view(model, "module", "docs/widgets/calendar.js")
    assert detail["nodes"][0]["purpose"] == "Not explained yet"
    path.write_text(
        source + '\nconst sample = "import \'./helpers.js\'";\n// import "./helpers.js";\n'
    )
    (path.parent / "helpers.js").write_text("export const day = 1;\n")
    assert not any(
        e.evidence == "docs/widgets/calendar.js" for e in scan_project(flutter).graph_edges
    )
    path.write_text(source + '\nimport {day} from "./helpers.js";\n')
    model = scan_project(flutter)
    assert any(e.evidence == "docs/widgets/calendar.js" for e in model.graph_edges)


def test_single_file_explanation_privacy_cache_invalidation_and_no_other_files(flutter, tmp_path):
    class Explainer(FakeProvider):
        def generate(self, prompt, schema=None):
            self.calls.append(prompt)
            assert "explanation" in schema["properties"]
            assert "calendar.js" in prompt and "CALENDAR_OTHER_FILE" not in prompt
            assert "AKIAIOSFODNN7EXAMPLE" not in prompt
            return json.dumps(
                {"explanation": "This file returns a calendar preview.", "references": []}
            )

    path = "docs/widgets/calendar.js"
    (flutter / path).write_text(
        'function render() { return "Calendar preview"; }\n// AKIAIOSFODNN7EXAMPLE\n'
    )
    (flutter / "lib/main.dart").write_text("// CALENDAR_OTHER_FILE\nvoid main() {}\n")
    config, notices = Config(), []
    with Database.open(tmp_path / "cache.db") as db:
        provider = Explainer()
        result = ProjectSummarizer(
            flutter, db, config, provider=provider, notify=notices.append
        ).summarize(only_file=path, explain=True)
        assert result.files[0].ai_explanation and len(provider.calls) == 1
        assert any("hosted provider" in n for n in notices)
        ProjectSummarizer(flutter, db, config, provider=provider).summarize(
            only_file=path, explain=True
        )
        assert len(provider.calls) == 1
        (flutter / path).write_text("function render() { return 'New preview'; }\n")
        ProjectSummarizer(flutter, db, config, provider=provider).summarize(
            only_file=path, explain=True
        )
        assert len(provider.calls) == 2
        assert len(db.file_explanations()) == 1
        for invalid in (".env", "../private.js", "/etc/passwd"):
            with pytest.raises(UserError):
                ProjectSummarizer(flutter, db, config, provider=provider).summarize(
                    only_file=invalid, explain=True
                )
        assert len(provider.calls) == 2


def test_explain_requires_selected_provider_and_notice_has_no_key(flutter, monkeypatch):
    service = ProjectService(flutter)
    service.init(install_hook=False)
    config_path = service.ws.context_dir / "config.json"
    save_config({"llm": {"provider": "none"}}, config_path)
    with pytest.raises(UserError, match="Settings"):
        service.explain_file("lib/main.dart")
    save_config({"llm": {"provider": "groq", "cloud_model": "openai/gpt-oss-120b"}}, config_path)
    monkeypatch.setattr(
        "contextbridge.providers.credentials.get_key", lambda *_: pytest.fail("key read")
    )
    notice = service.file_explanation_notice("lib/main.dart")
    assert "groq" in notice and "Secrets are redacted" in notice


def test_dart_main_and_build_are_available_to_explanation_validator(flutter):
    from contextbridge.synthesis.explanations import allowed_names, valid_explanation

    for path, name in (
        ("lib/main.dart", "main"),
        ("lib/features/calendar/calendar_page.dart", "build"),
    ):
        source = (flutter / path).read_text()
        fact = static_file(path, source, "digest", set())
        names = allowed_names(path, source, fact.key_symbols, fact.depends_on)
        assert name in names
        assert valid_explanation(f"The `{name}` function prepares the screen.", [name], names)
