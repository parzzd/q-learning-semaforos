"""Simulación SUMO con observaciones de cinco sensores y transiciones restringidas."""
import csv
import json
from pathlib import Path

import traci
import traci.constants as tc

from .network import DATA, TLS_ID, binary
from .scenario import generate

DURATIONS = (10, 20, 30)
MIN_GREEN = 10
MAX_GREEN = 60
MAX_UNSERVED = 120


class Simulation:
    def __init__(self, layout, directory: Path, seed=1, horizon=600, trace=False):
        self.layout, self.horizon, self.directory = layout, horizon, directory
        route_file, self.annotations = generate(layout, directory, seed, horizon)
        self.log = (directory / "sumo.log").open("w")
        self.label = f"pilot_{id(self)}"
        traci.start([
            binary("sumo"), "--net-file", str(DATA / "pilot.net.xml"),
            "--route-files", str(route_file), "--seed", str(seed),
            "--step-length", "1", "--time-to-teleport", "-1",
            "--collision.action", "warn", "--collision.check-junctions", "true",
            "--no-step-log", "--duration-log.disable", "--xml-validation", "never",
            "--error-log", str(directory / "sumo_errors.log"),
        ], label=self.label, stdout=self.log, verbose=False)
        self.conn = traci.getConnection(self.label)
        self.time = 0
        self.current = None
        self.green_since = 0
        self.last_served = [0] * len(layout.phases)
        self.snapshots = {}
        self.waits = {}
        self.arrived = set()
        self.departed = set()
        self.collisions = set()
        self.teleports = 0
        self.queue_integral = 0
        self.max_queue = 0
        self.trace = [] if trace else None
        self.decisions = []
        self.total_reward = 0
        self.conn.trafficlight.setRedYellowGreenState(TLS_ID, "r" * len(layout.phases[0]))

    def _tick(self):
        self.conn.simulationStep()
        self.time += 1
        departed = self.conn.simulation.getDepartedIDList()
        self.departed.update(departed)
        for vehicle_id in departed:
            self.conn.vehicle.subscribe(vehicle_id, [tc.VAR_LANE_ID, tc.VAR_LANEPOSITION,
                                                     tc.VAR_SPEED, tc.VAR_POSITION])
        self.snapshots = self.conn.vehicle.getAllSubscriptionResults()
        for vehicle_id, values in self.snapshots.items():
            if values.get(tc.VAR_SPEED, 1) < 0.1:
                self.waits[vehicle_id] = self.waits.get(vehicle_id, 0) + 1
        self.arrived.update(self.conn.simulation.getArrivedIDList())
        self.collisions.update(self.conn.simulation.getCollidingVehiclesIDList())
        self.teleports += len(self.conn.simulation.getStartingTeleportIDList())
        observation = self.observe()
        queue = sum(observation["queues"])
        self.queue_integral += queue
        self.max_queue = max(self.max_queue, queue)
        emergency_wait = sum(1 for event in observation["emergencies"] if event["stopped"])
        reward = -(queue + 12 * emergency_wait + 4 * sum(observation["blocked"])) / 50
        self.total_reward += reward
        if self.trace is not None:
            self.trace.append({
                "time_s": self.time, "queues": observation["queues"],
                "turn_queues": observation["turn_queues"],
                "signals": observation["signal_state"],
                "blocked": observation["blocked"],
                "emergencies": observation["emergencies"],
                "vehicles": [{"id": vehicle_id, "xy": list(values[tc.VAR_POSITION]),
                              "type": self.annotations.get(vehicle_id, {}).get("type", "car")}
                             for vehicle_id, values in self.snapshots.items()],
            })
        return reward

    def observe(self):
        queues = [0] * 4
        counts = [0] * 4
        turn_queues = [0] * 4
        lane_queues = {lane: 0 for lane in self.layout.lane_camera}
        emergencies = []
        output_counts = {}
        output_stopped = {}
        entry_counts = {}
        entry_stopped = {}
        queue_tail = {}
        for vehicle_id, values in self.snapshots.items():
            lane = values.get(tc.VAR_LANE_ID, "")
            speed = values.get(tc.VAR_SPEED, 0)
            position = values.get(tc.VAR_LANEPOSITION, 0)
            annotation = self.annotations.get(vehicle_id, {})
            if lane in self.layout.lane_camera:
                camera, offset, length = self.layout.lane_camera[lane]
                distance = offset + length - position
                if distance <= 240:
                    counts[camera] += 1
                    if speed < 0.1:
                        queues[camera] += 1
                        lane_queues[lane] += 1
                        turn_queues[camera] += int(annotation.get("direction") in ("l", "r"))
                    if annotation.get("active"):
                        emergencies.append({"id": vehicle_id, "camera": camera,
                                            "phase": self.layout.camera_phase[camera],
                                            "distance_m": round(distance, 1), "stopped": speed < 0.1})
            if lane in self.layout.output_lanes:
                offset, length = self.layout.output_lanes[lane]
                if offset + position <= 150:
                    output_counts[lane] = output_counts.get(lane, 0) + 1
                    if speed < 0.1:
                        output_stopped[lane] = output_stopped.get(lane, 0) + 1
                if offset == 0 and position <= 60:
                    entry_counts[lane] = entry_counts.get(lane, 0) + 1
                    if speed < 0.1:
                        entry_stopped[lane] = entry_stopped.get(lane, 0) + 1
                        queue_tail[lane] = min(queue_tail.get(lane, 60), position)
        blocked_edges = set()
        for edge in self.layout.outgoing:
            capacity = sum(min(lane.getLength(), 60) / 7.5 for lane in edge.getLanes() if lane.allows("passenger"))
            count = sum(entry_counts.get(lane.getID(), 0) for lane in edge.getLanes())
            stopped = sum(entry_stopped.get(lane.getID(), 0) for lane in edge.getLanes())
            near_entry = any(entry_stopped.get(lane.getID(), 0) >= 2
                             and queue_tail.get(lane.getID(), 60) <= 15 for lane in edge.getLanes())
            if (count >= 0.75 * capacity and stopped >= 2) or near_entry:
                blocked_edges.add(edge.getID())
        blocked = [any(edge in blocked_edges for edge in outputs) for outputs in self.layout.phase_outputs]
        return {"queues": queues, "counts": counts, "turn_queues": turn_queues,
                "lane_queues": lane_queues, "emergencies": emergencies, "blocked": blocked,
                "output_lane_counts": output_counts, "output_lane_stopped": output_stopped,
                "signal_state": self.conn.trafficlight.getRedYellowGreenState(TLS_ID)}

    def next_phase(self, observation):
        available = [phase for phase, blocked in enumerate(observation["blocked"]) if not blocked]
        if not available:
            return None, "salidas_congestionadas"
        # La emergencia activa más próxima que puede salir tiene prioridad.
        emergencies = sorted(observation["emergencies"], key=lambda event: event["distance_m"])
        for event in emergencies:
            if event["phase"] in available:
                if event["phase"] != self.current or self.time - self.green_since < MAX_GREEN:
                    return event["phase"], "emergencia_activa"
        overdue = [phase for phase in available
                   if observation["queues"][self.layout.phase_camera[phase]] > 0
                   and self.time - self.last_served[phase] >= MAX_UNSERVED]
        if overdue:
            return min(overdue, key=lambda phase: self.last_served[phase]), "espera_prolongada"
        start = 0 if self.current is None else (self.current + 1) % len(self.layout.phases)
        cycle = [(start + delta) % len(self.layout.phases) for delta in range(len(self.layout.phases))]
        demanded = [phase for phase in cycle if phase in available
                    and observation["counts"][self.layout.phase_camera[phase]] > 0]
        candidates = demanded or [phase for phase in cycle if phase in available]
        if self.current in candidates and self.time - self.green_since >= MAX_GREEN:
            candidates = [phase for phase in candidates if phase != self.current]
        return (candidates[0], "ciclo") if candidates else (None, "verde_maximo")

    def _advance(self, seconds):
        return sum(self._tick() for _ in range(min(int(seconds), self.horizon - self.time)))

    def apply(self, phase, duration, reason):
        start = self.time
        reward = 0
        if phase != self.current:
            if self.current is not None:
                state = self.layout.phases[self.current]
                self.conn.trafficlight.setRedYellowGreenState(TLS_ID, "".join("y" if c in "Gg" else "r" for c in state))
                reward += self._advance(self.layout.yellow)
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, "r" * len(self.layout.phases[0]))
            reward += self._advance(self.layout.allred)
            # El siguiente acceso sólo recibe verde cuando se despejó el cruce.
            while self.time < self.horizon and any(values.get(tc.VAR_LANE_ID, "").startswith(":" + TLS_ID)
                                                   for values in self.snapshots.values()):
                reward += self._advance(1)
            self.current = None
        # Volvemos a verificar salidas después del despeje: puede cambiar su ocupación.
        if phase is None or self.time >= self.horizon or self.observe()["blocked"][phase]:
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, "r" * len(self.layout.phases[0]))
            self.current = None
            reward += self._advance(3)
        else:
            if self.current != phase:
                self.current = phase
                self.green_since = self.time
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, self.layout.phases[phase])
            target_end = min(self.time + duration, self.green_since + MAX_GREEN, self.horizon)
            while self.time < target_end:
                reward += self._advance(1)
                self.last_served[phase] = self.time
                observation = self.observe()
                active_elsewhere = any(event["phase"] != phase for event in observation["emergencies"])
                if self.time - self.green_since >= MIN_GREEN and (active_elsewhere or observation["blocked"][phase]):
                    break
        self.decisions.append({"time_s": start, "phase": phase, "requested_green_s": duration,
                               "elapsed_s": self.time - start, "reason": reason})
        return reward, self.time - start

    def close(self):
        pending = self.conn.simulation.getPendingVehicles()
        metrics = {
            "horizon_s": self.time, "departed": len(self.departed), "arrived": len(self.arrived),
            "still_in_network": len(self.snapshots), "pending_departures": len(pending),
            "observed_mean_queue_vehicles": round(self.queue_integral / max(self.time, 1), 3),
            "observed_max_queue_vehicles": self.max_queue,
            "network_stopped_vehicle_seconds": sum(self.waits.values()),
            "mean_stopped_s_per_departed_vehicle": round(sum(self.waits.values()) / max(len(self.departed), 1), 3),
            "colliding_vehicle_count": len(self.collisions), "teleports": self.teleports,
            "reward": round(self.total_reward, 3),
            "emergencies": [{"id": vehicle_id, "type": annotation["type"],
                             "stopped_s": self.waits.get(vehicle_id, 0),
                             "departed": vehicle_id in self.departed, "arrived": vehicle_id in self.arrived,
                             "censored_at_horizon": vehicle_id not in self.arrived}
                            for vehicle_id, annotation in self.annotations.items() if annotation.get("active")],
            "data_kind": "synthetic; uncalibrated",
        }
        self.conn.close()
        self.log.close()
        (self.directory / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        with (self.directory / "decisions.csv").open("w", newline="") as stream:
            if self.decisions:
                writer = csv.DictWriter(stream, fieldnames=self.decisions[0].keys())
                writer.writeheader()
                writer.writerows(self.decisions)
        if self.trace is not None:
            (self.directory / "trace.json").write_text(json.dumps(self.trace, ensure_ascii=False))
        return metrics
