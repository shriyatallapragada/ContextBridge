"""`cb hook post-commit`: record the latest commit's file changes and symbols.

Runs from the git hook in the background. It is silent and never fails loudly:
every error is logged to `.context/cb.log` and the process exits 0.
"""

from __future__ import annotations

import contextlib

import typer

from contextbridge.cli.common import app_context, log
from contextbridge.core.change_logger import ingest_head, record_commit  # noqa: F401
from contextbridge.errors import ContextBridgeError
from contextbridge.service import ProjectService
from contextbridge.storage.workspace import Workspace

# Older unrecorded commits of the session picked up by each hook run (e.g. after a
# rebase, which does not run post-commit for every pick).
BACKFILL_LIMIT = 20

hook_app = typer.Typer(
    help="Internal: entry points called by git hooks.", no_args_is_help=True, hidden=True
)


@hook_app.command("post-commit")
def post_commit_command(ctx: typer.Context) -> None:
    """Record the latest commit. Silent; always exits 0."""
    try:
        ws = Workspace.locate(app_context(ctx).start_dir)
    except ContextBridgeError:
        return  # Not initialized here: nothing to do, and nothing to complain about.
    try:
        ws.setup_logging(verbose=app_context(ctx).verbose)
        ProjectService(ws.root).ingest_head()
    except Exception:
        with contextlib.suppress(Exception):  # Even logging must not fail the hook.
            log.exception("hook post-commit failed")
