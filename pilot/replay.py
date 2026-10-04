"""Exporta una reproducción visual autónoma de las observaciones reales de SUMO."""
import csv
import json

from .network import OUTPUTS, ROOT


def export_replay(layout, recordings):
    road_geometry = []
    for edge in layout.net.getEdges():
        road_geometry.append({"id": edge.getID(), "name": edge.getName(),
                              "shape": edge.getShape(), "lanes": edge.getLaneNumber(),
                              "major": "Prado" in edge.getName() or "Salaverry" in edge.getName()})
    signal_positions = [edge.getLanes()[0].getShape()[-1] for edge in layout.incoming]
    payload = {"center": layout.center, "roads": road_geometry, "cameras": layout.cameras,
               "signal_positions": signal_positions, "phases": layout.phases,
               "phase_camera": layout.phase_camera, "recordings": {}}
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
    path = OUTPUTS / "simulacion.html"
    path.write_text(template.replace("__REPLAY_DATA__", data))
    return path
