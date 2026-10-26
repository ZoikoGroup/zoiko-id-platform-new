"""In-process metrics with cardinality governance (DEC-G5 §2; SRE-001 §4, §28; OPS-08).

Counters and histograms with a fixed label set each, exposed in the Prometheus text format for the
cell's collector to scrape. Label governance is enforced when a metric is defined and on every
observation:
- a label whose name is a per-request or per-subject identifier (tenant, principal, request,
  session, e-mail ...) is refused at definition time: those belong in logs and traces only;
- each metric has a series budget; an observation that would create a series beyond it is folded
  into a single `__overflow__` series and counted in `zid_telemetry_cardinality_overflow_total`,
  so a runaway label never exhausts the process (OPS-10) and the breach is visible and alertable.
"""

from __future__ import annotations

import bisect
import math
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

FORBIDDEN_LABELS = re.compile(
    r"(?i)(^|_)(tenant|principal|subject|user|account|request|correlation|session|trace|span|"
    r"email|phone|ip|token|client|decision|receipt|mandate|grant|operation|incident)(_id|_ref)?$"
)
_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
OVERFLOW = "__overflow__"
LATENCY_BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.06, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)


class CardinalityError(ValueError):
    pass


def check_label_names(labels: Sequence[str]) -> None:
    for name in labels:
        if not _NAME.match(name):
            raise CardinalityError(f"invalid label name {name!r}")
        if FORBIDDEN_LABELS.search(name):
            raise CardinalityError(
                f"label {name!r} is a high-cardinality identifier: logs/traces only"
            )


@dataclass
class _Metric:
    name: str
    help: str
    labels: tuple[str, ...]
    max_series: int
    registry: Registry

    def _key(self, values: Mapping[str, str]) -> tuple[str, ...]:
        if set(values) != set(self.labels):
            raise CardinalityError(f"{self.name} takes labels {self.labels}, got {tuple(values)}")
        return tuple(str(values[k]) for k in self.labels)

    def _admit(self, store: dict[tuple[str, ...], object], key: tuple[str, ...]) -> tuple[str, ...]:
        if key in store or len(store) < self.max_series:
            return key
        self.registry.overflow(self.name)
        return tuple(OVERFLOW for _ in self.labels)


@dataclass
class Counter(_Metric):
    values: dict[tuple[str, ...], float] = field(default_factory=dict)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters only increase")
        with self.registry.lock:
            key = self._admit(self.values, self._key(labels))  # type: ignore[arg-type]
            self.values[key] = self.values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self.values.get(self._key(labels), 0.0)


@dataclass
class Gauge(_Metric):
    """A value that goes up and down (queue age, backlog); the last set value per series."""

    values: dict[tuple[str, ...], float] = field(default_factory=dict)

    def set(self, value: float, **labels: str) -> None:
        with self.registry.lock:
            key = self._admit(self.values, self._key(labels))  # type: ignore[arg-type]
            self.values[key] = float(value)

    def value(self, **labels: str) -> float:
        return self.values.get(self._key(labels), 0.0)


@dataclass
class Histogram(_Metric):
    buckets: tuple[float, ...] = LATENCY_BUCKETS_S
    series: dict[tuple[str, ...], list[float]] = field(
        default_factory=dict
    )  # counts..., sum, count

    def observe(self, value: float, **labels: str) -> None:
        with self.registry.lock:
            key = self._admit(self.series, self._key(labels))  # type: ignore[arg-type]
            s = self.series.setdefault(key, [0.0] * (len(self.buckets) + 2))
            i = bisect.bisect_left(self.buckets, value)
            if i < len(self.buckets):
                s[i] += 1
            s[-2] += value
            s[-1] += 1

    def quantile(self, q: float, **labels: str) -> float | None:
        """Upper bound of the bucket holding the q-quantile (what a recording rule would give)."""
        s = self.series.get(self._key(labels))
        if not s or s[-1] == 0:
            return None
        target, running = q * s[-1], 0.0
        for bound, n in zip(self.buckets, s, strict=False):
            running += n
            if running >= target:
                return bound
        return math.inf


class Registry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.metrics: dict[str, _Metric] = {}
        self.overflows: dict[str, int] = {}

    def _define(
        self, cls: type[_Metric], name: str, help: str, labels: Iterable[str], **kw: object
    ) -> _Metric:
        labels = tuple(labels)
        if not _NAME.match(name):
            raise CardinalityError(f"invalid metric name {name!r}")
        check_label_names(labels)
        with self.lock:
            existing = self.metrics.get(name)
            if existing is not None:
                if not isinstance(existing, cls) or existing.labels != labels:
                    raise CardinalityError(f"{name} already defined differently")
                return existing
            m = cls(name=name, help=help, labels=labels, registry=self, **kw)  # type: ignore[arg-type]
            self.metrics[name] = m
            return m

    def counter(
        self, name: str, help: str, labels: Iterable[str] = (), *, max_series: int = 200
    ) -> Counter:
        m = self._define(Counter, name, help, labels, max_series=max_series)
        assert isinstance(m, Counter)
        return m

    def gauge(
        self, name: str, help: str, labels: Iterable[str] = (), *, max_series: int = 200
    ) -> Gauge:
        m = self._define(Gauge, name, help, labels, max_series=max_series)
        assert isinstance(m, Gauge)
        return m

    def histogram(
        self,
        name: str,
        help: str,
        labels: Iterable[str] = (),
        *,
        buckets: tuple[float, ...] = LATENCY_BUCKETS_S,
        max_series: int = 200,
    ) -> Histogram:
        m = self._define(Histogram, name, help, labels, max_series=max_series, buckets=buckets)
        assert isinstance(m, Histogram)
        return m

    def overflow(self, name: str) -> None:  # called with the lock held
        self.overflows[name] = self.overflows.get(name, 0) + 1

    def exposition(self) -> str:
        """Prometheus text format 0.0.4."""

        def fmt(labels: tuple[str, ...], values: tuple[str, ...], extra: str = "") -> str:
            pairs = [f'{k}="{_escape(v)}"' for k, v in zip(labels, values, strict=True)]
            if extra:
                pairs.append(extra)
            return "{" + ",".join(pairs) + "}" if pairs else ""

        out: list[str] = []
        with self.lock:
            for m in self.metrics.values():
                out.append(f"# HELP {m.name} {m.help}")
                if isinstance(m, Counter):
                    out.append(f"# TYPE {m.name} counter")
                    for key, v in sorted(m.values.items()):
                        out.append(f"{m.name}{fmt(m.labels, key)} {_num(v)}")
                elif isinstance(m, Gauge):
                    out.append(f"# TYPE {m.name} gauge")
                    for key, v in sorted(m.values.items()):
                        out.append(f"{m.name}{fmt(m.labels, key)} {_num(v)}")
                elif isinstance(m, Histogram):
                    out.append(f"# TYPE {m.name} histogram")
                    for key, s in sorted(m.series.items()):
                        running = 0.0
                        for bound, n in zip(m.buckets, s, strict=False):
                            running += n
                            le = 'le="' + _num(bound) + '"'
                            out.append(f"{m.name}_bucket{fmt(m.labels, key, le)} {_num(running)}")
                        inf = 'le="+Inf"'
                        out.append(f"{m.name}_bucket{fmt(m.labels, key, inf)} {_num(s[-1])}")
                        out.append(f"{m.name}_sum{fmt(m.labels, key)} {_num(s[-2])}")
                        out.append(f"{m.name}_count{fmt(m.labels, key)} {_num(s[-1])}")
            out.append(
                "# HELP zid_telemetry_cardinality_overflow_total Observations folded into __overflow__"
            )
            out.append("# TYPE zid_telemetry_cardinality_overflow_total counter")
            for name, n in sorted(self.overflows.items()):
                out.append(f'zid_telemetry_cardinality_overflow_total{{metric="{name}"}} {n}')
        return "\n".join(out) + "\n"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


REGISTRY = Registry()
