"""Exporta una reproducción visual autónoma de las observaciones reales de SUMO."""
import csv
import json

from .network import OUTPUTS, ROOT


def export_replay(layout, recordings, path=None):
    road_geometry = []
    for edge in layout.net.getEdges():
        road_geometry.append({"id": edge.getID(), "name": edge.getName(),
                              "shape": edge.getShape(), "lanes": edge.getLaneNumber(),
                              "major": "Prado" in edge.getName() or "Salaverry" in edge.getName()})
    signal_positions = [edge.getLanes()[0].getShape()[-1] for edge in layout.incoming]
    payload = {"center": layout.center, "roads": road_geometry, "cameras": layout.cameras,
               "signal_positions": signal_positions, "phases": layout.phases,
               "phase_cameras": layout.phase_cameras, "phase_meta": layout.phase_meta,
               "movements": layout.movements, "schema": layout.schema, "recordings": {}}
    if hasattr(layout, "inventory"):
        points = [layout.net.getEdge(e).getShape()[0] for e in layout.corridor_edges]
        span = [2 * max(abs(p[i] - layout.center[i]) for p in points) + 250 for i in (0, 1)]
        neighbors = [s for s in layout.inventory["signals"] if s["id"] in layout.focus_signals]
        for i, signal in enumerate(neighbors):
            signal["label"] = f"N{i + 1}"
        payload["expanded"] = {"profile": layout.profile, "scenario_schema": layout.scenario_schema,
            "signals": layout.inventory["signals"], "neighbors": neighbors,
            "corridors": layout.corridors, "map_span": span,
            "corridor_edges": sorted(layout.corridor_edges)}
    for controller, entry in recordings.items():
        directory = entry["directory"]
        with (directory / "decisions.csv").open() as stream:
            decisions = list(csv.DictReader(stream))
        payload["recordings"][controller] = {
            "metrics": entry["metrics"], "decisions": decisions,
            "trace": json.loads((directory / "trace.json").read_text()),
        }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = (ROOT / "pilot" / "replay_template.html").read_text()
    path = path or OUTPUTS / "simulacion.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace("__REPLAY_DATA__", data))
    return path
