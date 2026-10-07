"""Simulación SUMO con observaciones de cinco sensores y transiciones restringidas."""
import csv
import json
from pathlib import Path

import traci
import traci.constants as tc

from .network import TLS_ID, binary
from .scenario import generate

DURATIONS = (10, 20, 30, 40, 50, 60)
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
            binary("sumo"), "--net-file", str(layout.net_file),
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
        self.green_end = 0
        self.stage = "all_red"
        self.stage_end = 0
        self.active_reason = "inicio"
        self.allocations = [20 if p["kind"] == "through" else 10 for p in layout.phase_meta]
        self.last_served = [0] * len(layout.phases)
        self.snapshots = {}
        self.waits = {}
        self.arrived = set()
        self.departed = set()
        self.collisions = set()
        self.teleports = 0
        self.queue_integral = 0
        self.max_queue = 0
        self.turn_queue_integral = 0
        self.max_turn_queue = 0
        self.trace = [] if trace else None
        self.decisions = []
        self.total_reward = 0
        self.network_audit = layout.make_audit() if hasattr(layout, "make_audit") else None
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
        if self.network_audit is not None:
            self.network_audit.tick(self)
        queue = sum(observation["queues"])
        self.queue_integral += queue
        self.max_queue = max(self.max_queue, queue)
        emergency_wait = sum(1 for event in observation["emergencies"] if event["stopped"])
        turn_queue = sum(observation["turn_queues"])
        self.turn_queue_integral += turn_queue
        self.max_turn_queue = max(self.max_turn_queue, turn_queue)
        reward = -(queue + turn_queue + 12 * emergency_wait + 4 * sum(observation["blocked"])) / 50
        self.total_reward += reward
        if self.trace is not None:
            self.trace.append({
                "time_s": self.time, "queues": observation["queues"],
                "turn_queues": observation["turn_queues"],
                "signals": observation["signal_state"],
                "blocked": observation["blocked"],
                "movement_queues": observation["movement_queues"],
                "phase": self.current, "stage": self.stage,
                "reason": self.active_reason,
                "signal_timings": self.signal_timings(observation["signal_state"]),
                "emergencies": observation["emergencies"],
                "vehicles": [{"id": vehicle_id, "xy": list(values[tc.VAR_POSITION]),
                              "type": self.annotations.get(vehicle_id, {}).get("type", "car")}
                             for vehicle_id, values in self.snapshots.items()],
            })
            if self.network_audit is not None:
                self.trace[-1]["neighbor_signals"] = self.network_audit.signal_snapshot(self)
        return reward

    def observe(self):
        queues = [0] * 4
        counts = [0] * 4
        turn_queues = [0] * 4
        movement_queues = {m["id"]: 0 for m in self.layout.movements}
        movement_counts = dict(movement_queues)
        movement_waits = dict(movement_queues)
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
                    movement = self.layout.movement_lookup.get((camera, annotation.get("direction", "s")))
                    counts[camera] += 1
                    if movement:
                        movement_counts[movement] += 1
                    if speed < 0.1:
                        queues[camera] += 1
                        lane_queues[lane] += 1
                        turn_queues[camera] += int(annotation.get("direction") in ("l", "r"))
                        if movement:
                            movement_queues[movement] += 1
                            movement_waits[movement] = max(movement_waits[movement], self.waits.get(vehicle_id, 0))
                    if annotation.get("active"):
                        candidates = [p for p, state in enumerate(self.layout.phases)
                                      if movement and any(state[i] == "G" for i in self.layout.movement_by_id[movement]["links"])]
                        emergencies.append({"id": vehicle_id, "camera": camera,
                                            "phases": candidates, "movement": movement,
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
        blocked = [self.layout.phase_blocked(p, blocked_edges) for p in range(len(self.layout.phases))]
        serviceable = [[m for m in goals if not any(edge in blocked_edges for edge in self.layout.movement_by_id[m]["outputs"])]
                       for goals in self.layout.phase_goals]
        phase_queues = [sum(movement_queues[m] for m in goals) for goals in serviceable]
        phase_counts = [sum(movement_counts[m] for m in goals) for goals in serviceable]
        phase_waits = [max((movement_waits[m] for m in goals), default=0) for goals in serviceable]
        return {"queues": queues, "counts": counts, "turn_queues": turn_queues,
                "movement_queues": movement_queues, "movement_counts": movement_counts,
                "phase_queues": phase_queues, "phase_counts": phase_counts, "phase_waits": phase_waits,
                "lane_queues": lane_queues, "emergencies": emergencies, "blocked": blocked,
                "blocked_outputs": sorted(blocked_edges),
                "output_lane_counts": output_counts, "output_lane_stopped": output_stopped,
                "signal_state": self.conn.trafficlight.getRedYellowGreenState(TLS_ID)}

    def next_phase(self, observation):
        available = [phase for phase, blocked in enumerate(observation["blocked"]) if not blocked]
        if self.current in available and self.time - self.green_since >= MAX_GREEN:
            available.remove(self.current)
        if not available:
            return None, "salidas_congestionadas"
        # La emergencia activa más próxima que puede salir tiene prioridad.
        emergencies = sorted(observation["emergencies"], key=lambda event: event["distance_m"])
        for event in emergencies:
            candidates = [p for p in event["phases"] if p in available
                          and not any(edge in observation["blocked_outputs"] for edge in self.layout.movement_by_id[event["movement"]]["outputs"])]
            if candidates:
                return self.current if self.current in candidates else candidates[0], "emergencia_activa"
        overdue = [phase for phase in available
                   if observation["phase_queues"][phase] > 0
                   and self.time - self.last_served[phase] >= MAX_UNSERVED]
        if overdue:
            return min(overdue, key=lambda phase: self.last_served[phase]), "espera_prolongada"
        bottlenecks = [p for p in available if self.layout.phase_meta[p]["kind"] != "through"
                       and observation["phase_queues"][p] >= 3 and observation["phase_waits"][p] >= 20]
        if bottlenecks:
            return max(bottlenecks, key=lambda p: observation["phase_queues"][p] * 2 + observation["phase_waits"][p] / 10), "cuello_de_botella_giro"
        start = 0 if self.current is None else (self.current + 1) % len(self.layout.phases)
        cycle = [(start + delta) % len(self.layout.phases) for delta in range(len(self.layout.phases))]
        demanded = [phase for phase in cycle if phase in available
                    and observation["phase_counts"][phase] > 0
                    and (self.layout.phase_meta[phase]["kind"] == "through" or observation["phase_queues"][phase] >= 2)]
        def pressure(phase):
            score = observation["phase_queues"][phase] + .3 * observation["phase_counts"][phase] + .05 * observation["phase_waits"][phase]
            return score * (.55 if phase == self.current else 1)
        return (max(demanded, key=pressure), "demanda_movimientos") if demanded else (None, "sin_demanda")

    def signal_timings(self, state):
        result = []
        for movement in self.layout.movements:
            chars = [state[i] for i in movement["links"]]
            signal = "G" if "G" in chars else "g" if "g" in chars else "y" if "y" in chars else "r"
            phase = self.current if signal in "Gg" and self.current is not None else self.layout.movement_phase[movement["id"]]
            result.append({"id": movement["id"], "state": signal,
                           "assigned_green_s": self.allocations[phase],
                           "remaining_s": max(0, self.green_end - self.time) if signal in "Gg" else None,
                           "phase": phase, "shared": movement["shared"]})
        return result

    def _advance(self, seconds):
        return sum(self._tick() for _ in range(min(int(seconds), self.horizon - self.time)))

    def apply(self, phase, duration, reason):
        start = self.time
        reward = 0
        green_start = None
        self.active_reason = reason
        if phase is not None:
            self.allocations[phase] = max(MIN_GREEN, min(MAX_GREEN, int(duration)))
        planned_state = self.layout.safe_state(phase, self.observe()["blocked_outputs"]) if phase is not None else None
        if phase != self.current or (phase is not None and planned_state != self.conn.trafficlight.getRedYellowGreenState(TLS_ID)):
            if self.current is not None:
                state = self.conn.trafficlight.getRedYellowGreenState(TLS_ID)
                self.stage = "yellow"
                self.conn.trafficlight.setRedYellowGreenState(TLS_ID, "".join("y" if c in "Gg" else "r" for c in state))
                reward += self._advance(self.layout.yellow)
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, "r" * len(self.layout.phases[0]))
            self.stage = "all_red"
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
            self.stage = "all_red"
            reward += self._advance(3)
        else:
            if self.current != phase:
                self.current = phase
                self.green_since = self.time
            self.stage = "green"
            green_start = self.time
            self.green_end = min(self.time + duration, self.green_since + MAX_GREEN)
            self.allocations[phase] = self.green_end - self.green_since
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, self.layout.safe_state(phase, self.observe()["blocked_outputs"]))
            while self.time < min(self.green_end, self.horizon):
                reward += self._advance(1)
                self.last_served[phase] = self.time
                observation = self.observe()
                active_elsewhere = any(phase not in event["phases"] for event in observation["emergencies"])
                # Una salida recién bloqueada se cierra mediante amarillo/despeje
                # al terminar el mínimo, nunca habilitando un movimiento nuevo.
                changed_outputs = self.layout.safe_state(phase, observation["blocked_outputs"]) != self.conn.trafficlight.getRedYellowGreenState(TLS_ID)
                if self.time - self.green_since >= MIN_GREEN and (active_elsewhere or observation["blocked"][phase] or changed_outputs):
                    break
        self.decisions.append({"time_s": start, "phase": phase, "requested_green_s": duration,
                               "elapsed_s": self.time - start, "reason": reason,
                               "actual_green_s": 0 if green_start is None else self.time - green_start})
        return reward, self.time - start

    def close(self):
        pending = self.conn.simulation.getPendingVehicles()
        metrics = {
            "horizon_s": self.time, "departed": len(self.departed), "arrived": len(self.arrived),
            "still_in_network": len(self.snapshots), "pending_departures": len(pending),
            "observed_mean_queue_vehicles": round(self.queue_integral / max(self.time, 1), 3),
            "observed_max_queue_vehicles": self.max_queue,
            "observed_mean_turn_queue_vehicles": round(self.turn_queue_integral / max(self.time, 1), 3),
            "observed_max_turn_queue_vehicles": self.max_turn_queue,
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
            "phase_schema": self.layout.schema,
        }
        if self.network_audit is not None:
            metrics.update(self.network_audit.metrics(self))
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
