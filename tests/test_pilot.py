"""Comprueba restricciones y datos que afectan al aprendizaje, sin abrir SUMO."""
import tempfile
import unittest
import xml.etree.ElementTree as ET
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
        simulation.last_served = [20] * 4
        simulation.snapshots = {}
        simulation.annotations = {}
        class Lights:
            def getRedYellowGreenState(self, junction):
                return "r" * len(PilotTests.layout.phases[0])
        class Connection:
            trafficlight = Lights()
        simulation.conn = Connection()
        return simulation

    def test_single_approach_phases_and_five_cameras(self):
        self.assertEqual(len(self.layout.cameras), 5)
        self.assertEqual(len(self.layout.phases), 4)
        self.assertEqual(sorted(self.layout.phase_camera), [0, 1, 2, 3])
        self.assertTrue(all(route["direction"] in ("l", "r", "s") for route in self.layout.routes))

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
        observation = {"queues": [5] * 4, "counts": [5] * 4,
                       "blocked": [True, False, False, False],
                       "emergencies": [{"phase": 0, "distance_m": 10}]}
        phase, reason = simulation.next_phase(observation)
        self.assertNotEqual(phase, 0)
        self.assertNotEqual(reason, "emergencia_activa")
        observation["blocked"] = [True] * 4
        self.assertIsNone(simulation.next_phase(observation)[0])

    def test_maximum_green_cannot_continue(self):
        simulation = self.fake()
        simulation.current = 0
        simulation.time = 60
        simulation.green_since = 0
        observation = {"queues": [0] * 4, "counts": [0] * 4,
                       "blocked": [False, True, True, True], "emergencies": []}
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
