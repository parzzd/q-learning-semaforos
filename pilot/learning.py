"""Q-learning tabular para asignar duración al siguiente verde permitido."""
import json
import random
from pathlib import Path

from .engine import DURATIONS, Simulation


def bin_queue(value):
    return 0 if value < 3 else 1 if value < 9 else 2


def state_key(layout, observation, phase):
    turns = sum(observation["movement_queues"][m] for m in layout.phase_goals[phase]
                if layout.movement_by_id[m]["direction"] in ("l", "r"))
    return ":".join(map(str, [phase, bin_queue(observation["phase_queues"][phase]),
                              bin_queue(sum(observation["queues"])),
                              bin_queue(turns), min(2, observation["phase_waits"][phase] // 30),
                              int(any(phase in event["phases"] for event in observation["emergencies"]))]))


class Learner:
    def __init__(self, seed=42):
        self.q = {}
        self.rng = random.Random(seed)
        self.updates = 0
        self.schema = None

    def select(self, key, epsilon=0):
        values = self.q.get(key)
        if values is None or self.rng.random() < epsilon:
            return self.rng.randrange(len(DURATIONS)) if epsilon else 1
        best = max(values)
        return self.rng.choice([index for index, value in enumerate(values) if value == best])

    def update(self, key, action, reward, next_key, elapsed, terminal):
        values = self.q.setdefault(key, [0.0] * len(DURATIONS))
        # Descuento por tiempo: las acciones duran cantidades diferentes de segundos.
        continuation = 0 if terminal or next_key is None else max(self.q.get(next_key, [0.0] * len(DURATIONS)))
        target = reward + (0.95 ** (elapsed / 20)) * continuation
        values[action] += 0.15 * (target - values[action])
        self.updates += 1

    def save(self, path: Path):
        path.write_text(json.dumps({"algorithm": "tabular Q-learning; semi-Markov timing",
                                    "durations_s": DURATIONS, "updates": self.updates,
                                    "phase_schema": self.schema,
                                    "q": self.q, "scope": "Initial synthetic experiment; no field validation."}, indent=2))

    @classmethod
    def load(cls, path, layout=None):
        payload = json.loads(Path(path).read_text())
        if payload.get("durations_s") != list(DURATIONS) or (layout and payload.get("phase_schema") != layout.schema):
            raise ValueError("El modelo no corresponde a las fases actuales. Ejecuta: python -m pilot.cli train")
        learner = cls()
        learner.q = payload["q"]
        learner.updates = payload["updates"]
        learner.schema = payload.get("phase_schema")
        return learner


def run_episode(layout, directory, seed, horizon, controller="adaptive", learner=None, epsilon=0, train=False, trace=False):
    if learner is not None:
        if learner.schema not in (None, layout.schema):
            raise ValueError("Modelo entrenado para otro plan de movimientos.")
        learner.schema = layout.schema
    simulation = Simulation(layout, directory, seed, horizon, trace)
    try:
        while simulation.time < horizon:
            observation = simulation.observe()
            phase, reason = simulation.next_phase(observation)
            key = state_key(layout, observation, phase) if phase is not None else None
            if controller == "qlearning" and key is not None:
                action = learner.select(key, epsilon)
                duration = DURATIONS[action]
            elif controller == "adaptive" and phase is not None:
                loads = [sum(observation["movement_queues"][m] for m in layout.phase_goals[phase]
                             if layout.movement_by_id[m]["camera"] == camera
                             and not any(edge in observation["blocked_outputs"] for edge in layout.movement_by_id[m]["outputs"]))
                         for camera in layout.phase_cameras[phase]]
                needed = 10 + 2 * max(loads, default=0) + (10 if observation["phase_waits"][phase] >= 45 else 0)
                action = next((i for i, seconds in enumerate(DURATIONS) if seconds >= needed), len(DURATIONS) - 1)
                duration = DURATIONS[action]
            else:
                action, duration = 1, 20
            reward, elapsed = simulation.apply(phase, duration, reason)
            if train and key is not None:
                next_observation = simulation.observe()
                next_phase, _ = simulation.next_phase(next_observation)
                next_key = state_key(layout, next_observation, next_phase) if next_phase is not None else None
                learner.update(key, action, reward, next_key, elapsed, simulation.time >= horizon)
        metrics = simulation.close()
    except BaseException:
        if not simulation.log.closed:
            simulation.conn.close()
            simulation.log.close()
        raise
    metrics.update({"controller": controller, "seed": seed})
    (directory / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics
