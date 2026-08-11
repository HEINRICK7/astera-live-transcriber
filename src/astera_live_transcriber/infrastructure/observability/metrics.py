from dataclasses import dataclass, field


@dataclass(slots=True)
class PipelineMetrics:
    counters: dict[str, int] = field(default_factory=dict)
    observations: dict[str, list[float]] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe(self, name: str, value: float) -> None:
        self.observations.setdefault(name, []).append(value)

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def snapshot(self) -> dict[str, object]:
        return {
            "counters": dict(self.counters),
            "observations": {name: list(values) for name, values in self.observations.items()},
            "gauges": dict(self.gauges),
        }
