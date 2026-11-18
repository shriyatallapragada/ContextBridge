"""Review regressions: reasoning, quota pacing, opt-in polish and separate AI labels."""

import json
import re
from dataclasses import dataclass

import httpx
import pytest
from typer.testing import CliRunner

from contextbridge.config import Config, save_config
from contextbridge.main import app
from contextbridge.providers import credentials
from contextbridge.providers.base import ProviderRateLimited
from contextbridge.providers.http import HTTPProvider
from contextbridge.providers.limits import RateLimits, seconds
from contextbridge.providers.settings import ProviderSettingsService
from contextbridge.service import ProjectService
from contextbridge.storage.db import Database
from contextbridge.synthesis.compressor import project_sections
from contextbridge.synthesis.explanations import valid_explanation
from contextbridge.synthesis.project_summary import ProjectSummarizer
from tests.test_project_summary import FakeProvider
from tests.test_project_summary import project as project


@pytest.fixture
def transport(monkeypatch):
    original = httpx.Client

    def install(handler):
        monkeypatch.setattr(
            httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
        )

    return install


@pytest.mark.parametrize(
    "content",
    [
        "OK",
        [{"type": "reasoning", "text": "not final"}, {"type": "text", "text": "OK"}],
        "<think>private reasoning</think>OK",
    ],
)
def test_groq_reasoning_probe_reads_final_content_and_leaves_room(tmp_path, transport, content):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"reasoning": "private", "content": content}}]}
        )

    transport(respond)
    result = ProviderSettingsService(tmp_path).check(
        "openai-compatible", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "key"
    )
    assert result.ok
    assert calls[0]["max_completion_tokens"] >= 1024
    assert calls[0]["reasoning_effort"] == "low" and calls[0]["include_reasoning"] is False
    assert "max_tokens" not in calls[0] and "reasoning_format" not in calls[0]
    assert "private" not in result.message


@pytest.mark.parametrize(
    "content", [None, "", [{"type": "reasoning", "text": "not final"}], "<think>unfinished"]
)
def test_reasoning_only_response_is_explicit_failure(tmp_path, transport, content):
    transport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"reasoning": "do not expose", "content": content},
                    }
                ]
            },
        )
    )
    result = ProviderSettingsService(tmp_path).check(
        "openai-compatible", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "key"
    )
    assert not result.ok and "no final answer" in result.message and "1024" in result.message
    assert "do not expose" not in result.message


def test_unknown_compatible_endpoint_gets_no_unadvertised_reasoning_parameters(transport):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    transport(respond)
    HTTPProvider("openai/gpt-oss-120b", "https://custom.test/v1", api_key="key").generate("Hi")
    assert "reasoning_effort" not in bodies[0] and "include_reasoning" not in bodies[0]


def test_rate_headers_parse_durations_and_daily_request_semantics(transport):
    assert seconds("2m59.56s") == pytest.approx(179.56)
    limits = RateLimits()
    limits.update(
        {
            "x-ratelimit-limit-requests": "14400",
            "x-ratelimit-remaining-requests": "14370",
            "x-ratelimit-reset-requests": "2m59.56s",
        },
        0,
    )
    assert limits.wait(100, 100, 0) == 0  # RPD is not requests per minute.
    limits.update(
        {
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "50",
            "x-ratelimit-reset-tokens": "7.66s",
        },
        0,
    )
    assert limits.wait(100, 100, 0) >= 7.66
    transport(
        lambda request: httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "6000",
                "x-ratelimit-reset-tokens": "7.66s",
            },
            json={"choices": [{"message": {"content": "OK"}}]},
        )
    )
    provider = HTTPProvider("m", "https://example.test/v1", api_key="key")
    provider.generate("Hi")
    assert provider.rate_limits.limits["tokens"] == 8000


@dataclass
class Clock:
    value: float = 1_000_000

    def now(self):
        return self.value

    def sleep(self, delay):
        assert delay <= 5
        self.value += delay


