"""Comprueba restricciones y datos que afectan al aprendizaje, sin abrir SUMO."""
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from pathlib import Path

import traci.constants as tc

from pilot.network import Layout
from pilot.engine import Simulation
from pilot.learning import Learner, state_key
from pilot.scenario import generate


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = Layout()

    def fake(self):
        simulation = Simulation.__new__(Simulation)
        simulation.layout = self.layout
        simulation.current = None
        simulation.time = 30
        simulation.green_since = 0
        simulation.green_end = 50
        simulation.allocations = [20] * len(self.layout.phases)
        simulation.last_served = [20] * len(self.layout.phases)
        simulation.snapshots = {}
        simulation.annotations = {}
        simulation.waits = {}
        class Lights:
            def getRedYellowGreenState(self, junction):
                return "r" * len(PilotTests.layout.phases[0])
        class Connection:
            trafficlight = Lights()
        simulation.conn = Connection()
        return simulation

    def observation(self, simulation):
        return simulation.observe()

    def test_opposite_through_phases_and_five_cameras(self):
        self.assertEqual(len(self.layout.cameras), 5)
        normals = [p for p in self.layout.phase_meta if p["kind"] == "through"]
        self.assertEqual(len(normals), 2)
        self.assertEqual(sorted(c for p in normals for c in p["cameras"]), [0, 1, 2, 3])
        for meta in normals:
            for camera in meta["cameras"]:
                straight = self.layout.movement_by_id[self.layout.movement_lookup[camera, "s"]]
                self.assertTrue(all(self.layout.phases[meta["id"]][i] == "G" for i in straight["links"]))
                left = self.layout.movement_by_id[self.layout.movement_lookup[camera, "l"]]
                self.assertTrue(all(self.layout.phases[meta["id"]][i] == "r" for i in left["links"]))
        self.assertTrue(all(route["direction"] in ("l", "r", "s") for route in self.layout.routes))

    def test_protected_movements_do_not_conflict_across_approaches(self):
        for state in self.layout.phases:
            links = [i for i, color in enumerate(state) if color == "G"]
            for a in links:
                for b in links:
                    if self.layout.links[a]["camera"] != self.layout.links[b]["camera"]:
                        self.assertFalse(self.layout.node.areFoes(self.layout.links[a]["junction_index"], self.layout.links[b]["junction_index"]))

    def test_parallel_heads_share_assigned_and_remaining_seconds(self):
        simulation = self.fake()
        for meta in self.layout.phase_meta:
            if meta["kind"] != "through":
                continue
            simulation.current = meta["id"]
            simulation.allocations[simulation.current] = 40
            heads = {h["id"]: h for h in simulation.signal_timings(self.layout.phases[simulation.current])}
            for camera in meta["cameras"]:
                head = heads[self.layout.movement_lookup[camera, "s"]]
                self.assertEqual((head["state"], head["assigned_green_s"], head["remaining_s"]), ("G", 40, 20))

    def test_turn_bottleneck_requests_protected_shared_lane_discharge(self):
        simulation = self.fake()
        movement = self.layout.movement_by_id["C1_l"]
        self.assertTrue(movement["shared"])
        lane = movement["lanes"][0]
        length = self.layout.net.getLane(lane).getLength()
        for index in range(4):
            vehicle = f"turn_{index}"
            simulation.annotations[vehicle] = {"direction": "l"}
            simulation.waits[vehicle] = 30
            simulation.snapshots[vehicle] = {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: length - 10 - index * 7.5, tc.VAR_SPEED: 0}
        phase, reason = simulation.next_phase(simulation.observe())
        self.assertEqual(reason, "cuello_de_botella_giro")
        self.assertEqual(self.layout.phase_meta[phase]["cameras"], [0])
        self.assertTrue(all(self.layout.phases[phase][i] == "G" for i in movement["links"]))
        # No se inmoviliza al vehículo de frente que comparte carril con el giro.
        lane_straight = [i for i, link in self.layout.links.items() if link["lane"] == lane and link["direction"] == "s"]
        self.assertTrue(all(self.layout.phases[phase][i] == "G" for i in lane_straight))

    def test_blocked_turn_does_not_close_unrelated_parallel_through_heads(self):
        blocked = set(self.layout.movement_by_id["C1_l"]["outputs"])
        phase = self.layout.camera_phase[0]
        state = self.layout.safe_state(phase, blocked)
        self.assertFalse(self.layout.phase_blocked(phase, blocked))
        for camera in (0, 2):
            self.assertTrue(all(state[i] == "G" for i in self.layout.movement_by_id[f"C{camera + 1}_s"]["links"]))
        self.assertTrue(all(state[i] == "r" for i in self.layout.movement_by_id["C1_l"]["links"]))

    def test_dedicated_turn_lane_has_independent_protected_phase(self):
        # Configuración alternativa: se elimina el movimiento de frente de los
        # carriles izquierdos. No se altera la geometría del piloto publicado.
        layout = Layout()
        left_lanes = {lane for m in layout.movements if m["direction"] == "l" for lane in m["lanes"]}
        connections = [(a, b, i) for a, b, i in layout.tls.getConnections()
                       if not (a.getID() in left_lanes and layout.links[i]["direction"] == "s")]
        with patch.object(layout.tls, "getConnections", return_value=connections):
            layout._build_signal_plan()
        left = layout.movement_by_id["C1_l"]
        self.assertFalse(left["shared"])
        phase = layout.movement_phase[left["id"]]
        state = layout.phases[phase]
        self.assertEqual(layout.phase_meta[phase]["kind"], "turn")
        self.assertTrue(all(state[i] == "G" for i in left["links"]))
        self.assertTrue(all(state[i] == "r" for i in layout.movement_by_id["C1_s"]["links"]))

    def test_inactive_police_does_not_request_priority(self):
        simulation = self.fake()
        lane = next(iter(self.layout.lane_camera))
        simulation.annotations = {"idle_police": {"type": "police_idle", "active": False}}
        simulation.snapshots = {"idle_police": {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: 0, tc.VAR_SPEED: 0}}
        self.assertEqual(simulation.observe()["emergencies"], [])

    def test_vehicle_outside_camera_range_is_not_observed(self):
        simulation = self.fake()
        lane = next(iter(self.layout.lane_camera))
        camera, offset, length = self.layout.lane_camera[lane]
        simulation.snapshots = {"far": {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: -300, tc.VAR_SPEED: 0}}
        self.assertEqual(sum(simulation.observe()["counts"]), 0)

    def test_blocked_output_prevents_emergency_green(self):
        simulation = self.fake()
        observation = simulation.observe()
        observation["phase_queues"] = observation["phase_counts"] = [5] * len(self.layout.phases)
        observation["blocked"][0] = True
        observation["emergencies"] = [{"phases": [0], "movement": "C2_s", "distance_m": 10}]
        phase, reason = simulation.next_phase(observation)
        self.assertNotEqual(phase, 0)
        self.assertNotEqual(reason, "emergencia_activa")
        observation["blocked"] = [True] * len(self.layout.phases)
        self.assertIsNone(simulation.next_phase(observation)[0])

    def test_maximum_green_cannot_continue(self):
        simulation = self.fake()
        simulation.current = 0
        simulation.time = 60
        simulation.green_since = 0
        observation = simulation.observe()
        observation["blocked"] = [False] + [True] * (len(self.layout.phases) - 1)
        self.assertIsNone(simulation.next_phase(observation)[0])

    def test_queue_near_output_entry_blocks_affected_phases(self):
        simulation = self.fake()
        edge = self.layout.outgoing[0]
        lane = edge.getLanes()[0].getID()
        simulation.snapshots = {
            "head": {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: 20, tc.VAR_SPEED: 0},
            "tail": {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: 12.5, tc.VAR_SPEED: 0},
        }
        observation = simulation.observe()
        self.assertTrue(any(observation["blocked"]))
        self.assertFalse(all(observation["blocked"]))

    def test_terminal_learning_does_not_bootstrap(self):
        learner = Learner()
        learner.q["next"] = [1000, 1000, 1000]
        learner.update("current", 1, -20, "next", 20, terminal=True)
        self.assertAlmostEqual(learner.q["current"][1], -3)

    def test_scenario_is_reproducible_and_stop_lanes_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            a, annotations = generate(self.layout, Path(temp) / "a", 7, 600)
            b, _ = generate(self.layout, Path(temp) / "b", 7, 600)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(sum(bool(item.get("active")) for item in annotations.values()), 4)
            for stop in ET.parse(a).getroot().iter("stop"):
                self.layout.net.getLane(stop.attrib["lane"])


if __name__ == "__main__":
    unittest.main()
