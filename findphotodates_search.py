"""Fast, queryable search indexes for findphotodates TSV inventories."""

from __future__ import annotations

import csv
import datetime as _datetime
import re
import shlex
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
SEARCH_FIELDS = (
    "filepath",
    "date_taken",
    "size_bytes",
    "mtime_ns",
    "gps_lat",
    "gps_lon",
    "location",
    "content_hash",
)


class SearchQueryError(ValueError):
    """A query could not be understood."""


@dataclass(frozen=True)
class SearchResult:
    source_path: str
    filepath: str
    date_taken: str
    size_bytes: int
    mtime_ns: int
    gps_lat: str
    gps_lon: str
    location: str
    content_hash: str


def normalize_photo_day(value):
    """Return a user-entered calendar day as YYYY-MM-DD."""
    text = value.strip()
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d")
    for date_format in formats:
        try:
            return _datetime.datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            pass
    raise SearchQueryError(
        "Enter the photo date as YYYY-MM-DD (for example, 2024-06-30)."
    )


_FIELD_ALIASES = {
    "all": "all",
    "any": "all",
    "path": "filepath",
    "file": "filepath",
    "filepath": "filepath",
    "name": "filename",
    "filename": "filename",
    "ext": "extension",
    "extension": "extension",
    "type": "extension",
    "date": "date_taken",
    "taken": "date_taken",
    "date_taken": "date_taken",
    "size": "size_bytes",
    "bytes": "size_bytes",
    "size_bytes": "size_bytes",
    "modified": "mtime_ns",
    "mtime": "mtime_ns",
    "mtime_ns": "mtime_ns",
    "lat": "gps_lat",
    "latitude": "gps_lat",
    "gps_lat": "gps_lat",
    "lon": "gps_lon",
    "longitude": "gps_lon",
    "gps_lon": "gps_lon",
    "place": "location",
    "location": "location",
    "hash": "content_hash",
    "content_hash": "content_hash",
    "list": "source_name",
    "source": "source_name",
}
_TEXT_COLUMNS = {
    "filepath": "f.filepath",
    "filename": "f.filename",
    "extension": "f.extension",
    "date_taken": "f.date_taken",
    "gps_lat": "f.gps_lat",
    "gps_lon": "f.gps_lon",
    "location": "f.location",
    "content_hash": "f.content_hash",
    "source_name": "s.name",
}
_ALL_TEXT_COLUMNS = tuple(_TEXT_COLUMNS.values())
_CLAUSE_RE = re.compile(r"^(-)?([A-Za-z_]+)(<=|>=|!=|=|<|>|:)(.*)$")
_SIZE_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([kmgtpe]?i?b?)?$", re.IGNORECASE
)


def _parse_size(value):
    match = _SIZE_RE.fullmatch(value.strip())
    if not match:
        raise SearchQueryError("Sizes look like 500KB, 12MiB, or 2.5GB.")
    unit = (match.group(2) or "b").lower()
    powers = {
        "": 0,
        "b": 0,
        "k": 1,
        "kb": 1,
        "ki": 1,
        "kib": 1,
        "m": 2,
        "mb": 2,
        "mi": 2,
        "mib": 2,
        "g": 3,
        "gb": 3,
        "gi": 3,
        "gib": 3,
        "t": 4,
        "tb": 4,
        "ti": 4,
        "tib": 4,
        "p": 5,
        "pb": 5,
        "pi": 5,
        "pib": 5,
        "e": 6,
        "eb": 6,
        "ei": 6,
        "eib": 6,
    }
    if unit not in powers:
        raise SearchQueryError("Sizes look like 500KB, 12MiB, or 2.5GB.")
    base = 1024 if "i" in unit else 1000
    try:
        return int(float(match.group(1)) * (base ** powers[unit]))
    except (OverflowError, ValueError) as exc:
        raise SearchQueryError("That size is too large to search for.") from exc


def _parse_timestamp(value):
    text = value.strip().replace("Z", "+00:00")
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            number = float(text)
            return int(
                number if number >= 1_000_000_000_000 else number * 1_000_000_000
            )
        parsed = _datetime.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return int(parsed.timestamp() * 1_000_000_000)
    except (ValueError, OverflowError):
        raise SearchQueryError(
            "Modified dates use ISO format, for example 2024-06-30 or 2024-06-30T14:00."
        )


