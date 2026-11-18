"""UI-free graphs from source, stable identifiers, bounded views and safe Mermaid."""

import hashlib
import json
from pathlib import Path

import pytest

from contextbridge.errors import UserError
from contextbridge.project_map import (
    export_mermaid,
    graph_view,
    scan_project,
    validate_graph,
    validate_mermaid,
)


@pytest.fixture
def sample(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="sample"\n[project.scripts]\ncb="main:main"\n'
    )
    (tmp_path / "main.py").write_text(
        '"""Start the sample app."""\nfrom app.service import Service\n'
        "def main():\n    service = Service()\n    return service.run()\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app/service.py").write_text(
        '"""Coordinate sample requests."""\nfrom app.storage import save\n'
        "class Service:\n    def run(self):\n"
        '        """Save the sample result."""\n        return save()\n'
    )
    (tmp_path / "app/storage.py").write_text(
        '"""Store sample results."""\ndef save():\n    return "saved"\n'
    )
    return tmp_path


def test_calls_imports_methods_and_stable_ids_without_execution(sample):
    model = scan_project(sample)
    nodes = {n.id: n for n in model.graph_nodes.values()}
    calls = [
        (nodes[e.source].label, nodes[e.target].label)
        for e in model.graph_edges
        if e.kind == "calls"
    ]
    assert ("main", "Service.run") in calls and ("Service.run", "save") in calls
    assert any(e.kind == "imports" for e in model.graph_edges)
    ids = {(n.path, n.label): n.id for n in nodes.values()}
    source = sample / "app/service.py"
    source.write_text(
        "\n\n" + source.read_text() + '\nraise RuntimeError("never execute project code")\n'
    )
    again = scan_project(sample)
    assert all(ids[n.path, n.label] == n.id for n in again.graph_nodes.values())
    for view in ("overview", "module", "flow"):
        graph = graph_view(again, view, "app/service.py" if view == "module" else None)
        validate_graph(graph)
        assert json.loads(json.dumps(graph)) == graph
        assert all(
            {"kind", "path", "group", "purpose", "ai_explanation"} <= n.keys()
            for n in graph["nodes"]
        )


@pytest.mark.parametrize("view", ["overview", "module", "flow"])
def test_view_caps_include_exact_more_node(sample, view):
    (sample / "extra").mkdir()
    (sample / "extra/extra.py").write_text("def extra(): pass\n")
    graph = graph_view(
        scan_project(sample), view, "app/service.py" if view == "module" else None, 2
    )
    assert len(graph["nodes"]) <= 2
    hidden = graph["meta"]["hidden_nodes"]
    assert hidden > 0
    collapsed = next(n for n in graph["nodes"] if n["kind"] == "collapsed")
    assert collapsed["label"] == f"+{hidden} more"
    assert graph["meta"]["total_nodes"] == hidden + len(graph["nodes"]) - 1
    assert all(e["source"] in {n["id"] for n in graph["nodes"]} for e in graph["edges"])


def test_cached_purpose_and_ai_provenance_require_current_content_hash(sample):
    source = sample / "app/service.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = [
        {
            "path": "app/service.py",
            "content_hash": digest,
            "purpose": "Coordinate sample requests.",
            "method": "model",
        }
    ]
    ai = [
        {
            "path": "app/service.py",
            "content_hash": digest,
            "explanation": "This file connects the app to its saved data.",
        }
    ]
    graph = graph_view(
        scan_project(sample, cached=cache, explanations=ai), "module", "app/service.py"
    )
    file_node = next(
        n for n in graph["nodes"] if n["kind"] == "module" and n["path"] == "app/service.py"
    )
    assert file_node["ai_explanation"]["label"] == "AI-written"
    assert file_node["purpose"] != file_node["ai_explanation"]["text"]
    source.write_text(source.read_text() + "\n# changed\n")
    changed = scan_project(sample, cached=cache, explanations=ai)
    assert not any(n.ai_explanation for n in changed.graph_nodes.values())


def test_graph_skips_sensitive_ignored_and_external_source(sample):
    (sample / ".gitignore").write_text("private/\n")
    for folder in ("private", "secrets"):
        (sample / folder).mkdir()
        (sample / folder / "hidden.py").write_text('"""SECRET_MUST_NOT_APPEAR."""')
    (sample / "link.py").symlink_to("/etc/passwd")
    (sample / "credentials.py").write_text('"""SECRET_MUST_NOT_APPEAR."""')
    text = json.dumps(graph_view(scan_project(sample)))
    assert "SECRET_MUST_NOT_APPEAR" not in text and "link.py" not in text


def test_mermaid_is_deterministic_escaped_and_validated(sample):
    graph = graph_view(scan_project(sample))
    graph["nodes"][0]["label"] = 'end"] --> injected["<script> %%{init:evil} &quot; \\ \n next'
    first = export_mermaid(graph)
    assert first == export_mermaid(graph)
    assert "<script>" not in first and "%%{" not in first and "&quot;" not in first
    assert "#34;" in first and "#60;" in first and "#38;" in first
    validate_mermaid(first)
    graph["edges"].append({"source": "bad", "target": "unknown", "kind": "calls"})
    with pytest.raises(UserError):
        export_mermaid(graph)
    with pytest.raises(UserError):
        validate_mermaid('flowchart TD\nclick node "https://evil.test"\n')


def test_ai_export_is_explicit_and_labeled(sample):
    graph = graph_view(scan_project(sample))
    graph["nodes"][0]["ai_explanation"] = {"text": "Beginner explanation.", "label": "AI-written"}
    assert "Beginner explanation" not in export_mermaid(graph)
    assert "AI-written: Beginner explanation" in export_mermaid(graph, include_ai=True)


def test_graph_this_repository_and_cli_service_storage_flow():
    root = Path(__file__).resolve().parents[1]
    model = scan_project(root)
    assert model.scanned > 30
    flow = graph_view(model, "flow", "contextbridge/main.py", 12)
    paths = {n["path"] for n in flow["nodes"]}
    assert "contextbridge/main.py" in paths and "contextbridge/service.py" in paths
    assert any(p.startswith("contextbridge/cli/") for p in paths)
    assert any(p.startswith("contextbridge/storage/") for p in paths)
    assert any(p.startswith("contextbridge/providers/") for p in paths)
    detail = graph_view(model, "module", "contextbridge/service.py", 30)
    assert any(n["label"] == "ProjectService.prepare_handoff" for n in detail["nodes"])
    assert any(
        e.kind == "calls"
        and "cli/handoff_cmd.py" in model.graph_nodes[e.source].path
        and model.graph_nodes[e.target].path == "contextbridge/service.py"
        for e in model.graph_edges
    )
    for graph in (flow, detail, graph_view(model)):
        validate_mermaid(export_mermaid(graph))


def test_module_path_must_be_in_the_scanned_project(sample):
    model = scan_project(sample)
    for path in ("../../etc/passwd", "/etc/passwd", "missing.py"):
        with pytest.raises(UserError):
            graph_view(model, "module", path)
    assert graph_view(model, "flow", "app/storage.py")["nodes"]