def test_success_headers_pace_following_requests_and_plan_smaller_chunks(project, tmp_path):
    clock = Clock()
    notices = []
    times = []

    class Paced(FakeProvider):
        def generate(self, prompt, schema=None):
            times.append(clock.now())
            value = super().generate(prompt, schema)
            self.rate_limits.update(
                {
                    "x-ratelimit-limit-tokens": "4000",
                    "x-ratelimit-remaining-tokens": "3000",
                    "x-ratelimit-reset-tokens": "3s",
                },
                clock.now(),
            )
            return value

    provider = Paced()
    provider.rate_limits = RateLimits()
    (project / "core/report.py").write_text(
        (project / "core/report.py").read_text() + "# report details\n" * 40
    )
    with Database.open(tmp_path / "cache.db") as db:
        result = ProjectSummarizer(
            project,
            db,
            Config(),
            provider=provider,
            clock=clock.now,
            wall_clock=clock.now,
            sleep=clock.sleep,
            notify=notices.append,
        ).summarize()
        assert db.conn.execute("SELECT COUNT(*) FROM provider_limits").fetchone()[0] == 1
    assert len(times) >= 2 and times[1] - times[0] > 0.5
    assert any("Waiting" in n for n in notices)
    assert all(len(json.loads(p)["code"]) <= 500 for p, _ in provider.calls[1:])
    assert result.requests <= 12


def test_429_checkpoint_persists_and_next_run_respects_wait_then_resumes(project, tmp_path):
    clock = Clock()
    cfg = Config()
    cfg.summary.chunk_chars = 512
    cfg.summary.max_requests = 2
    (project / "runner.py").write_text(
        (project / "runner.py").read_text() + "# chunk context\n" * 100
    )
    with Database.open(tmp_path / "cache.db") as db:
        first = FakeProvider(responses=[None, ProviderRateLimited("rate", 600)])
        one = ProjectSummarizer(
            project,
            db,
            cfg,
            provider=first,
            clock=clock.now,
            wall_clock=clock.now,
            sleep=clock.sleep,
        ).summarize()
        assert len(first.calls) == 2
        blocked = FakeProvider()
        ProjectSummarizer(
            project,
            db,
            cfg,
            provider=blocked,
            clock=clock.now,
            wall_clock=clock.now,
            sleep=clock.sleep,
        ).summarize()
        assert not blocked.calls  # Immediate rerun must not hammer the endpoint.
        clock.value += 601
        resumed = FakeProvider()
        ProjectSummarizer(
            project,
            db,
            cfg,
            provider=resumed,
            clock=clock.now,
            wall_clock=clock.now,
            sleep=clock.sleep,
        ).summarize()
        assert json.loads(resumed.calls[0][0])["chunk"] == 2
        assert one.warnings


def test_explanations_are_optional_labeled_separate_cached_and_invalidated(project, tmp_path):
    class Explainer(FakeProvider):
        def generate(self, prompt, schema=None):
            if "explanation" in schema["properties"]:
                self.calls.append((prompt, schema))
                return json.dumps(
                    {
                        "explanation": (
                            "The `Report` class keeps report data together. "
                            "It helps the program reuse it."
                        ),
                        "references": ["Report"],
                    }
                )
            return super().generate(prompt, schema)

    with Database.open(tmp_path / "cache.db") as db:
        provider = Explainer()
        result = ProjectSummarizer(
            project, db, Config(), provider=provider, sleep=lambda s: None
        ).summarize(explain=True)
        assert len(db.file_explanations()) == 2
        assert all(row["label"] == "AI-written" for row in db.file_explanations())
        assert all(not row.get("ai_explanation") for row in db.file_summaries())
        assert "AI-written:" in result.render(2000)
        cached = Explainer()
        ProjectSummarizer(project, db, Config(), provider=cached, sleep=lambda s: None).summarize(
            explain=True
        )
        assert not cached.calls
        (project / "runner.py").write_text("def different(): pass\n")
        ProjectSummarizer(project, db, Config()).summarize(no_llm=True)
        assert not any(row["path"] == "runner.py" for row in db.file_explanations())


@pytest.mark.parametrize(
    "text,refs",
    [
        ("The `ImaginaryClass` handles data.", ["ImaginaryClass"]),
        ("This reads `missing.py` to start.", ["missing.py"]),
        ("OtherFileClass handles the work.", []),
        ("The function missing_helper processes data.", []),
    ],
)
def test_invented_names_drop_explanations(text, refs):
    assert not valid_explanation(text, refs, {"Report", "render", "current.py"})


def test_plain_english_without_code_names_and_function_call_references_are_valid():
    assert valid_explanation("This file gathers report data and passes it on.", [], {"Report"})
    assert valid_explanation(
        "The `render()` function prepares the output.", ["render()"], {"render", "render()"}
    )


