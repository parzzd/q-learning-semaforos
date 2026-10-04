from pathlib import Path
import json
import math
import sys

import sumolib

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
TLS_ID = "JP_SALAVERRY"


def binary(name):
    path = Path(sys.executable).parent / name
    return str(path) if path.exists() else sumolib.checkBinary(name)


def extend(edge, backwards, distance=240):
    """Sigue la misma calzada sin atravesar el cruce central ni cambiar de avenida."""
    result = [edge]
    length = edge.getLength()
    while length < distance:
        node = result[-1].getFromNode() if backwards else result[-1].getToNode()
        if node.getID() == TLS_ID:
            break
        candidates = node.getIncoming() if backwards else node.getOutgoing()
        candidates = [candidate for candidate in candidates
                      if candidate.getName() == edge.getName()
                      and candidate.allows("passenger") and candidate not in result
                      and (candidate.getFromNode() if backwards else candidate.getToNode()).getID() != TLS_ID]
        if not candidates:
            break
        reference = result[-1].getShape()
        dx, dy = reference[-1][0] - reference[0][0], reference[-1][1] - reference[0][1]
        def alignment(candidate):
            shape = candidate.getShape()
            vx, vy = shape[-1][0] - shape[0][0], shape[-1][1] - shape[0][1]
            return (dx * vx + dy * vy) / max(math.hypot(vx, vy) * math.hypot(dx, dy), 1)
        candidate = max(candidates, key=alignment)
        if alignment(candidate) < 0.5:
            break
        # Seguimos solamente conexiones que realmente permiten ese recorrido.
        a, b = (candidate, result[-1]) if backwards else (result[-1], candidate)
        if not a.getConnections(b):
            break
        result.append(candidate)
        length += candidate.getLength()
    return result


class Layout:
    def __init__(self):
        self.net = sumolib.net.readNet(str(DATA / "pilot.net.xml"), withPrograms=True)
        self.node = self.net.getNode(TLS_ID)
        self.center = self.node.getCoord()
        self.tls = self.net.getTLS(TLS_ID)
        cx, cy = self.center
        self.incoming = sorted(self.node.getIncoming(), key=lambda edge:
            math.atan2(edge.getFromNode().getCoord()[1] - cy,
                       edge.getFromNode().getCoord()[0] - cx))
        self.outgoing = list(self.node.getOutgoing())
        assert len(self.incoming) == 4, "Se esperan cuatro accesos en el piloto."
        self.program = next(iter(self.tls.getPrograms().values()))
        self.phases = [phase.state for phase in self.program.getPhases()
                       if "y" not in phase.state and any(s in phase.state for s in "Gg")]
        self.yellow = max(int(phase.duration) for phase in self.program.getPhases() if "y" in phase.state)
        self.allred = 2
        self.inchains = [extend(edge, True) for edge in self.incoming]
        self.outchains = [extend(edge, False, 150) for edge in self.outgoing]
        self.lane_camera = {}
        self.camera_labels = []
        self.cameras = []
        for index, (edge, chain) in enumerate(zip(self.incoming, self.inchains)):
            sx, sy = edge.getFromNode().getCoord()
            compass = ("este" if sx > cx else "oeste") if "Prado" in edge.getName() else ("norte" if sy > cy else "sur")
            self.camera_labels.append(("Javier Prado" if "Prado" in edge.getName() else "Salaverry") + " · " + compass)
            offset = 0.0
            lane_ids = []
            for segment in chain:
                for lane in segment.getLanes():
                    if lane.allows("passenger"):
                        self.lane_camera[lane.getID()] = (index, offset, lane.getLength())
                        lane_ids.append(lane.getID())
                offset += segment.getLength()
            shape = edge.getLanes()[0].getShape()
            px, py = sumolib.geomhelper.positionAtShapeOffset(shape, max(0, edge.getLength() - 35))
            dx, dy = cx - sx, cy - sy
            norm = max(math.hypot(dx, dy), 1)
            visible_links = [link for incoming, outgoing, link in self.tls.getConnections()
                             if incoming.getEdge() == edge]
            self.cameras.append({
                "id": f"C{index + 1}", "label": self.camera_labels[-1],
                "position_xy": [px + 9 * dy / norm, py - 9 * dx / norm],
                "coverage_lanes": lane_ids, "range_m": 240,
                "visible_signal_links": sorted(set(visible_links)),
                "observes": ["colas por carril", "movimientos de giro", "estado semafórico", "emergencia activa"],
                "virtual": True,
            })
        self.output_lanes = {}
        for chain in self.outchains:
            offset = 0.0
            for segment in chain:
                for lane in segment.getLanes():
                    if lane.allows("passenger"):
                        self.output_lanes[lane.getID()] = (offset, lane.getLength())
                offset += segment.getLength()
        self.cameras.append({
            "id": "C5", "label": "Salidas y área del cruce",
            "position_xy": [cx + 25, cy + 22],
            "coverage_lanes": list(self.output_lanes), "range_m": 150,
            "visible_signal_links": list(range(len(self.phases[0]))),
            "observes": ["ocupación de calles receptoras", "estado semafórico"],
            "virtual": True,
            "note": "Cobertura agregada ideal. Una cámara física puede requerir otra ubicación o sensores adicionales.",
        })
        self.phase_links = [[i for i, value in enumerate(state) if value in "Gg"] for state in self.phases]
        self.phase_camera = []
        self.camera_phase = {}
        self.phase_outputs = []
        for phase, links in enumerate(self.phase_links):
            inputs = {a.getEdge().getID() for a, b, link in self.tls.getConnections() if link in links}
            camera = next(i for i, edge in enumerate(self.incoming) if edge.getID() in inputs)
            assert len(inputs) == 1, "Cada fase inicial debe servir un solo acceso."
            self.phase_camera.append(camera)
            self.camera_phase[camera] = phase
            self.phase_outputs.append(sorted({b.getEdge().getID() for a, b, link in self.tls.getConnections()
                if link in links and any(connection.getDirection() != "t"
                    for connection in a.getEdge().getConnections(b.getEdge()))}))
        self.routes = self._routes()

    def _routes(self):
        routes = []
        for camera, incoming in enumerate(self.incoming):
            for outgoing in self.outgoing:
                connections = incoming.getConnections(outgoing)
                if not connections or connections[0].getDirection() == "t":
                    continue
                upstream = list(reversed(self.inchains[camera]))
                downstream = extend(outgoing, False, 240)
                routes.append({
                    "id": f"r{len(routes)}", "camera": camera,
                    "direction": connections[0].getDirection(),
                    "edges": [edge.getID() for edge in upstream + downstream],
                    "output_edge": outgoing.getID(),
                })
        return routes

    def save(self):
        payload = {"cameras": self.cameras, "green_phases": self.phases,
                   "phase_camera": self.phase_camera, "routes": self.routes,
                   "camera_model": "Sensor ideal por carriles: sin imágenes, perspectiva ni oclusiones."}
        (DATA / "cameras.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
