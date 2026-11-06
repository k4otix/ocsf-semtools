"""S1: events per second per core through the engine, at 2-20 KB of JSON (`make bench`, not CI).

The corpus is drawn by the property-test strategies: every core class of the newest model-window
version under each of its test profile sets, kept when its JSON falls in a size bucket. Each rate is
the best of five passes over a bucket. The target is for the ingest path, decode + parse: msgspec
reads the bytes and the engine walks the event once.
"""

import platform
import statistics
import sys
import tempfile
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from ocsf_toolkit import ResolvedSchema, build, load, parse, repair
from tests.fixtures.packages import generated_class, import_generated
from tests.property.strategies import WINDOW, fields, profile_sets

TARGET = 1_000  # events per second per core, decode + parse (architecture §11 S1)
VERSION = WINDOW[-1]
BUCKETS = {"~3 KB": (2_000, 4_000), "~12 KB": (10_000, 14_000), "~20 KB": (18_000, 24_000)}
PER_BUCKET = 40
PASSES = 5


@dataclass(frozen=True, slots=True)
class Sample:
    """One corpus event, with what each operation needs to make it again."""

    schema: ResolvedSchema
    key: str
    fields: dict[str, Any]
    event: dict[str, Any]
    raw: bytes
    model: type[msgspec.Struct]


def corpus(package: Any) -> dict[str, list[Sample]]:
    """Draw events until every bucket holds PER_BUCKET of them, or the examples run out."""
    keys = sorted(load(VERSION).classes)
    sets = profile_sets(VERSION)
    found: dict[str, list[Sample]] = {name: [] for name in BUCKETS}

    @settings(
        max_examples=4_000,
        derandomize=True,
        database=None,
        phases=[Phase.generate],
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
    )
    @given(st.data())
    def draw(data: st.DataObject) -> None:
        key = data.draw(st.sampled_from(keys))
        profiles = data.draw(st.sampled_from(sets))
        budget = data.draw(st.sampled_from([12, 60, 150, 250]))
        values = data.draw(fields(VERSION, profiles, key, budget=budget))
        schema = load(VERSION, profiles=profiles)
        event = build((schema, key), **values)
        raw = msgspec.json.encode(event)
        for name, (low, high) in BUCKETS.items():
            if low <= len(raw) < high and len(found[name]) < PER_BUCKET:
                found[name].append(Sample(schema, key, values, event, raw, generated_class(package, key)))

    draw()
    return found


OPERATIONS: dict[str, Callable[[Sample], object]] = {
    "decode (msgspec)": lambda s: msgspec.json.decode(s.raw),
    "parse": lambda s: parse(s.event),
    "decode + parse": lambda s: parse(msgspec.json.decode(s.raw)),
    "decode + parse(as_=)": lambda s: parse(msgspec.json.decode(s.raw), as_=s.model),
    "repair": lambda s: repair(s.event),
    "build": lambda s: build((s.schema, s.key), **s.fields),
    "encode (msgspec)": lambda s: msgspec.json.encode(s.event),
}


def rate(operation: Callable[[Sample], object], samples: list[Sample]) -> float:
    """Return events per second: the best of PASSES passes over the samples."""
    best = float("inf")
    for _ in range(PASSES):
        start = time.perf_counter()
        for sample in samples:
            operation(sample)
        best = min(best, time.perf_counter() - start)
    return len(samples) / best


def main() -> int:
    """Print events per second for each operation and bucket; return 1 if ingest misses TARGET."""
    warnings.simplefilter("ignore", DeprecationWarning)  # the corpus uses deprecated attributes
    with tempfile.TemporaryDirectory() as out:
        package = import_generated(load(VERSION).ir, "bench_models", Path(out))
        buckets = corpus(package)
        print(
            f"OCSF {VERSION}; Python {platform.python_version()}; msgspec {msgspec.__version__}; {platform.machine()}"
        )
        for name, samples in buckets.items():
            sizes = [len(s.raw) for s in samples]
            print(
                f"{name}: {len(samples)} events, median {statistics.median(sizes) / 1000:.1f} KB"
                if sizes
                else f"{name}: none"
            )
        names = [name for name, samples in buckets.items() if samples]
        print(f"\n{'events/s':<22}" + "".join(f"{name:>10}" for name in names))
        ingest: list[float] = []
        for label, operation in OPERATIONS.items():
            rates = [rate(operation, buckets[name]) for name in names]
            if label == "decode + parse":
                ingest = rates
            print(f"{label:<22}" + "".join(f"{r:>10,.0f}" for r in rates))
    worst = min(ingest, default=0.0)
    verdict = "meets" if worst >= TARGET and len(names) == len(BUCKETS) else "misses"
    print(f"\ndecode + parse, slowest bucket: {worst:,.0f} events/s; {verdict} the {TARGET:,}/s target")
    return 0 if verdict == "meets" else 1


if __name__ == "__main__":
    sys.exit(main())
