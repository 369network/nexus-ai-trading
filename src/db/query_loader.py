"""
src/db/query_loader.py
----------------------
Loads named SQL queries from .sql files at startup and exposes them
via a simple dictionary-like interface.

Query files use the convention:

    -- name: my_query_name
    SELECT ...
    ;

    -- name: another_query
    SELECT ...
    ;

All text between one ``-- name:`` marker and the next (or end-of-file)
belongs to the named query.  Leading/trailing whitespace is stripped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

# Regex that matches the `-- name: <identifier>` marker line.
_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*--\s*name:\s*(?P<name>\w+)\s*$",
    re.MULTILINE,
)


class SQLQueryLoader:
    """Loads and caches named SQL queries from .sql files in a directory.

    Parameters
    ----------
    queries_dir:
        Path to the directory that contains .sql files.  The directory is
        scanned recursively so queries may live in sub-directories.

    Usage
    -----
    >>> loader = SQLQueryLoader(Path("src/db/queries"))
    >>> loader.load_all()
    >>> sql = loader.get("get_portfolio_summary")
    """

    def __init__(self, queries_dir: Path) -> None:
        if not queries_dir.is_dir():
            raise NotADirectoryError(
                f"Query directory does not exist or is not a directory: {queries_dir}"
            )
        self._queries_dir: Path = queries_dir
        self._cache: dict[str, str] = {}
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> None:
        """Scan *queries_dir* recursively for .sql files and cache every
        named query found.

        Safe to call multiple times; subsequent calls reload from disk and
        replace the existing cache (useful for hot-reloading in development).
        """
        new_cache: dict[str, str] = {}

        sql_files = sorted(self._queries_dir.rglob("*.sql"))
        if not sql_files:
            logger.warning(
                "no_sql_files_found",
                queries_dir=str(self._queries_dir),
            )

        for sql_file in sql_files:
            parsed = self._parse_file(sql_file)
            duplicates = set(parsed) & set(new_cache)
            if duplicates:
                logger.warning(
                    "duplicate_query_names",
                    file=str(sql_file),
                    names=sorted(duplicates),
                )
            new_cache.update(parsed)
            logger.debug(
                "loaded_sql_file",
                file=str(sql_file),
                query_count=len(parsed),
                query_names=sorted(parsed.keys()),
            )

        self._cache = new_cache
        self._loaded = True
        logger.info(
            "sql_queries_loaded",
            total=len(self._cache),
            names=sorted(self._cache.keys()),
        )

    def get(self, name: str) -> str:
        """Return the SQL string for the named query.

        Parameters
        ----------
        name:
            The identifier used in the ``-- name: <name>`` marker inside
            a .sql file.

        Raises
        ------
        RuntimeError
            If :meth:`load_all` has not been called yet.
        KeyError
            If no query with *name* exists in any loaded .sql file.
        """
        if not self._loaded:
            raise RuntimeError(
                "SQLQueryLoader.load_all() must be called before get(). "
                "Call load_all() once at application startup."
            )

        try:
            return self._cache[name]
        except KeyError:
            available = sorted(self._cache.keys())
            raise KeyError(
                f"No SQL query named '{name}' was found in {self._queries_dir}. "
                f"Available queries ({len(available)}): {available}"
            ) from None

    @property
    def names(self) -> list[str]:
        """Sorted list of all cached query names."""
        return sorted(self._cache.keys())

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, name: object) -> bool:
        return name in self._cache

    def __repr__(self) -> str:
        return (
            f"SQLQueryLoader("
            f"queries_dir={self._queries_dir!r}, "
            f"loaded={self._loaded}, "
            f"count={len(self._cache)})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_file(self, path: Path) -> dict[str, str]:
        """Parse a single .sql file and return ``{name: sql}`` pairs."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("failed_to_read_sql_file", file=str(path), error=str(exc))
            return {}

        return _split_named_queries(text, source=str(path))


def _split_named_queries(text: str, source: str = "<unknown>") -> dict[str, str]:
    """Split *text* into named sections delimited by ``-- name: X`` markers.

    Each section spans from its ``-- name:`` line to the line immediately
    before the next ``-- name:`` line (or end-of-file).  The marker line
    itself is **excluded** from the query body.

    Parameters
    ----------
    text:
        Raw contents of a .sql file.
    source:
        Human-readable label used in log messages (typically the file path).

    Returns
    -------
    dict[str, str]
        Mapping of query name to SQL text (stripped of leading/trailing
        whitespace).
    """
    matches = list(_NAME_PATTERN.finditer(text))
    if not matches:
        logger.debug("no_named_queries_in_file", source=source)
        return {}

    queries: dict[str, str] = {}
    for idx, match in enumerate(matches):
        name: str = match.group("name")
        body_start: int = match.end()
        body_end: int = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body: str = text[body_start:body_end].strip()

        if not body:
            logger.warning(
                "empty_sql_query",
                name=name,
                source=source,
            )

        queries[name] = body

    return queries


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------

def create_query_loader(queries_dir: Path | str | None = None) -> SQLQueryLoader:
    """Convenience factory that creates and immediately loads a
    :class:`SQLQueryLoader`.

    Parameters
    ----------
    queries_dir:
        Path to the directory containing .sql files.  Defaults to
        ``src/db/queries`` relative to the current working directory.

    Returns
    -------
    SQLQueryLoader
        A fully loaded instance ready for use.
    """
    if queries_dir is None:
        queries_dir = Path("src/db/queries")
    loader = SQLQueryLoader(Path(queries_dir))
    loader.load_all()
    return loader
