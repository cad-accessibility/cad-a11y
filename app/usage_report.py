"""What the analytics database says about how the viewer is used.

``build_report`` reads usage.db and returns a ``Report``: a list of sections,
each a short summary, some headline numbers and some charts. ``render_html``
turns one into a self-contained page (inline CSS and SVG, no script, nothing
fetched) and ``report_to_json`` into plain data. ``app.usage_routes`` serves
both behind a password; ``python -m app.usage_report hash`` makes the hash of
that password.

What it reads, and what it cannot
---------------------------------
The database is opened read-only, and every query goes through four TEMP views
(``s``, ``r``, ``e``, ``u``) with a fixed list of columns. A table or column an
older database lacks reads as empty or NULL, so no section needs to know which
version of the schema it is looking at. A column that could identify someone
(``sessions.identifier``, an upload's names and hash) is not in any view, so no
section can show it. Session ids are in the views, to count and join on, and
never become a label.

Labels that come from the browser (section labels, keys, formats) are shown,
escaped and shortened. A model name is shown only when it names a built-in
model; anything else may be an uploaded file's name, and reads "Uploaded model".
"""

from __future__ import annotations

import argparse
import getpass
import html
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .studies import tokens

Value = int | float | str


@dataclass(frozen=True)
class Chart:
    """One chart and the table that always follows it.

    ``rows`` are ``(label, value, *more_values)``. The SVG draws the first
    value; the table shows them all. ``columns`` names every column, the label
    column first, when there is more than one value; otherwise the headers are
    ``(title, value_label)``. An empty chart is shown as its caption alone.
    """

    title: str
    caption: str
    kind: str  # "hbar", "columns" or "table"
    rows: tuple[tuple[Value, ...], ...]
    value_label: str
    columns: tuple[str, ...] | None = None

    @property
    def headers(self) -> tuple[str, ...]:
        return self.columns or (self.title, self.value_label)


@dataclass(frozen=True)
class Section:
    title: str
    summary: str
    charts: tuple[Chart, ...] = ()
    tiles: tuple[tuple[str, Value], ...] = ()


@dataclass(frozen=True)
class Report:
    generated_at: str
    exclude_workshop: bool
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Event:
    session_id: str | None
    event_type: str
    data: dict[str, Any] | None  # None when event_data was not a JSON object


@dataclass(frozen=True)
class Context:
    exclude_workshop: bool
    builtin_names: frozenset[str]
    events: tuple[Event, ...]
    malformed_events: int


# ---------------------------------------------------------------------------
# Reading the database
# ---------------------------------------------------------------------------

# view name -> (table, the columns it exposes)
_VIEWS = {
    "s": ("sessions", ("id", "consent_given", "is_workshop", "created_at")),
    "r": (
        "render_stats",
        (
            "session_id",
            "view",
            "render_mode",
            "depth",
            "zoom",
            "layout_mode",
            "input_source",
            "created_at",
        ),
    ),
    "e": ("page_events", ("session_id", "event_type", "event_data", "created_at")),
    "u": ("uploaded_models", ("session_id", "file_size", "uploaded_at", "deleted_at")),
}

# Every render and every event, as (session_id, created_at).
_ACTIVITY = "SELECT session_id, created_at FROM r UNION ALL SELECT session_id, created_at FROM e"


def _create_views(conn: sqlite3.Connection, exclude_workshop: bool) -> None:
    present = {
        table: {row[1] for row in conn.execute(f"PRAGMA main.table_info({table})")}
        for table, _ in _VIEWS.values()
    }
    if exclude_workshop and "is_workshop" in present["sessions"]:
        workshop_ids = (
            "SELECT id FROM main.sessions WHERE is_workshop = 1 AND id IS NOT NULL"
        )
    else:
        workshop_ids = "SELECT NULL AS id WHERE 0"
    conn.execute(f"CREATE TEMP VIEW w AS {workshop_ids}")
    for view, (table, columns) in _VIEWS.items():
        found = present[table]
        if not found:
            nulls = ", ".join(f"NULL AS {column}" for column in columns)
            conn.execute(f"CREATE TEMP VIEW {view} AS SELECT {nulls} WHERE 0")
            continue
        select = ", ".join(
            column if column in found else f"NULL AS {column}" for column in columns
        )
        key = "id" if view == "s" else "session_id"
        conn.execute(
            f"CREATE TEMP VIEW {view} AS SELECT {select} FROM main.{table}"
            f" WHERE COALESCE({key}, '') NOT IN (SELECT id FROM w)"
        )


def _load_events(conn: sqlite3.Connection) -> tuple[tuple[Event, ...], int]:
    events: list[Event] = []
    malformed = 0
    for session_id, event_type, blob in conn.execute(
        "SELECT session_id, event_type, event_data FROM e"
    ):
        try:
            data = json.loads(blob) if blob else {}
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            malformed += 1
            data = None
        events.append(Event(session_id, str(event_type), data))
    return tuple(events), malformed


def build_report(
    db_path: str | Path,
    *,
    exclude_workshop: bool = False,
    builtin_names: Iterable[str] = (),
) -> Report:
    """Read usage.db at ``db_path`` without writing to it, and summarise it."""
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = Path(db_path)
    if not path.is_file():
        return Report(
            generated_at,
            exclude_workshop,
            (
                Section(
                    "Overview",
                    "No usage has been recorded: there is no usage database yet.",
                ),
            ),
        )
    conn = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    try:
        _create_views(conn, exclude_workshop)
        events, malformed = _load_events(conn)
        ctx = Context(exclude_workshop, frozenset(builtin_names), events, malformed)
        sections = tuple(builder(conn, ctx) for builder in SECTIONS)
    finally:
        conn.close()
    return Report(generated_at, exclude_workshop, sections)


def report_to_json(report: Report) -> dict[str, Any]:
    return asdict(report)


# ---------------------------------------------------------------------------
# Small helpers the sections share
# ---------------------------------------------------------------------------

_NOT_RECORDED = "(not recorded)"
_LABEL_LIMIT = 60


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def _pct(part: float, whole: float) -> str:
    return f"{part / whole:.0%}" if whole else "0%"


def _label(value: Any) -> str:
    """A label from the browser: one line, short, never empty."""
    text = " ".join(str(value).split()) if value is not None else ""
    if not text:
        return _NOT_RECORDED
    return text if len(text) <= _LABEL_LIMIT else text[: _LABEL_LIMIT - 1] + "…"


def _ranked(counts: Counter, limit: int = 10) -> tuple[tuple[Value, ...], ...]:
    """Largest first, the first ``limit`` of them, the rest summed as Other."""
    ranked = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
    rows = [(label, count) for label, count in ranked[:limit]]
    rest = sum(count for _, count in ranked[limit:])
    if rest:
        rows.append(("Other", rest))
    return tuple(rows)


def _lead(rows: tuple[tuple[Value, ...], ...], phrase: str, unit: str) -> str:
    """'Filled is the most used render mode: 412 of 600 renders (69%).'"""
    total = sum(row[1] for row in rows)
    label, count = rows[0][0], rows[0][1]
    return f"{label} is {phrase}: {count:,} of {total:,} {unit} ({_pct(count, total)})."


def _hbar(title: str, counts: Counter, phrase: str, unit: str, empty: str) -> Chart:
    rows = _ranked(counts)
    caption = _lead(rows, phrase, unit) if rows else empty
    return Chart(title, caption, "hbar", rows, unit.capitalize())


def _date(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _datetime(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)[:19])
    except ValueError:
        return None


def _iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _days(first: date, last: date) -> Iterable[date]:
    for offset in range((last - first).days + 1):
        yield first + timedelta(days=offset)


def _bucket(value: float, buckets: tuple[tuple[str, float, float], ...]) -> str:
    return next(label for label, low, high in buckets if low <= value < high)


def _bucket_rows(
    values: Iterable[float], buckets: tuple[tuple[str, float, float], ...]
) -> tuple:
    counts = Counter(_bucket(value, buckets) for value in values)
    return tuple((label, counts[label]) for label, _, _ in buckets)


def _top_bucket_caption(rows: tuple, unit: str, suffix: str = "") -> str:
    """'The most common is 2-5 renders: 30 of 60 active sessions (50%).'"""
    total = sum(row[1] for row in rows)
    label, count = max(rows, key=lambda row: row[1])
    return f"The most common is {label}{suffix}: {count:,} of {total:,} {unit} ({_pct(count, total)})."


# ---------------------------------------------------------------------------
# Sections, in the order the report shows them
# ---------------------------------------------------------------------------