def test_folder_counts_and_progress_have_consistent_denominators(project, tmp_path):
    (project / "asset.bin").write_bytes(b"\x00binary")
    notices = []
    with Database.open(tmp_path / "cache.db") as db:
        result = ProjectSummarizer(project, db, Config(), notify=notices.append).summarize(
            no_llm=True
        )
    progress = [
        re.match(r"File (\d+)/(\d+):", n).groups() for n in notices if n.startswith("File ")
    ]
    assert len(progress) == len(result.files)
    assert all(int(total) == len(result.files) for _, total in progress)
    assert sum(
        int(re.search(r"(\d+) eligible files", row)[1]) for row in result.folder_view()
    ) == len(result.tree)
    output = result.render(2000)
    assert "## Folders" in output and "## File tree" not in output
    assert f"{len(result.files)} files analyzed of {len(result.tree)} eligible files" in output


def test_handoff_is_quota_free_unless_polish_or_setting_is_enabled(git_repo, monkeypatch):
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    save_config(
        {
            "llm": {
                "provider": "openai-compatible",
                "model": "m",
                "base_url": "https://example.test/v1",
            }
        },
        service.ws.context_dir / "config.json",
    )
    monkeypatch.setattr(credentials, "get_key", lambda endpoint: "private-key")
    calls = []

    def request(self, path, body):
        calls.append(body)
        prompt = json.loads(body["messages"][0]["content"])
        return {"choices": [{"message": {"content": json.dumps({"lines": prompt["lines"]})}}]}

    monkeypatch.setattr(HTTPProvider, "request", request)
    runner = CliRunner()
    assert runner.invoke(app, ["handoff", "--dry-run"]).exit_code == 0
    assert not calls
    assert runner.invoke(app, ["handoff", "--dry-run", "--polish"]).exit_code == 0
    assert len(calls) == 1
    ProviderSettingsService(service.ws.context_dir).save(
        "openai-compatible", "m", "https://example.test/v1", polish_handoffs=True
    )
    assert runner.invoke(app, ["handoff", "--dry-run"]).exit_code == 0
    assert len(calls) == 2
    assert runner.invoke(app, ["handoff", "--dry-run", "--no-llm"]).exit_code == 0
    assert len(calls) == 2


def test_done_is_latest_detail_older_one_sentence_without_todo_spam(git_repo):
    from contextbridge.core.git_observer import CommitInfo
    from contextbridge.storage.db import utc_now

    service = ProjectService(git_repo)
    service.init(install_hook=False)
    service.add_note("Done: Added older work. This old detail must disappear.")
    service.add_note("Done: Added current work. This current detail stays.")
    todo = service.add_note("Completed old todo", "todo").decision
    with service.ws.open_db() as db:
        db.set_decision_status(todo.id, "resolved")
    context = service.prepare_handoff(use_llm=False).context
    context.commits = [CommitInfo(str(i), utc_now(), f"Commit {i}") for i in range(6)]
    done = project_sections(context)["Done"]
    assert "This current detail stays" in " ".join(done)
    assert "This old detail must disappear" not in " ".join(done)
    assert not any("Resolved todo" in text for text in done)
    assert sum(text.startswith("Recorded commit") for text in done) <= 3


def test_invalid_explanation_is_dropped_without_changing_grounded_purpose(project, tmp_path):
    class Invalid(FakeProvider):
        def generate(self, prompt, schema=None):
            if "explanation" in schema["properties"]:
                self.calls.append((prompt, schema))
                return json.dumps(
                    {
                        "explanation": "The `ImaginaryClass` processes reports.",
                        "references": ["ImaginaryClass"],
                    }
                )
            return super().generate(prompt, schema)

    with Database.open(tmp_path / "cache.db") as db:
        result = ProjectSummarizer(
            project, db, Config(), provider=Invalid(), sleep=lambda s: None
        ).summarize(explain=True)
        assert not db.file_explanations()
        assert all(not f.ai_explanation for f in result.files)
        assert all(f.method == "model" for f in result.files if f.language == "python")
        assert any("Dropped" in warning for warning in result.warnings)


def test_common_english_capital_cannot_hide_an_invented_class_name():
    assert not valid_explanation("The Data class prepares output.", [], {"Report"})


def test_429_captures_retry_and_rate_headers(transport):
    transport(
        lambda request: httpx.Response(
            429,
            headers={
                "retry-after": "3",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "0",
                "x-ratelimit-reset-tokens": "7s",
            },
        )
    )
    provider = HTTPProvider("m", "https://example.test/v1", api_key="key")
    with pytest.raises(ProviderRateLimited) as error:
        provider.generate("Hi")
    assert error.value.retry_after == 3
    assert provider.rate_limits.remaining["tokens"] == 0
    assert "3.0s" in str(error.value)
