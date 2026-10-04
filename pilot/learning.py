"""Q-learning tabular para asignar duración al siguiente verde permitido."""
import json
import random
from pathlib import Path

from .engine import DURATIONS, Simulation


def bin_queue(value):
    return 0 if value < 3 else 1 if value < 9 else 2


def state_key(layout, observation, phase):
    camera = layout.phase_camera[phase]
    return ":".join(map(str, [phase, bin_queue(observation["queues"][camera]),
                              bin_queue(sum(observation["queues"])),
                              bin_queue(observation["turn_queues"][camera]),
                              int(any(event["phase"] == phase for event in observation["emergencies"]))]))


class Learner:
    def __init__(self, seed=42):
        self.q = {}
        self.rng = random.Random(seed)
        self.updates = 0

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
                                    "q": self.q, "scope": "Initial synthetic experiment; no field validation."}, indent=2))

    @classmethod
    def load(cls, path):
        payload = json.loads(Path(path).read_text())
        learner = cls()
        learner.q = payload["q"]
        learner.updates = payload["updates"]
        return learner


def run_episode(layout, directory, seed, horizon, controller="adaptive", learner=None, epsilon=0, train=False, trace=False):
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
                camera = layout.phase_camera[phase]
                action = min(bin_queue(observation["queues"][camera]), len(DURATIONS) - 1)
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
