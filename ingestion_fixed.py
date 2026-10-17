#!/usr/bin/env python3
"""Telemetry ingestion: NDJSON -> 1-minute tumbling windows in SQLite.

Layout
  * Worker processes (share-nothing) only parse lines and return small tuples.
  * The parent process is the single writer: it de-duplicates (first occurrence
    in file order wins), aggregates, and writes SQLite. No shared mutable state,
    so there is nothing to race on.
  * Chunks are submitted with a bounded in-flight window, so memory does not
    grow with file size.
"""
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

CHUNK_LINES = 5_000
WORKERS = max(1, min(4, os.cpu_count() or 1))
MAX_INFLIGHT = WORKERS * 2
ROLLING_MINUTES = 5

EPOCH_2024 = 1704067200  # 2024-01-01T00:00:00Z in unix seconds
_UTC = timezone.utc
_UNIX0 = datetime(1970, 1, 1, tzinfo=_UTC)
_FRAC = re.compile(r"(\.\d+)")


def iso_to_unix_seconds(s):
    """ISO-8601 string (any offset, 'Z', or naive=UTC) -> integer unix seconds (floored)."""
    s = s.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # older Pythons only accept 3 or 6 fractional digits
        s = _FRAC.sub(lambda m: (m.group(1) + "000000")[:7], s, count=1)
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    delta = dt - _UNIX0
    return delta.days * 86400 + delta.seconds


def event_minute(ts, shift):
    """Return the UTC minute bucket (unix seconds // 60) for an event."""
    if shift is not None and shift % 7 == 0:
        # integer nanoseconds since 2024-01-01 UTC; integer math only, floats lose precision
        if isinstance(ts, bool):
            raise ValueError("bad timestamp")
        if isinstance(ts, str):
            ts = int(ts.strip())
        elif isinstance(ts, float):
            ts = int(ts)
        elif not isinstance(ts, int):
            raise ValueError("bad timestamp")
        secs = EPOCH_2024 + ts // 1_000_000_000
    else:
        if not isinstance(ts, str):
            raise ValueError("bad timestamp")
        secs = iso_to_unix_seconds(ts)
    return secs // 60


def parse_chunk(lines):
    """Worker: parse lines -> ([(event_id, service, minute, latency, is_error)], malformed)."""
    out = []
    bad = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            ev = {k.lower(): v for k, v in raw.items()}
            shift = ev.get("epoch_shift")
            if isinstance(shift, bool):
                shift = None
            elif shift is not None:
                shift = int(shift)
            minute = event_minute(ev["timestamp"], shift)
            service = ev["service"].strip().lower()
            latency = float(ev["latency_ms"])
            if not math.isfinite(latency):
                raise ValueError("non-finite latency")
            level = str(ev["level"]).strip().lower()
            eid = str(ev["event_id"])
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            bad += 1
            continue
        out.append((eid, service, minute, latency, level in ("error", "critical")))
    return out, bad


def read_chunks(path, chunk_lines=CHUNK_LINES):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        chunk = []
        for line in fh:
            chunk.append(line)
            if len(chunk) >= chunk_lines:
                yield chunk
                chunk = []
        if chunk:
            yield chunk


def ordered_bounded_map(pool, fn, iterable, max_inflight):
    """Like pool.map but never holds more than max_inflight chunks, results in order."""
    pending = deque()
    for item in iterable:
        pending.append(pool.submit(fn, item))
        if len(pending) >= max_inflight:
            yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def _kahan(row, i, x):
    """Neumaier compensated add of x into row[i], compensation kept in row[i+1]."""
    t = row[i] + x
    if abs(row[i]) >= abs(x):
        row[i + 1] += (row[i] - t) + x
    else:
        row[i + 1] += (x - t) + row[i]
    row[i] = t


class Accumulator:
    """Only ever touched by the parent process. Sums are compensated so the
    averages do not drift with the number of events."""

    def __init__(self):
        self.buckets = {}         # (service, minute) -> [count, sum, sum_comp, max, errors]
        self.service_totals = {}  # service -> [count, sum, sum_comp, errors]

    def add(self, service, minute, latency, is_error):
        key = (service, minute)
        row = self.buckets.get(key)
        if row is None:
            row = self.buckets[key] = [0, 0.0, 0.0, latency, 0]
        row[0] += 1
        _kahan(row, 1, latency)
        if latency > row[3]:
            row[3] = latency
        if is_error:
            row[4] += 1
        tot = self.service_totals.get(service)
        if tot is None:
            tot = self.service_totals[service] = [0, 0.0, 0.0, 0]
        tot[0] += 1
        _kahan(tot, 1, latency)
        if is_error:
            tot[3] += 1


def fmt_minute(m):
    return datetime.fromtimestamp(m * 60, _UTC).strftime("%Y-%m-%dT%H:%M:00Z")


def rolling_rows(acc):
    by_service = defaultdict(dict)
    for (svc, m), row in acc.buckets.items():
        by_service[svc][m] = row
    for svc in sorted(by_service):
        recent = deque()
        for m in sorted(by_service[svc]):
            cnt, tot, comp, peak, errs = by_service[svc][m]
            tot += comp
            recent.append((m, cnt, tot))
            while recent[0][0] <= m - ROLLING_MINUTES:
                recent.popleft()
            rc = sum(r[1] for r in recent)
            rs = sum(r[2] for r in recent)
            yield (svc, fmt_minute(m), cnt, round(tot / cnt, 3), peak, errs, round(rs / rc, 3))


def write_sqlite(acc, db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE window_stats(
        service TEXT, window_start_utc TEXT, event_count INTEGER, avg_latency_ms REAL,
        max_latency_ms REAL, error_count INTEGER, rolling_avg_latency_5m_ms REAL,
        PRIMARY KEY(service, window_start_utc))""")
    con.executemany("INSERT INTO window_stats VALUES (?,?,?,?,?,?,?)", rolling_rows(acc))
    con.execute("CREATE TABLE service_totals(service TEXT PRIMARY KEY, event_count INTEGER, "
                "avg_latency_ms REAL, error_count INTEGER)")
    con.executemany("INSERT INTO service_totals VALUES (?,?,?,?)",
                    [(s, c, round((t + comp) / c, 3), e) for s, (c, t, comp, e) in sorted(acc.service_totals.items())])
    con.commit()
    con.close()


def main(path, db_path):
    t0 = time.time()
    acc = Accumulator()
    parsed = malformed = dupes = 0

    # Seen-id set lives on disk so memory stays flat no matter how many ids there are.
    tmpdir = tempfile.mkdtemp(prefix="ingest_")
    seen_path = os.path.join(tmpdir, "seen.db")
    seen = sqlite3.connect(seen_path, isolation_level=None)
    seen.execute("PRAGMA journal_mode=OFF")
    seen.execute("PRAGMA synchronous=OFF")
    seen.execute("PRAGMA cache_size=-8000")
    seen.execute("CREATE TABLE seen(id TEXT PRIMARY KEY) WITHOUT ROWID")
    try:
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            results = ordered_bounded_map(pool, parse_chunk, read_chunks(path), MAX_INFLIGHT)
            for records, bad in results:
                malformed += bad
                seen.execute("BEGIN")
                for eid, service, minute, latency, is_error in records:
                    if seen.execute("INSERT OR IGNORE INTO seen VALUES (?)", (eid,)).rowcount == 0:
                        dupes += 1
                        continue
                    acc.add(service, minute, latency, is_error)
                    parsed += 1
                seen.execute("COMMIT")
    finally:
        seen.close()
        try:
            os.remove(seen_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    write_sqlite(acc, db_path)
    print(f"parsed={parsed:,} malformed={malformed:,} duplicates={dupes:,} "
          f"windows={len(acc.buckets):,} elapsed={time.time() - t0:.1f}s -> {db_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: ingestion_fixed.py <input.ndjson> <output.db>")
    main(sys.argv[1], sys.argv[2])
