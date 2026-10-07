"""`cb search`: semantic search when the vector store is enabled, else keyword search."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from contextbridge.cli.common import console, err_console, error_boundary, log, open_workspace
from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import UserError
from contextbridge.storage.db import SearchHit, keyword_search
from contextbridge.storage.workspace import Workspace

VECTOR_DIRNAME = "vector_db"
SNIPPET_CHARS = 160


def _semantic(ws: Workspace, query: str, limit: int) -> list[SearchHit]:
    from contextbridge.storage.vector_store import VectorStore

    store = VectorStore(ws.context_dir / VECTOR_DIRNAME)
    with ws.open_db() as db:
        store.sync(db)
    return store.search(query, limit=limit)


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 3] + "..."


def search_command(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="What to look for in decisions and changes."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100, help="Maximum results."),
) -> None:
    """Search decisions and file changes across all sessions."""
    with error_boundary():
        query = sanitize(query).strip()
        if not query:
            raise UserError("Search query is empty.")
        ws = open_workspace(ctx)
        config = ws.load_config()
        hits: list[SearchHit] | None = None
        mode = "keyword"
        if config.vector.enabled:
            try:
                hits = _semantic(ws, query, limit)
                mode = "semantic"
            except Exception as exc:
                log.warning("search: vector search failed: %s", exc)
                err_console.print(
                    f"[yellow]Semantic search unavailable ({escape(str(exc))}); "
                    "using keyword search.[/]"
                )
        else:
            err_console.print(
                "[dim]Keyword search (set vector.enabled in .context/config.json for "
                "semantic search).[/]"
            )
        if hits is None:
            with ws.open_db() as db:
                hits = keyword_search(db, query, limit=limit)

        log.info("search: mode=%s results=%d", mode, len(hits))
        if not hits:
            console.print(f"No results for [cyan]{escape(query)}[/].")
            return
        table = Table(
            title=f"{mode.capitalize()} results for “{escape(query)}”", title_justify="left"
        )
        table.add_column("Kind", no_wrap=True)
        table.add_column("When", style="dim", no_wrap=True)
        if mode == "semantic":
            table.add_column("Score", justify="right")
        table.add_column("Match")
        for hit in hits:
            row = [hit.kind.replace("_", " "), hit.timestamp[:16].replace("T", " ")]
            if mode == "semantic":
                row.append(f"{hit.score:.2f}" if hit.score is not None else "")
            row.append(escape(_snippet(hit.text)))
            table.add_row(*row)
        console.print(table)