def overview(conn: sqlite3.Connection, ctx: Context) -> Section:
    sessions = _scalar(conn, "SELECT COUNT(*) FROM s")
    consented = _scalar(conn, "SELECT COUNT(*) FROM s WHERE consent_given = 1")
    workshop: Value = (
        "excluded"
        if ctx.exclude_workshop
        else _scalar(conn, "SELECT COUNT(*) FROM s WHERE is_workshop = 1")
    )
    active = _scalar(conn, f"SELECT COUNT(DISTINCT session_id) FROM ({_ACTIVITY})")
    renders = _scalar(conn, "SELECT COUNT(*) FROM r")
    events = len(ctx.events)
    live = _scalar(conn, "SELECT COUNT(*) FROM u WHERE deleted_at IS NULL")
    deleted = _scalar(conn, "SELECT COUNT(*) FROM u WHERE deleted_at IS NOT NULL")
    first, last, days = conn.execute(
        f"SELECT MIN(created_at), MAX(created_at), COUNT(DISTINCT substr(created_at, 1, 10)) FROM ({_ACTIVITY})"
    ).fetchone()
    tiles = (
        ("Sessions", sessions),
        ("Sessions with analytics consent", consented),
        ("Workshop sessions", workshop),
        ("Active sessions", active),
        ("Renders", renders),
        ("Interaction events", events),
        ("Uploads kept", live),
        ("Uploads deleted", deleted),
        ("First activity", str(first)[:10] if first else "none"),
        ("Last activity", str(last)[:10] if last else "none"),
        ("Days with activity", days),
    )
    if not (sessions or renders or events):
        summary = "No usage has been recorded yet."
    else:
        summary = (
            f"{sessions:,} sessions, {active:,} of them active (at least one render or event),"
            f" with {renders:,} renders and {events:,} interaction events."
        )
    if ctx.exclude_workshop:
        summary += " Workshop sessions are excluded."
    return Section("Overview", summary, (), tiles)


