"""Convention-based overview descriptions and bounded large offline projects."""

from pathlib import Path

import pytest

from contextbridge.project_map import graph_view, scan_project


@pytest.mark.parametrize(
    ("path", "description"),
    [
        ("src/components/Card.tsx", "Reusable interface"),
        ("lib/utils/date.ts", "Shared helpers"),
        ("src/hooks/useState.ts", "Reusable hooks"),
        ("src/api/events.ts", "Request endpoints"),
        ("app/admin/page.tsx", "Next.js pages"),
        ("src/models/user.ts", "Data types"),
        ("src/services/events.ts", "Service code"),
        ("tests/unit/example.ts", "Checks that verify"),
        ("docs/examples/demo.js", "Project documentation"),
        ("assets/icons/index.js", "Images, styles"),
        ("lib/main.dart", "Flutter startup"),
        ("lib/features/calendar.dart", "Flutter feature screens"),
    ],
)
def test_conventional_role_is_labeled_automatic(tmp_path: Path, path: str, description: str):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("// Example source\n")
    node = graph_view(scan_project(tmp_path))["nodes"][0]
    assert description in node["purpose"]
    assert node["purpose_source"] == "automatic"
    assert node["ai_explanation"] is None
    assert node["top_files"] == [path]
    assert node["uses_count"] == node["used_by_count"] == 0


def test_large_project_groups_small_folders_and_counts_distinct_files(tmp_path: Path):
    paths = []
    for index in range(55):
        relative = f"features/f{index:02}/view.ts"
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_text("export const view = 1;\n")
        paths.append(relative)
    (tmp_path / "main.ts").write_text("\n".join(f"import './{path[:-3]}';" for path in paths))
    (tmp_path / "other.ts").write_text("import './features/f00/view';\n")
    model = scan_project(tmp_path)
    assert len(model.graph_nodes) >= 57
    graph = graph_view(model)
    assert len(graph["nodes"]) == 8
    assert sum(n["file_count"] for n in graph["nodes"]) == 57
    collapsed = next(n for n in graph["nodes"] if n["label"].startswith("+"))
    assert collapsed["label"] == "+49 more"
    assert collapsed["file_count"] == 49
    assert len(collapsed["top_files"]) == 3
    root = next(n for n in graph["nodes"] if n["path"] == ".")
    assert root["uses_count"] == 55
    assert root["used_by_count"] == 0
    assert sum(e["count"] for e in graph["edges"]) == 55
    assert all(e["source"] == root["id"] for e in graph["edges"])
