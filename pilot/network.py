from pathlib import Path
import json
import hashlib
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
    def __init__(self, net_file=None):
        self.net_file = Path(net_file) if net_file else DATA / "pilot.net.xml"
        self.net = sumolib.net.readNet(str(self.net_file), withPrograms=True)
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
        self._build_signal_plan()
        self.routes = self._routes()

    def _build_signal_plan(self):
        """Agrupa por movimiento sin inventar carriles exclusivos en el mapa."""
        self.links = {}
        lane_directions = {}
        for incoming, outgoing, link in self.tls.getConnections():
            connection = next(c for c in incoming.getOutgoing()
                              if c.getToLane() == outgoing and c.getTLSID() == TLS_ID)
            camera = self.incoming.index(incoming.getEdge())
            self.links[link] = {"camera": camera, "direction": connection.getDirection(),
                                "lane": incoming.getID(), "output": outgoing.getEdge().getID(),
                                "junction_index": connection.getJunctionIndex()}
            if connection.getDirection() != "t":
                lane_directions.setdefault(incoming.getID(), set()).add(connection.getDirection())
        self.movements = []
        self.movement_lookup = {}
        for camera in range(4):
            for direction, label in (("s", "Frente"), ("l", "Giro izquierdo"), ("r", "Giro derecho")):
                links = [i for i, link in self.links.items()
                         if link["camera"] == camera and link["direction"] == direction]
                if not links:
                    continue
                lanes = sorted({self.links[i]["lane"] for i in links})
                movement = {"id": f"C{camera + 1}_{direction}", "camera": camera,
                            "direction": direction, "label": label, "links": sorted(links),
                            "lanes": lanes, "shared": any(len(lane_directions[lane]) > 1 for lane in lanes),
                            "outputs": sorted({self.links[i]["output"] for i in links})}
                self.movements.append(movement)
                self.movement_lookup[camera, direction] = movement["id"]
        self.movement_by_id = {m["id"]: m for m in self.movements}
        self.phases, self.phase_meta, self.phase_goals = [], [], []
        self.camera_phase, self.movement_phase = {}, {}
        size = max(self.links) + 1

        def compatible(links):
            return not any(self.links[a]["camera"] != self.links[b]["camera"]
                           and self.node.areFoes(self.links[a]["junction_index"], self.links[b]["junction_index"])
                           for a in links for b in links if a < b)

        def add(label, kind, cameras, priority, yielding, goals):
            assert compatible(priority), f"Movimientos prioritarios incompatibles: {label}"
            state = ["r"] * size
            for link in priority:
                state[link] = "G"
            for link in yielding:
                state[link] = "g"
            self.phases.append("".join(state))
            self.phase_goals.append(goals)
            self.phase_meta.append({"id": len(self.phases) - 1, "label": label,
                                    "kind": kind, "cameras": cameras, "goal_movements": goals})
            return len(self.phases) - 1

        for avenue in ("Javier Prado", "Salaverry"):
            cameras = [i for i, label in enumerate(self.camera_labels) if avenue in label]
            assert len(cameras) == 2
            groups = [m for m in self.movements if m["camera"] in cameras]
            primary = [m for m in groups if m["direction"] == "s" or (m["direction"] == "r" and m["shared"])]
            phase = add(avenue + " · frente en ambos sentidos", "through", cameras,
                        [i for m in primary for i in m["links"]],
                        [], [m["id"] for m in primary])
            for camera in cameras:
                self.camera_phase[camera] = phase
            for movement in primary:
                self.movement_phase[movement["id"]] = phase
            # Carriles exclusivos: flechas protegidas y duración independiente.
            for direction in ("l", "r"):
                dedicated = [m for m in groups if m["direction"] == direction and not m["shared"]]
                batches = [dedicated] if compatible([i for m in dedicated for i in m["links"]]) else [[m] for m in dedicated]
                for batch in batches:
                    if not batch:
                        continue
                    phase = add(avenue + " · " + batch[0]["label"].lower(), "turn",
                                [m["camera"] for m in batch], [i for m in batch for i in m["links"]], [],
                                [m["id"] for m in batch])
                    for movement in batch:
                        self.movement_phase[movement["id"]] = phase
        # En carriles compartidos se descarga el acceso completo: un vehículo de
        # frente puede preceder al que gira. El sentido opuesto permanece rojo.
        for camera in range(4):
            turns = [m for m in self.movements if m["camera"] == camera and m["direction"] in ("l", "r") and m["shared"]]
            if not turns:
                continue
            phase = add(f"C{camera + 1} · descarga de giros compartidos", "shared_turn", [camera],
                        [i for i, link in self.links.items() if link["camera"] == camera and link["direction"] != "t"], [],
                        [m["id"] for m in turns])
            for movement in turns:
                if movement["direction"] == "l":
                    self.movement_phase[movement["id"]] = phase
        self.phase_cameras = [meta["cameras"] for meta in self.phase_meta]
        self.phase_links = [[i for i, value in enumerate(state) if value in "Gg"] for state in self.phases]
        self.phase_outputs = [sorted({self.links[i]["output"] for i in links}) for links in self.phase_links]
        self.schema = hashlib.sha256(json.dumps({"states": self.phases, "goals": self.phase_goals}, sort_keys=True).encode()).hexdigest()[:16]

    def safe_state(self, phase, blocked_outputs):
        """Cierra sólo movimientos hacia salidas ocupadas, sin añadir verdes."""
        return "".join("r" if self.links.get(i, {}).get("output") in blocked_outputs else value
                       for i, value in enumerate(self.phases[phase]))

    def phase_blocked(self, phase, blocked_outputs):
        goals = [self.movement_by_id[m] for m in self.phase_goals[phase]]
        if self.phase_meta[phase]["kind"] == "through":
            # El frente de los dos sentidos se abre y se cierra conjuntamente.
            return any(output in blocked_outputs for m in goals if m["direction"] == "s" for output in m["outputs"])
        return all(any(output in blocked_outputs for output in m["outputs"]) for m in goals)

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
                   "phase_cameras": self.phase_cameras, "phase_meta": self.phase_meta,
                   "movements": self.movements, "schema": self.schema, "routes": self.routes,
                   "camera_model": "Sensor ideal por carriles: sin imágenes, perspectiva ni oclusiones."}
        (DATA / "cameras.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