def _like_pattern(value):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if "*" in escaped or "?" in escaped:
        return escaped.replace("*", "%").replace("?", "_")
    return "%" + escaped + "%"


def _split_alternatives(value):
    return [part for part in value.split("|") if part != ""] or [""]


def _text_condition(columns, operator, value, negative=False):
    if operator == "!=":
        equal_sql, params = _text_condition(columns, "=", value)
        return (equal_sql if negative else f"NOT ({equal_sql})"), params
    special = value.lower()
    if special in ("missing", "blank", "empty"):
        sql = "(" + " AND ".join(f"COALESCE({col}, '') = ''" for col in columns) + ")"
        return ("NOT " + sql if negative else sql), []
    if special in ("present", "any", "set"):
        sql = "(" + " OR ".join(f"COALESCE({col}, '') != ''" for col in columns) + ")"
        return ("NOT " + sql if negative else sql), []

    pieces, params = [], []
    for alternative in _split_alternatives(value):
        per_column = []
        for column in columns:
            if operator == "=":
                per_column.append(f"LOWER(COALESCE({column}, '')) = LOWER(?)")
                params.append(alternative)
            else:
                per_column.append(f"COALESCE({column}, '') LIKE ? ESCAPE '\\'")
                params.append(_like_pattern(alternative))
        pieces.append("(" + " OR ".join(per_column) + ")")
    sql = "(" + " OR ".join(pieces) + ")"
    return ("NOT " + sql if negative else sql), params


def _fts_term(value):
    words = re.findall(r"[^\W_]+", value, re.UNICODE)
    if not words:
        return ""
    return " AND ".join('"{}"*'.format(word.replace('"', '""')) for word in words)


def compile_query(query, *, fts_enabled=True):
    """Return (joins, where SQL, parameters, ordering) for a search query.

    Plain words use FTS when it is available.  Field filters always use SQL,
    which gives predictable substring, wildcard, exact, and range semantics.
    """
    try:
        tokens = shlex.split(query)
    except ValueError as exc:
        raise SearchQueryError(
            "Check that every quote in the search has a matching quote."
        ) from exc

    conditions, params, fts_terms = [], [], []
    for token in tokens:
        match = _CLAUSE_RE.match(token)
        if not match:
            negative = token.startswith("-") and len(token) > 1
            value = token[1:] if negative else token
            if not negative and fts_enabled:
                term = _fts_term(value)
                if term:
                    fts_terms.append(term)
                    continue
            sql, values = _text_condition(_ALL_TEXT_COLUMNS, ":", value, negative)
            conditions.append(sql)
            params.extend(values)
            continue

        negative, raw_field, operator, value = (
            bool(match.group(1)),
            match.group(2).lower(),
            match.group(3),
            match.group(4),
        )
        field = _FIELD_ALIASES.get(raw_field)
        if field is None:
            names = (
                "path, name, ext, date, size, modified, lat, lon, location, hash, list"
            )
            raise SearchQueryError(
                f"Unknown field {raw_field!r}. Searchable fields: {names}."
            )
        if value == "":
            raise SearchQueryError(f"Add a value after {raw_field}{operator}.")

        if field == "size_bytes":
            if operator == ":":
                operator = "="
            if operator not in ("=", "!=", "<", "<=", ">", ">="):
                raise SearchQueryError("Size supports =, !=, <, <=, >, and >=.")
            sql = f"f.size_bytes {operator} ?"
            conditions.append(f"NOT ({sql})" if negative else sql)
            params.append(_parse_size(value))
        elif field == "mtime_ns":
            if operator == ":":
                operator = "="
            sql = f"f.mtime_ns {operator} ?"
            conditions.append(f"NOT ({sql})" if negative else sql)
            params.append(_parse_timestamp(value))
        elif field in ("gps_lat", "gps_lon") and operator in ("<", "<=", ">", ">="):
            try:
                number = float(value)
            except ValueError as exc:
                raise SearchQueryError(
                    "Latitude and longitude ranges need a number."
                ) from exc
            sql = f"CAST(f.{field} AS REAL) {operator} ? AND f.{field} != ''"
            conditions.append(f"NOT ({sql})" if negative else "(" + sql + ")")
            params.append(number)
        elif operator in ("<", "<=", ">", ">="):
            if field != "date_taken":
                raise SearchQueryError(
                    "Text fields support :, =, and !=; ranges work with date, size, modified, lat, and lon."
                )
            normalized = value.replace("-", ":")
            sql = f"f.date_taken {operator} ?"
            conditions.append(f"NOT ({sql})" if negative else sql)
            params.append(normalized)
        else:
            if field == "date_taken":
                value = value.replace("-", ":")
                # A calendar day is the useful meaning of date=2024-06-30;
                # inventory values also contain a time, so make it a prefix match.
                if re.fullmatch(r"\d{4}:\d{2}:\d{2}", value):
                    if operator == "!=":
                        negative = not negative
                    if operator in ("=", "!="):
                        operator = ":"
            if field == "extension":
                value = value.lstrip(".")
            columns = _ALL_TEXT_COLUMNS if field == "all" else (_TEXT_COLUMNS[field],)
            sql, values = _text_condition(columns, operator, value, negative)
            conditions.append(sql)
            params.extend(values)

    join = ""
    order = "f.filepath COLLATE NOCASE, s.path COLLATE NOCASE, f.id"
    if fts_terms:
        join = " JOIN files_fts ON files_fts.rowid = f.id"
        conditions.insert(0, "files_fts MATCH ?")
        params.insert(0, " AND ".join(f"({term})" for term in fts_terms))
        order = (
            "bm25(files_fts), f.filepath COLLATE NOCASE, s.path COLLATE NOCASE, f.id"
        )
    return join, (" AND ".join(conditions) if conditions else "1"), params, order


