"""Verifica procedencia, continuidad y auditoría del escenario ampliado."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import traci.constants as tc

from pilot.expanded import ExpandedLayout, CorridorAudit, passenger_connections, validate_structure
from pilot.learning import Learner
from pilot.network import OUTPUTS, TLS_ID
from pilot.scenario import generate


class ExpandedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = ExpandedLayout()

    def test_routes_cross_previous_signals_and_at_least_five_blocks(self):
        checks = validate_structure(self.layout)
        self.assertTrue(checks["passed"], checks["errors"])
        self.assertTrue(checks["five_blocks_each_approach"])
        self.assertEqual(checks["camera_count"], 5)
        self.assertEqual(checks["central_routes"], 12)
        self.assertTrue(all(checks["upstream_signals_each_approach"]))
        self.assertGreater(checks["lateral_routes"], 0)
        self.assertTrue(all(c["range_m"] <= 240 for c in self.layout.cameras))
        self.assertTrue(all(c["entry_buffer_m"] >= 100 for c in self.layout.corridors))

    def test_previous_model_is_rejected_even_with_same_central_phases(self):
        old = json.loads((OUTPUTS / "model.json").read_text())
        self.assertEqual(old["phase_schema"], self.layout.schema)
        with self.assertRaisesRegex(ValueError, "otro escenario"):
            Learner.load(OUTPUTS / "model.json", self.layout)

    def test_neighbor_presence_has_osm_evidence_and_generated_program_label(self):
        neighbors = [s for s in self.layout.inventory["signals"] if s["id"] in self.layout.focus_signals]
        self.assertGreaterEqual(len(neighbors), 5)
        for signal in neighbors:
            self.assertTrue(signal["osm_nodes"], signal["id"])
            self.assertEqual(signal["program_source"], "generated_by_SUMO; not_municipal")

    def test_expanded_demand_reproduces_and_starts_before_previous_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            a, annotations = generate(self.layout, Path(temp) / "a", 201, 1200)
            b, other = generate(self.layout, Path(temp) / "b", 201, 1200)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(annotations, other)
            self.assertFalse(any(v.get("blocker") for v in annotations.values()))
            self.assertTrue(any(v.get("background") for v in annotations.values()))
            for v in annotations.values():
                if not v.get("background"):
                    self.assertTrue(v["upstream_signals"])
                    self.assertNotEqual(v["edges"][0], self.layout.incoming[v["camera"]].getID())

    def test_audit_requires_actual_upstream_crossing_not_only_route_metadata(self):
        audit = CorridorAudit(self.layout)
        route = next(r for r in self.layout.routes if r["camera"] == 0 and r["direction"] == "s")
        simulation = SimpleNamespace(annotations={"via_prior": route, "direct": route}, time=1, snapshots={})

        def transition(vehicle, connection):
            incoming, outgoing = connection.getFromLane(), connection.getToLane()
            audit.previous[vehicle] = incoming.getEdge().getID(), incoming.getID()
            simulation.snapshots = {vehicle: {tc.VAR_LANE_ID: outgoing.getID(), tc.VAR_SPEED: 10,
                                              tc.VAR_LANEPOSITION: 1}}
            audit.tick(simulation)
            simulation.time += 1

        edges = [self.layout.net.getEdge(e) for e in route["edges"]]
        prior = next(c for a, b in zip(edges, edges[1:]) for c in passenger_connections(a, b)
                     if c.getTLSID() and c.getTLSID() != TLS_ID)
        central = next(c for a, b in zip(edges, edges[1:]) for c in passenger_connections(a, b)
                       if c.getTLSID() == TLS_ID)
        transition("via_prior", prior)
        transition("via_prior", central)
        transition("direct", central)
        self.assertTrue(audit.central["via_prior"]["prior_signals"])
        self.assertEqual(audit.central["direct"]["prior_signals"], [])

    def test_audit_records_signal_when_one_step_skips_tiny_osm_edges(self):
        route = next(r for r in self.layout.routes if r["camera"] == 3 and r["direction"] == "s")
        edges = [self.layout.net.getEdge(e) for e in route["edges"]]
        i = next(i for i, (a, b) in enumerate(zip(edges, edges[1:]))
                 if any(c.getTLSID() == "263383329" for c in passenger_connections(a, b)))
        self.assertLess(edges[i + 1].getLength(), 1)
        previous, current = edges[i], edges[i + 2]
        connection = passenger_connections(previous, edges[i + 1])[0]
        audit = CorridorAudit(self.layout)
        audit.previous["vehicle"] = previous.getID(), connection.getFromLane().getID()
        simulation = SimpleNamespace(annotations={"vehicle": route}, time=10,
            snapshots={"vehicle": {tc.VAR_LANE_ID: current.getLanes()[0].getID(),
                                   tc.VAR_SPEED: 10, tc.VAR_LANEPOSITION: 1}})
        audit.tick(simulation)
        self.assertIn("263383329", audit.crossed["vehicle"])


if __name__ == "__main__":
    unittest.main()