def activity_over_time(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "Activity over time"

    def per_day(sql: str) -> dict[date, int]:
        counts: dict[date, int] = defaultdict(int)
        for day, count in conn.execute(sql):
            parsed = _date(day)
            if parsed:
                counts[parsed] += count
        return counts

    renders = per_day("SELECT substr(created_at, 1, 10), COUNT(*) FROM r GROUP BY 1")
    events = per_day("SELECT substr(created_at, 1, 10), COUNT(*) FROM e GROUP BY 1")
    new = per_day("SELECT substr(created_at, 1, 10), COUNT(*) FROM s GROUP BY 1")
    active_pairs = conn.execute(
        f"SELECT DISTINCT substr(created_at, 1, 10), session_id FROM ({_ACTIVITY}) WHERE session_id IS NOT NULL"
    ).fetchall()
    all_days = set(renders) | set(events) | set(new)
    if not all_days:
        return Section(title, "No activity recorded.")

    first, last = min(all_days), max(all_days)
    span = (last - first).days + 1
    weekly = span > 90
    bin_of: Callable[[date], str] = _iso_week if weekly else date.isoformat
    bins = list(dict.fromkeys(bin_of(day) for day in _days(first, last)))
    totals = {name: Counter() for name in ("new", "renders", "events")}
    for name, counts in (("new", new), ("renders", renders), ("events", events)):
        for day, count in counts.items():
            totals[name][bin_of(day)] += count
    active: dict[str, set] = defaultdict(set)
    for day, session_id in active_pairs:
        parsed = _date(day)
        if parsed:
            active[bin_of(parsed)].add(session_id)

    rows = tuple(
        (b, len(active[b]), totals["new"][b], totals["renders"][b], totals["events"][b])
        for b in bins
    )
    unit = "week" if weekly else "day"
    busiest = max(rows, key=lambda row: row[1])
    idle = sum(1 for row in rows if not (row[1] or row[2]))
    caption = (
        f"The busiest {unit} was {busiest[0]}, with {busiest[1]:,} active sessions."
        f" {idle:,} of {len(rows):,} {unit}s had no activity."
    )
    chart = Chart(
        f"Active sessions per {unit}",
        caption,
        "columns",
        rows,
        "Active sessions",
        (
            "Week" if weekly else "Day (UTC)",
            "Active sessions",
            "New sessions",
            "Renders",
            "Interaction events",
        ),
    )
    summary = f"From {first.isoformat()} to {last.isoformat()} (UTC), {span:,} days."
    if weekly:
        summary += " Shown by ISO week, because the span is longer than 90 days."
    return Section(title, summary, (chart,))


_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def when_people_use_it(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "When people use it"
    hours = Counter()
    for hour, count in conn.execute(
        f"SELECT substr(created_at, 12, 2), COUNT(*) FROM ({_ACTIVITY}) GROUP BY 1"
    ):
        if str(hour).isdigit() and 0 <= int(hour) < 24:
            hours[int(hour)] += count
    weekdays = Counter()
    for day, count in conn.execute(
        f"SELECT substr(created_at, 1, 10), COUNT(*) FROM ({_ACTIVITY}) GROUP BY 1"
    ):
        parsed = _date(day)
        if parsed:
            weekdays[_WEEKDAYS[parsed.weekday()]] += count
    total = sum(hours.values())
    if not total:
        return Section(title, "No activity recorded.")

    hour_rows = tuple((f"{hour:02d}:00", hours[hour]) for hour in range(24))
    top_hour = max(hour_rows, key=lambda row: row[1])
    weekday_rows = tuple((name, weekdays[name]) for name in _WEEKDAYS)
    top_day = max(weekday_rows, key=lambda row: row[1])
    unit = "renders and events"
    charts = (
        Chart(
            "Activity by hour of day (UTC)",
            f"The busiest hour is {top_hour[0]} UTC: {top_hour[1]:,} of {total:,} {unit} ({_pct(top_hour[1], total)}).",
            "columns",
            hour_rows,
            "Renders and events",
            ("Hour (UTC)", "Renders and events"),
        ),
        Chart(
            "Activity by weekday (UTC)",
            f"The busiest day is {top_day[0]}: {top_day[1]:,} of {sum(weekdays.values()):,} {unit}"
            f" ({_pct(top_day[1], sum(weekdays.values()))}).",
            "columns",
            weekday_rows,
            "Renders and events",
            ("Weekday", "Renders and events"),
        ),
    )
    return Section(
        title, "Every render and interaction event, by when it happened.", charts
    )


_RENDER_BUCKETS = (
    ("0", 0, 1),
    ("1", 1, 2),
    ("2-5", 2, 6),
    ("6-20", 6, 21),
    ("21-100", 21, 101),
    ("101+", 101, math.inf),
)
_MINUTE_BUCKETS = (
    ("under 1 min", 0, 1),
    ("1-5 min", 1, 5),
    ("5-15 min", 5, 15),
    ("15-60 min", 15, 60),
    ("60+ min", 60, math.inf),
)


def engagement(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "Engagement"
    renders = dict(
        conn.execute(
            "SELECT session_id, COUNT(*) FROM r WHERE session_id IS NOT NULL GROUP BY 1"
        )
    )
    spans = conn.execute(
        f"""SELECT session_id, MIN(created_at), MAX(created_at), COUNT(DISTINCT substr(created_at, 1, 10))
            FROM ({_ACTIVITY}) WHERE session_id IS NOT NULL GROUP BY 1"""
    ).fetchall()
    if not spans:
        return Section(title, "No active sessions recorded.")

    render_counts = [renders.get(session_id, 0) for session_id, *_ in spans]
    minutes = []
    for _, first, last, _ in spans:
        start, end = _datetime(first), _datetime(last)
        minutes.append((end - start).total_seconds() / 60 if start and end else 0.0)
    returning = sum(1 for *_, days in spans if days >= 2)
    render_rows = _bucket_rows(render_counts, _RENDER_BUCKETS)
    minute_rows = _bucket_rows(minutes, _MINUTE_BUCKETS)
    tiles = (
        ("Active sessions", len(spans)),
        (
            "Median renders per active session",
            round(statistics.median(render_counts), 1),
        ),
        ("Median active minutes per session", round(statistics.median(minutes), 1)),
        ("Returning sessions", returning),
    )
    charts = (
        Chart(
            "Renders per active session",
            _top_bucket_caption(render_rows, "active sessions", " renders"),
            "columns",
            render_rows,
            "Sessions",
            ("Renders", "Sessions"),
        ),
        Chart(
            "Active time per session",
            _top_bucket_caption(minute_rows, "active sessions"),
            "columns",
            minute_rows,
            "Sessions",
            ("Time from first to last activity", "Sessions"),
        ),
    )
    summary = (
        f"A session is active once it has a render or an event. {returning:,} of {len(spans):,}"
        f" ({_pct(returning, len(spans))}) were active on two or more days."
    )
    return Section(title, summary, charts, tiles)


_RENDER_CHOICES = (
    ("render_mode", "Render mode"),
    ("view", "View"),
    ("layout_mode", "Layout"),
    ("input_source", "Input source"),
)
_HISTOGRAM_BINS = 10


def _histogram(conn: sqlite3.Connection, column: str, title: str) -> Chart:
    numeric = f"typeof({column}) IN ('integer', 'real')"
    low, high = conn.execute(
        f"SELECT MIN({column}), MAX({column}) FROM r WHERE {numeric}"
    ).fetchone()
    if low is None:
        return Chart(title, f"No {title.lower()} recorded.", "columns", (), "Renders")
    width = (high - low) / _HISTOGRAM_BINS or 1.0
    bins = 1 if high == low else _HISTOGRAM_BINS
    counts = Counter()
    for index, count in conn.execute(
        f"SELECT CAST(({column} - ?) / ? AS INTEGER), COUNT(*) FROM r WHERE {numeric} GROUP BY 1",
        (low, width),
    ):
        counts[min(int(index), bins - 1)] += count

    def edge(i: int) -> str:
        return f"{low + i * width:.3g}"

    rows = tuple(
        (f"{edge(i)} to {edge(i + 1)}" if bins > 1 else f"{low:.3g}", counts[i])
        for i in range(bins)
    )
    top = max(rows, key=lambda row: row[1])
    total = sum(counts.values())
    caption = f"Most renders were at {title.lower()} {top[0]}: {top[1]:,} of {total:,} ({_pct(top[1], total)})."
    return Chart(
        title, caption, "columns", rows, "Renders", (f"{title} range", "Renders")
    )


def rendering_choices(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "Rendering choices"
    total = _scalar(conn, "SELECT COUNT(*) FROM r")
    if not total:
        return Section(title, "No renders recorded.")
    charts = []
    for column, name in _RENDER_CHOICES:
        counts = Counter(
            {
                _label(value): count
                for value, count in conn.execute(
                    f"SELECT {column}, COUNT(*) FROM r GROUP BY 1"
                )
            }
        )
        charts.append(
            _hbar(name, counts, f"the most used {name.lower()}", "renders", "")
        )
    charts.append(_histogram(conn, "depth", "Depth"))
    charts.append(_histogram(conn, "zoom", "Zoom"))
    return Section(title, f"What the {total:,} renders asked for.", tuple(charts))


_DWELL_LIMIT_MS = 6 * 3600 * 1000
_MODIFIERS = (("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift"))


def _shortcut(data: dict[str, Any]) -> str:
    held = [name for flag, name in _MODIFIERS if data.get(flag) is True]
    return "+".join(held + [_label(data.get("key"))])


def _model_label(name: Any, builtin_names: frozenset[str]) -> str:
    if not isinstance(name, str) or not name.strip():
        return _NOT_RECORDED
    name = name.strip()
    return Path(name).stem if name in builtin_names else "Uploaded model"


def interaction(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "Interaction"
    if not ctx.events:
        return Section(title, "No interaction events recorded.")
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in ctx.events:
        if event.data is not None:
            by_type[event.event_type].append(event.data)

    dwell: dict[str, list[float]] = defaultdict(list)
    dropped = 0
    for data in by_type["section_dwell"]:
        duration = data.get("duration_ms")
        if (
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and 0 <= duration <= _DWELL_LIMIT_MS
        ):
            dwell[_label(data.get("section_label") or data.get("section_id"))].append(
                duration / 1000
            )
        else:
            dropped += 1
    dwell_rows = tuple(
        sorted(
            (
                (
                    label,
                    round(sum(seconds) / 60, 1),
                    len(seconds),
                    round(statistics.median(seconds), 1),
                )
                for label, seconds in dwell.items()
            ),
            key=lambda row: (-row[1], row[0]),
        )
    )
    if dwell_rows:
        total_minutes = sum(row[1] for row in dwell_rows)
        top = dwell_rows[0]
        dwell_caption = (
            f"Most time was spent in {top[0]}: {top[1]:,} of {total_minutes:,.1f} minutes"
            f" ({_pct(top[1], total_minutes)})."
        )
    else:
        dwell_caption = "No section dwell times recorded."

    devices: dict[str, Counter] = defaultdict(Counter)
    for data in by_type["device_connect"]:
        devices[_label(data.get("device_type"))][_label(data.get("status"))] += 1
    statuses = sorted({status for counts in devices.values() for status in counts})
    device_rows = tuple(
        (device, *(devices[device][status] for status in statuses))
        for device in sorted(devices, key=lambda d: (-sum(devices[d].values()), d))
    )
    if device_rows:
        device, status = max(
            ((d, s) for d in devices for s in devices[d]),
            key=lambda pair: devices[pair[0]][pair[1]],
        )
        device_caption = (
            f"{sum(sum(c.values()) for c in devices.values()):,} device events across {len(devices):,}"
            f" device types. The most frequent is {device} {status}: {devices[device][status]:,}."
        )
    else:
        device_caption = "No device connections recorded."

    charts = (
        _hbar(
            "Event types",
            Counter(event.event_type for event in ctx.events),
            "the most frequent event type",
            "events",
            "",
        ),
        _hbar(
            "Keyboard shortcuts",
            Counter(_shortcut(data) for data in by_type["keyboard_shortcut"]),
            "the most used shortcut",
            "shortcut presses",
            "No keyboard shortcuts recorded.",
        ),
        Chart(
            "Time in each section",
            dwell_caption,
            "hbar",
            dwell_rows,
            "Total minutes",
            ("Section", "Total minutes", "Visits", "Median seconds"),
        ),
        Chart(
            "Device connections",
            device_caption,
            "table",
            device_rows,
            "Events",
            ("Device", *statuses),
        ),
        _hbar(
            "Exports",
            Counter(_label(data.get("format")) for data in by_type["export"]),
            "the most exported format",
            "exports",
            "No exports recorded.",
        ),
        _hbar(
            "Models selected",
            Counter(
                _model_label(data.get("model_name"), ctx.builtin_names)
                for data in by_type["model_select"]
            ),
            "the most selected model",
            "selections",
            "No model selections recorded.",
        ),
    )
    summary = f"{len(ctx.events):,} interaction events sent by the viewer."
    if dropped:
        summary += f" {dropped:,} section dwell times under 0 or over 6 hours, or not a number, were left out as noise."
    if ctx.malformed_events:
        summary += f" {ctx.malformed_events:,} events had event data that was not a JSON object, and count only by type."
    tiles = (
        ("Interaction events", len(ctx.events)),
        ("Malformed event data", ctx.malformed_events),
    )
    return Section(title, summary, charts, tiles)


def tutorial_funnel(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "Tutorial funnel"
    sessions: dict[str, set] = defaultdict(set)
    lessons: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for event in ctx.events:
        if event.event_type != "tutorial" or event.data is None:
            continue
        action = event.data.get("action")
        sessions[str(action)].add(event.session_id)
        if action in ("lesson_completed", "lesson_skipped", "exited"):
            lessons[_label(event.data.get("lesson"))][action].add(event.session_id)
    if not sessions:
        return Section(title, "No tutorial activity recorded.")

    loaded, completed = len(sessions["loaded"]), len(sessions["completed"])
    finished_one = len(sessions["lesson_completed"])
    funnel = (
        ("Loaded the tutorial", loaded),
        ("Completed a lesson", finished_one),
        ("Completed the tutorial", completed),
    )
    lesson_rows = tuple(
        (
            lesson,
            len(actions["lesson_completed"]),
            len(actions["lesson_skipped"]),
            len(actions["exited"]),
        )
        for lesson, actions in lessons.items()
    )
    skipped = max(lesson_rows, key=lambda row: row[2], default=None)
    if not lesson_rows:
        lesson_caption = "No lesson was completed, skipped or exited."
    elif skipped and skipped[2]:
        lesson_caption = f"The most skipped lesson is {skipped[0]}, skipped in {skipped[2]:,} sessions."
    else:
        lesson_caption = "No lesson was skipped."
    charts = (
        Chart(
            "Tutorial progress",
            f"{completed:,} of {loaded:,} sessions that loaded the tutorial completed it ({_pct(completed, loaded)}).",
            "columns",
            funnel,
            "Sessions",
            ("Step", "Sessions"),
        ),
        Chart(
            "Lessons",
            lesson_caption,
            "table",
            lesson_rows,
            "Sessions",
            ("Lesson", "Completed", "Skipped", "Exited here"),
        ),
    )
    tiles = (
        ("Sessions that loaded the tutorial", loaded),
        ("Sessions that completed the tutorial", completed),
        ("Completion rate", _pct(completed, loaded)),
    )
    return Section(
        title,
        "Sessions, not events: a session that repeats a step counts once.",
        charts,
        tiles,
    )


_SIZE_BUCKETS = (
    ("under 100 KB", 0, 100_000),
    ("100 KB to 1 MB", 100_000, 1_000_000),
    ("1 to 10 MB", 1_000_000, 10_000_000),
    ("10 MB or more", 10_000_000, math.inf),
)


def uploads(conn: sqlite3.Connection, ctx: Context) -> Section:
    title = "Uploads"
    rows = conn.execute("SELECT file_size, uploaded_at, deleted_at FROM u").fetchall()
    if not rows:
        return Section(title, "No uploads recorded.")
    deleted = sum(1 for _, _, gone in rows if gone)
    weeks = Counter()
    days = [day for day in (_date(uploaded) for _, uploaded, _ in rows) if day]
    for day in days:
        weeks[_iso_week(day)] += 1
    week_rows = (
        tuple(
            (week, weeks[week])
            for week in dict.fromkeys(_iso_week(d) for d in _days(min(days), max(days)))
        )
        if days
        else ()
    )
    sizes = [
        size for size, _, _ in rows if isinstance(size, (int, float)) and size >= 0
    ]
    size_rows = _bucket_rows(sizes, _SIZE_BUCKETS) if sizes else ()
    busiest = max(week_rows, key=lambda row: row[1]) if week_rows else None
    charts = (
        Chart(
            "Uploads per week",
            (
                f"The busiest week was {busiest[0]}, with {busiest[1]:,} uploads."
                if busiest
                else "No upload dates recorded."
            ),
            "columns",
            week_rows,
            "Uploads",
            ("ISO week", "Uploads"),
        ),
        Chart(
            "Upload size",
            (
                _top_bucket_caption(size_rows, "uploads")
                if size_rows
                else "No upload sizes recorded."
            ),
            "columns",
            size_rows,
            "Uploads",
            ("File size", "Uploads"),
        ),
    )
    tiles = (
        ("Uploads", len(rows)),
        ("Kept", len(rows) - deleted),
        ("Deleted", deleted),
        ("Share deleted", _pct(deleted, len(rows))),
    )
    summary = f"{len(rows):,} models uploaded, {deleted:,} of them since deleted ({_pct(deleted, len(rows))})."
    return Section(title, summary, charts, tiles)


SECTIONS: tuple[Callable[[sqlite3.Connection, Context], Section], ...] = (
    overview,
    activity_over_time,
    when_people_use_it,
    engagement,
    rendering_choices,
    interaction,
    tutorial_funnel,
    uploads,
)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--muted:#4a4a4a;--bar:#1f5fa8;--line:#9a9a9a;
--link:#0b57d0;--tile:#f2f4f7}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#f0f0f0;--muted:#c8c8c8;--bar:#7fb2ff;
--line:#6a6a6a;--link:#9cc3ff;--tile:#1e2329}}
body{font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg);
margin:0 auto;max-width:62rem;padding:1rem 1.25rem 3rem}
a{color:var(--link)}
a:focus-visible{outline:3px solid var(--link);outline-offset:2px}
.skip{position:absolute;left:-10000px}
.skip:focus{position:static}
.meta,dt{color:var(--muted)}
section{margin-top:2.5rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(12rem,1fr));gap:.5rem;margin:1rem 0}
.tiles div{background:var(--tile);padding:.5rem .75rem;border-radius:4px}
dt{font-size:.9rem}
dd{margin:0;font-size:1.4rem;font-weight:600}
figure{margin:0 0 2rem}
figcaption{margin-bottom:.5rem}
svg{display:block;width:100%;max-width:44rem;height:auto;margin-bottom:.75rem}
svg text{fill:var(--fg);font-size:12px}
svg .bar{fill:var(--bar)}
svg .axis{stroke:var(--line)}
table{border-collapse:collapse}
caption{text-align:left;font-weight:600;padding-bottom:.25rem}
th,td{border-bottom:1px solid var(--line);padding:.2rem .75rem .2rem 0;text-align:left;vertical-align:top}
td{text-align:right;font-variant-numeric:tabular-nums}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.1f}"


def _svg_hbar(chart: Chart) -> str:
    label_width, bar_width, row_height = 200, 360, 26
    peak = max((row[1] for row in chart.rows), default=0) or 1
    parts = []
    for index, row in enumerate(chart.rows):
        y = index * row_height
        width = bar_width * row[1] / peak
        label = str(row[0]) if len(str(row[0])) <= 28 else str(row[0])[:27] + "…"
        parts.append(
            f'<text x="{label_width - 8}" y="{y + 17}" text-anchor="end">{_esc(label)}</text>'
            f'<rect class="bar" x="{label_width}" y="{y + 4}" width="{width:.1f}" height="{row_height - 8}"/>'
            f'<text x="{label_width + width + 6:.1f}" y="{y + 17}">{_number(row[1])}</text>'
        )
    height = len(chart.rows) * row_height
    return (
        f'<svg aria-hidden="true" focusable="false" viewBox="0 0 640 {height}"'
        f' width="640" height="{height}">{"".join(parts)}</svg>'
    )


def _svg_columns(chart: Chart) -> str:
    left, top, plot_height, plot_width = 40, 18, 160, 560
    count = len(chart.rows)
    peak = max((row[1] for row in chart.rows), default=0) or 1
    step = plot_width / count
    # About 7 units per character at 12px, so axis labels never overlap.
    label_width = 7 * max(len(str(row[0])) for row in chart.rows) + 10
    every = math.ceil(label_width / step)
    baseline = top + plot_height
    parts = [
        f'<line class="axis" x1="{left}" y1="{baseline}" x2="{left + plot_width}" y2="{baseline}"/>'
    ]
    for index, row in enumerate(chart.rows):
        x = left + index * step
        height = plot_height * row[1] / peak
        parts.append(
            f'<rect class="bar" x="{x + step * 0.1:.1f}" y="{baseline - height:.1f}"'
            f' width="{step * 0.8:.1f}" height="{height:.1f}"/>'
        )
        if count <= 24:
            parts.append(
                f'<text x="{x + step / 2:.1f}" y="{baseline - height - 4:.1f}"'
                f' text-anchor="middle">{_number(row[1])}</text>'
            )
        if index % every == 0:
            parts.append(
                f'<text x="{x + step / 2:.1f}" y="{baseline + 16}" text-anchor="middle">{_esc(row[0])}</text>'
            )
    height = baseline + 24
    return (
        f'<svg aria-hidden="true" focusable="false" viewBox="0 0 640 {height}"'
        f' width="640" height="{height}">{"".join(parts)}</svg>'
    )


_SVG: dict[str, Callable[[Chart], str]] = {
    "hbar": _svg_hbar,
    "columns": _svg_columns,
    "table": lambda chart: "",
}


def _table(chart: Chart) -> str:
    head = "".join(f'<th scope="col">{_esc(name)}</th>' for name in chart.headers)
    body = "".join(
        f'<tr><th scope="row">{_esc(row[0])}</th>{"".join(f"<td>{_number(v)}</td>" for v in row[1:])}</tr>'
        for row in chart.rows
    )
    return f"<table><caption>{_esc(chart.title)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _chart_html(chart: Chart) -> str:
    if not chart.rows:
        return f"<h3>{_esc(chart.title)}</h3><p>{_esc(chart.caption)}</p>"
    return (
        f"<h3>{_esc(chart.title)}</h3><figure><figcaption>{_esc(chart.caption)}</figcaption>"
        f"{_SVG[chart.kind](chart)}{_table(chart)}</figure>"
    )


def _section_html(index: int, section: Section) -> str:
    tiles = ""
    if section.tiles:
        items = "".join(
            f"<div><dt>{_esc(label)}</dt><dd>{_esc(_number(value))}</dd></div>"
            for label, value in section.tiles
        )
        tiles = f'<dl class="tiles">{items}</dl>'
    charts = "".join(_chart_html(chart) for chart in section.charts)
    return (
        f'<section id="section-{index}" aria-labelledby="section-{index}-title">'
        f'<h2 id="section-{index}-title">{_esc(section.title)}</h2>'
        f"<p>{_esc(section.summary)}</p>{tiles}{charts}</section>"
    )


def render_html(report: Report) -> str:
    """One self-contained page: inline CSS and SVG, no script, nothing fetched."""
    if report.exclude_workshop:
        scope = "Workshop sessions are excluded."
        toggle = '<a href="/usage">Include workshop sessions</a>'
        json_link = "/usage.json?workshop=exclude"
    else:
        scope = "Workshop sessions are included."
        toggle = '<a href="/usage?workshop=exclude">Exclude workshop sessions</a>'
        json_link = "/usage.json"
    contents = "".join(
        f'<li><a href="#section-{index}">{_esc(section.title)}</a></li>'
        for index, section in enumerate(report.sections, 1)
    )
    sections = "".join(
        _section_html(index, section)
        for index, section in enumerate(report.sections, 1)
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        "<title>CAD A11y usage report</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        '<a class="skip" href="#report">Skip to the report</a>\n'
        "<header><h1>CAD A11y usage report</h1>"
        f'<p class="meta">Generated {_esc(report.generated_at)}. All times are UTC. {scope}</p>'
        f'<p>{toggle}. <a href="{json_link}">The same report as JSON</a>.</p></header>\n'
        f'<nav aria-labelledby="contents-title"><h2 id="contents-title">Contents</h2><ol>{contents}</ol></nav>\n'
        f'<main id="report">{sections}</main>\n</body>\n</html>\n'
    )


# ---------------------------------------------------------------------------
# python -m app.usage_report hash
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.usage_report",
        description="Tools for the usage report at /usage.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "hash",
        help="Ask for a password twice and print the hash to set as CAD_A11Y_USAGE_PASSWORD_HASH.",
    )
    parser.parse_args(argv)

    try:
        password = getpass.getpass("Password for /usage: ")
        again = getpass.getpass("The same password again: ") if password else ""
    except (EOFError, KeyboardInterrupt):
        print("\nNo password read; nothing printed.", file=sys.stderr)
        return 1
    if not password:
        print("No password given; nothing printed.", file=sys.stderr)
        return 1
    if again != password:
        print("The two passwords differ; nothing printed.", file=sys.stderr)
        return 1
    print(tokens.hash_token(password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