class InventorySearchIndex:
    """Persistent SQLite index that reparses only changed inventory files."""

    def __init__(self, database_path):
        self.database_path = Path(database_path)

    def _connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._prepare(connection)
        return connection

    def _prepare(self, connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            connection.executescript(
                "DROP TABLE IF EXISTS files_fts; DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS sources;"
            )
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL, size_bytes INTEGER NOT NULL, row_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                filepath TEXT NOT NULL, filename TEXT NOT NULL, extension TEXT NOT NULL,
                date_taken TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                gps_lat TEXT NOT NULL, gps_lon TEXT NOT NULL, location TEXT NOT NULL, content_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS files_source ON files(source_id);
            CREATE INDEX IF NOT EXISTS files_size ON files(size_bytes);
            CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime_ns);
            CREATE INDEX IF NOT EXISTS files_date ON files(date_taken);
            CREATE INDEX IF NOT EXISTS files_extension ON files(extension COLLATE NOCASE);
        """)
        had_fts = self._has_fts(connection)
        try:
            connection.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                filepath, date_taken, gps_lat, gps_lon, location, content_hash, source_name
            )""")
        except sqlite3.OperationalError:
            pass
        if not had_fts and self._has_fts(connection):
            connection.execute("""INSERT INTO files_fts(
                rowid, filepath, date_taken, gps_lat, gps_lon, location, content_hash, source_name)
                SELECT f.id, f.filepath, f.date_taken, f.gps_lat, f.gps_lon,
                       f.location, f.content_hash, s.name
                FROM files f JOIN sources s ON s.id=f.source_id""")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()

    @staticmethod
    def _has_fts(connection):
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='files_fts' AND type='table'"
            ).fetchone()
            is not None
        )

    def _remove_source(self, connection, source_id):
        if self._has_fts(connection):
            connection.execute(
                "DELETE FROM files_fts WHERE rowid IN (SELECT id FROM files WHERE source_id=?)",
                (source_id,),
            )
        connection.execute("DELETE FROM files WHERE source_id=?", (source_id,))
        connection.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def refresh(self, inventory_paths, progress=None):
        """Synchronize the index and return (changed lists, total indexed rows)."""
        paths = [Path(path).resolve() for path in inventory_paths]
        signatures = {}
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            signatures[str(path)] = (stat.st_mtime_ns, stat.st_size, path.name)

        changed = 0
        with closing(self._connect()) as connection, connection:
            existing = {
                row[1]: row
                for row in connection.execute(
                    "SELECT id, path, mtime_ns, size_bytes FROM sources"
                )
            }
            for old_path, row in existing.items():
                if old_path not in signatures:
                    self._remove_source(connection, row[0])

            total_lists = len(signatures)
            for list_number, (path_text, signature) in enumerate(signatures.items(), 1):
                old = existing.get(path_text)
                if old and (old[2], old[3]) == signature[:2]:
                    if progress:
                        progress(list_number, total_lists, signature[2], None)
                    continue
                if old:
                    self._remove_source(connection, old[0])
                cursor = connection.execute(
                    "INSERT INTO sources(path, name, mtime_ns, size_bytes) VALUES (?, ?, ?, ?)",
                    (path_text, signature[2], signature[0], signature[1]),
                )
                source_id = cursor.lastrowid
                row_progress = None
                if progress:
                    progress(list_number, total_lists, signature[2], 0)
                    row_progress = lambda n, number=list_number, name=signature[2]: (
                        progress(number, total_lists, name, n)
                    )
                count = self._import_inventory(
                    connection, source_id, Path(path_text), signature[2], row_progress
                )
                connection.execute(
                    "UPDATE sources SET row_count=? WHERE id=?", (count, source_id)
                )
                connection.commit()
                changed += 1
            total = connection.execute(
                "SELECT COALESCE(SUM(row_count), 0) FROM sources"
            ).fetchone()[0]
        return changed, total

    def _import_inventory(self, connection, source_id, path, source_name, progress):
        count, batch = 0, []
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            lines = (line for line in handle if not line.startswith("#"))
            reader = csv.DictReader(lines, delimiter="\t", restval="")
            if not reader.fieldnames or "filepath" not in reader.fieldnames:
                raise SearchQueryError(f"{path.name} is not a valid file-list TSV.")
            for row in reader:
                filepath = row.get("filepath", "") or ""
                filename = re.split(r"[/\\]", filepath)[-1]
                extension = (
                    filename.rsplit(".", 1)[1].lower() if "." in filename else ""
                )
                try:
                    size = int(row.get("size_bytes", "") or 0)
                except ValueError:
                    size = 0
                try:
                    mtime = int(row.get("mtime_ns", "") or 0)
                except ValueError:
                    mtime = 0
                batch.append(
                    (
                        source_id,
                        filepath,
                        filename,
                        extension,
                        row.get("date_taken", "") or "",
                        size,
                        mtime,
                        row.get("gps_lat", "") or "",
                        row.get("gps_lon", "") or "",
                        row.get("location", "") or "",
                        row.get("content_hash", "") or "",
                    )
                )
                if len(batch) >= 2000:
                    connection.executemany(
                        """INSERT INTO files(
                        source_id, filepath, filename, extension, date_taken, size_bytes, mtime_ns,
                        gps_lat, gps_lon, location, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        batch,
                    )
                    count += len(batch)
                    batch.clear()
                    if progress and count % 20000 == 0:
                        progress(count)
            if batch:
                connection.executemany(
                    """INSERT INTO files(
                    source_id, filepath, filename, extension, date_taken, size_bytes, mtime_ns,
                    gps_lat, gps_lon, location, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    batch,
                )
                count += len(batch)
        if self._has_fts(connection):
            connection.execute(
                """INSERT INTO files_fts(
                rowid, filepath, date_taken, gps_lat, gps_lon, location, content_hash, source_name)
                SELECT id, filepath, date_taken, gps_lat, gps_lon, location, content_hash, ?
                FROM files WHERE source_id=?""",
                (source_name, source_id),
            )
        return count

    def search(self, query, *, source_paths=None, limit=500, offset=0):
        with closing(self._connect()) as connection:
            join, where, params, order = compile_query(
                query, fts_enabled=self._has_fts(connection)
            )
            if source_paths:
                normalized = [str(Path(path).resolve()) for path in source_paths]
                marks = ",".join("?" for _ in normalized)
                where += f" AND s.path IN ({marks})"
                params.extend(normalized)
            sql = f"""SELECT s.path, f.filepath, f.date_taken, f.size_bytes, f.mtime_ns,
                            f.gps_lat, f.gps_lon, f.location, f.content_hash
                     FROM files f JOIN sources s ON s.id=f.source_id{join}
                     WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"""
            params.extend((int(limit) + 1, int(offset)))
            rows = connection.execute(sql, params).fetchall()
        more = len(rows) > limit
        return [SearchResult(*row) for row in rows[:limit]], more
