"""Corredores de cinco cuadras y auditoría de llegadas desde semáforos previos.

La geometría viene de OSM. La demanda y los planes vecinos son sintéticos.
Este escenario se valida por reglas antes de entrenar un modelo nuevo.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import xml.etree.ElementTree as ET

import traci.constants as tc

from .network import DATA, Layout, TLS_ID

EXPANDED = DATA / "expanded"
BLOCKS = 5
ENTRY_BUFFER_M = 100


def passenger_connections(a, b):
    return [c for c in a.getConnections(b)
            if c.getFromLane().allows("passenger") and c.getToLane().allows("passenger")]


def corridor(first, backwards, require_signal=False):
    """Cuenta calles transversales, no nodos de geometría ni entradas sin nombre."""
    road = first.getName()
    chain, blocks, seen = [first], [], set()
    distance, previous_signal = 0.0, False
    boundary_distance, signal_extra_blocks = None, 0
    reached = False
    for _ in range(160):
        edge = chain[-1]
        distance += edge.getLength()
        node = edge.getFromNode() if backwards else edge.getToNode()
        crossing = sorted({a.getName() for a in node.getIncoming() + node.getOutgoing()
                           if a.allows("passenger") and a.getName() and a.getName() != road})
        new = set(crossing) - seen
        if new:
            blocks.append({"number": len(blocks) + 1, "node": node.getID(),
                           "streets": crossing, "distance_m": round(distance, 1),
                           "xy": list(node.getCoord())})
            seen.update(crossing)
        candidates = node.getIncoming() if backwards else node.getOutgoing()
        connected = []
        for candidate in candidates:
            if candidate in chain or candidate.getName() != road:
                continue
            a, b = (candidate, edge) if backwards else (edge, candidate)
            connections = passenger_connections(a, b)
            if not connections or not any(c.getDirection() == "s" for c in connections):
                continue
            connected.append((candidate, connections))
        if not connected:
            break
        # La dirección de conexión evita escoger la calzada contraria en nodos
        # de pocos centímetros y evita dar vueltas por una avenida con ramales.
        candidate, connections = max(connected, key=lambda item: item[0].getLaneNumber())
        previous_signal |= any(c.getTLSID() and c.getTLSID() != TLS_ID for c in connections)
        ready = len(blocks) >= BLOCKS and (previous_signal or not require_signal)
        if ready and boundary_distance is None:
            boundary_distance = distance
            signal_extra_blocks = len(blocks) - BLOCKS
        chain.append(candidate)
        if boundary_distance is not None and distance + candidate.getLength() - boundary_distance >= ENTRY_BUFFER_M:
            reached = True  # Margen físico: no basta un nodo de pocos centímetros.
            break
    if not reached:
        raise ValueError(f"{road}: no alcanza {BLOCKS} calles y un semáforo previo ({first.getID()}).")
    ordered = list(reversed(chain)) if backwards else chain
    signals = sorted({c.getTLSID() for a, b in zip(ordered, ordered[1:])
                      for c in passenger_connections(a, b) if c.getTLSID() and c.getTLSID() != TLS_ID})
    return chain, {"road": road, "blocks": blocks, "upstream": backwards,
                   "length_m": round(sum(e.getLength() for e in chain), 1),
                   "edges": [e.getID() for e in ordered], "traffic_lights": signals,
                   "entry_buffer_m": round(sum(e.getLength() for e in chain) - boundary_distance, 1),
                   "extra_blocks_for_signal": signal_extra_blocks}


class ExpandedLayout(Layout):
    def __init__(self):
        super().__init__(EXPANDED / "network.net.xml")
        self.profile = "five_blocks_v1"
        self.corridors = []
        approaches, outputs = {}, {}
        for camera, edge in enumerate(self.incoming):
            chain, entry = corridor(edge, True, require_signal=True)
            entry.update({"id": f"C{camera + 1}", "label": self.camera_labels[camera]})
            approaches[camera] = list(reversed(chain))
            self.corridors.append(entry)
        for index, edge in enumerate(self.outgoing):
            chain, entry = corridor(edge, False)
            entry.update({"id": f"exit_{index + 1}", "label": edge.getName() + " · salida"})
            outputs[edge.getID()] = chain
            self.corridors.append(entry)
        for route in self.routes:
            chain = approaches[route["camera"]] + outputs[route["output_edge"]]
            route["edges"] = [edge.getID() for edge in chain]
            before = approaches[route["camera"]]
            route["upstream_signals"] = sorted({c.getTLSID() for a, b in zip(before, before[1:])
                for c in passenger_connections(a, b) if c.getTLSID() and c.getTLSID() != TLS_ID})
        self.corridor_edges = {edge for entry in self.corridors for edge in entry["edges"]}
        self.focus_signals = sorted({s for entry in self.corridors for s in entry["traffic_lights"]})
        self.background_routes = self._background_routes()
        # La cabecera de netconvert incluye fecha y rutas locales; no define la red.
        network_xml = ET.tostring(ET.parse(self.net_file).getroot())
        signature = {"profile": self.profile, "network": hashlib.sha256(network_xml).hexdigest(),
                     "routes": self.routes + self.background_routes, "demand_version": 1}
        self.scenario_schema = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
        self.inventory = signal_inventory(self)

    def _background_routes(self):
        """Tráfico transversal en los cruces del corredor, sin entrar al central."""
        routes, seen = [], set()
        for tls_id in self.focus_signals:
            tls = self.net.getTLS(tls_id)
            for incoming, outgoing, _ in tls.getConnections():
                a, b = incoming.getEdge(), outgoing.getEdge()
                if a.getID() in self.corridor_edges or b.getID() in self.corridor_edges:
                    continue
                if not incoming.allows("passenger") or not outgoing.allows("passenger"):
                    continue
                connections = passenger_connections(a, b)
                if not any(c.getDirection() == "s" for c in connections):
                    continue
                # Extender la misma calle hasta 150 m a ambos lados, sólo por
                # conexiones existentes. No exige cinco cuadras a una calle lateral.
                left, right = self._side_chain(a, True), self._side_chain(b, False)
                edges = [e.getID() for e in reversed(left)] + [e.getID() for e in right]
                if tuple(edges) in seen or any(e in [x.getID() for x in self.incoming] for e in edges):
                    continue
                seen.add(tuple(edges))
                routes.append({"id": f"side_{len(routes)}", "edges": edges,
                               "crossing_tls": tls_id, "background": True,
                               "active": False, "direction": "s"})
        return routes

    @staticmethod
    def _side_chain(first, backwards):
        chain, distance = [first], first.getLength()
        while distance < 150:
            edge = chain[-1]
            node = edge.getFromNode() if backwards else edge.getToNode()
            options = node.getIncoming() if backwards else node.getOutgoing()
            candidates = []
            for candidate in options:
                a, b = (candidate, edge) if backwards else (edge, candidate)
                if candidate not in chain and candidate.getName() == first.getName() and any(
                        c.getDirection() == "s" for c in passenger_connections(a, b)):
                    candidates.append(candidate)
            if not candidates:
                break
            chosen = max(candidates, key=lambda e: e.getLaneNumber())
            chain.append(chosen)
            distance += chosen.getLength()
        return chain

    def generate_demand(self, directory, seed, horizon):
        """Entradas alejadas, pelotones causados por TLS y demanda lateral."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        rng = random.Random(seed)
        root = ET.Element("routes")
        for name, shape, vclass, color in [("car", "passenger", "passenger", "0.25,0.55,0.85"),
                ("ambulance_active", "emergency", "emergency", "1,0.35,0.2"),
                ("police_active", "police", "emergency", "0.5,0.35,1"),
                ("police_idle", "police", "passenger", "0.5,0.5,0.6")]:
            ET.SubElement(root, "vType", id=name, guiShape=shape, vClass=vclass, color=color,
                          accel="2.6", decel="4.5", sigma="0.5", length="5", minGap="2.5")
        for route in self.routes + self.background_routes:
            ET.SubElement(root, "route", id=route["id"], edges=" ".join(route["edges"]))
        specifications, annotations = [], {}
        rates = [rng.uniform(.10, .14) for _ in range(4)]
        cutoff = horizon * .7  # Cola final de 30% para observar recorridos largos.
        for camera, rate in enumerate(rates):
            candidates = [r for r in self.routes if r["camera"] == camera]
            t, serial = rng.expovariate(rate), 0
            while t < cutoff:
                route = rng.choices(candidates, weights=[.7 if r["direction"] == "s" else .15 for r in candidates])[0]
                vehicle = f"corridor_{camera}_{serial}"
                specifications.append((t, vehicle, "car", route["id"]))
                annotations[vehicle] = {**route, "active": False}
                serial += 1
                rush = 1.5 if .25 * horizon < t < .5 * horizon else 1
                t += rng.expovariate(rate * rush)
        for index, route in enumerate(self.background_routes):
            rate = .025
            t, serial = rng.expovariate(rate), 0
            while t < cutoff:
                vehicle = f"lateral_{index}_{serial}"
                specifications.append((t, vehicle, "car", route["id"]))
                annotations[vehicle] = dict(route)
                serial += 1
                t += rng.expovariate(rate)
        for index, (fraction, camera, kind) in enumerate([(.12, 0, "police_idle"),
                (.2, 1, "ambulance_active"), (.35, 2, "police_active"),
                (.48, 0, "ambulance_active"), (.58, 3, "police_active")]):
            # Rutas rectas aseguran comparar la propagación desde el semáforo previo.
            route = next(r for r in self.routes if r["camera"] == camera and r["direction"] == "s")
            vehicle = f"special_{index}"
            specifications.append((horizon * fraction, vehicle, kind, route["id"]))
            annotations[vehicle] = {**route, "active": kind.endswith("_active"), "type": kind}
        for t, vehicle, kind, route in sorted(specifications):
            ET.SubElement(root, "vehicle", id=vehicle, type=kind, route=route,
                          depart=f"{t:.2f}", departLane="best", departSpeed="0")
        ET.indent(root)
        path = directory / "demand.rou.xml"
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        (directory / "scenario.json").write_text(json.dumps({"seed": seed, "horizon_s": horizon,
            "profile": self.profile, "scenario_schema": self.scenario_schema, "synthetic": True,
            "base_arrivals_veh_per_s": rates, "lateral_rate_per_route": .025,
            "departures_until_s": cutoff, "vehicles": annotations,
            "note": "Sin obstáculos inyectados; las colas provienen de los flujos y semáforos."},
            ensure_ascii=False, indent=2))
        return path, annotations

    def make_audit(self):
        return CorridorAudit(self)

    def save(self):
        (EXPANDED / "corridors.json").write_text(json.dumps({"profile": self.profile,
            "scenario_schema": self.scenario_schema, "block_definition": "Cruce con calle transversal con nombre; mínimo cinco por sentido, más margen y TLS previo.",
            "corridors": self.corridors, "routes": self.routes,
            "background_routes": self.background_routes, "cameras": self.cameras,
            "phase_schema": self.schema}, ensure_ascii=False, indent=2))
        (EXPANDED / "signals.json").write_text(json.dumps(self.inventory, ensure_ascii=False, indent=2))


def signal_inventory(layout):
    root = ET.parse(EXPANDED / "area.osm").getroot()
    raw = []
    for node in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in node.findall("tag")}
        if tags.get("highway") == "traffic_signals":
            lonlat = [float(node.get("lon")), float(node.get("lat"))]
            raw.append({"osm_id": node.get("id"), "lonlat": lonlat, "tags": tags,
                        "xy": list(layout.net.convertLonLat2XY(*lonlat))})
    signals = []
    for tls in layout.net.getTrafficLights():
        nodes = {a.getEdge().getToNode() for a, _, _ in tls.getConnections()}
        xy = [sum(n.getCoord()[i] for n in nodes) / len(nodes) for i in (0, 1)]
        roads = sorted({e.getName() for n in nodes for e in n.getIncoming() + n.getOutgoing() if e.getName()})
        positions = [{"xy": list(a.getShape()[-1]), "link": link, "lane": a.getID()}
                     for a, b, link in tls.getConnections()]
        program = next(iter(tls.getPrograms().values()))
        signals.append({"id": tls.getID(), "xy": xy, "lonlat": list(layout.net.convertXY2LonLat(*xy)),
            "roads": roads, "nodes": sorted(n.getID() for n in nodes), "heads": positions,
            "osm_nodes": [], "focus": tls.getID() in layout.focus_signals or tls.getID() == TLS_ID,
            "generated_location": tls.getID().startswith(("GS_", "joinedS_")) or tls.getID() == TLS_ID,
            "program_source": "generated_by_SUMO; not_municipal", "program": [
                {"state": p.state, "duration_s": p.duration} for p in program.getPhases()]})
    # Coordenadas próximas apoyan la agrupación de señales de aproximación de
    # OSM; se conserva la distancia para revisarla, sin afirmar inspección física.
    unassociated = []
    for entry in raw:
        closest = min(signals, key=lambda s: math.dist(entry["xy"], s["xy"]))
        distance = math.dist(entry["xy"], closest["xy"])
        if distance <= 110:
            closest["osm_nodes"].append({"osm_id": entry["osm_id"], "distance_m": round(distance, 1)})
        else:
            unassociated.append(entry["osm_id"])
    for signal in signals:
        signal["presence_source"] = "osm_nearby_tags" if signal["osm_nodes"] else "simulation_inference_only"
    return {"source": json.loads((EXPANDED / "source.json").read_text()),
            "osm_nodes": raw, "osm_nodes_not_associated": unassociated,
            "association_method": "Nearest imported control within 110 m; spatial association, not field validation.",
            "signals": signals}


def validate_structure(layout):
    errors, routes = [], []
    for route in layout.routes + layout.background_routes:
        edges = [layout.net.getEdge(e) for e in route["edges"]]
        missing = [(a.getID(), b.getID()) for a, b in zip(edges, edges[1:]) if not passenger_connections(a, b)]
        if missing:
            errors.append({"route": route["id"], "disconnected": missing})
        if any(not e.allows("passenger") for e in edges):
            errors.append({"route": route["id"], "error": "edge disallows passenger"})
        if not route.get("background") and not route.get("upstream_signals"):
            errors.append({"route": route["id"], "error": "no upstream signal"})
        routes.append({"id": route["id"], "length_m": round(sum(e.getLength() for e in edges), 1),
                       "upstream_signals": route.get("upstream_signals", []), "connected": not missing})
    conflicts = []
    for tls_id in layout.focus_signals:
        tls = layout.net.getTLS(tls_id)
        connections = tls.getConnections()
        for phase, state in enumerate(next(iter(tls.getPrograms().values())).getPhases()):
            protected = [(a, b, i) for a, b, i in connections if state.state[i] == "G"]
            for a, b, i in protected:
                for x, y, j in protected:
                    node = a.getEdge().getToNode()
                    if i >= j or a.getEdge() == x.getEdge() or node != x.getEdge().getToNode():
                        continue
                    c1 = next(c for c in a.getOutgoing() if c.getToLane() == b and c.getTLSID() == tls_id)
                    c2 = next(c for c in x.getOutgoing() if c.getToLane() == y and c.getTLSID() == tls_id)
                    if node.areFoes(c1.getJunctionIndex(), c2.getJunctionIndex()):
                        conflicts.append({"tls": tls_id, "phase": phase, "links": [i, j]})
    errors.extend(conflicts)
    return {"passed": not errors, "errors": errors, "routes": routes,
            "neighbor_protected_phase_conflicts": conflicts,
            "five_blocks_each_approach": all(len(c["blocks"]) >= BLOCKS for c in layout.corridors),
            "central_routes": len(layout.routes), "lateral_routes": len(layout.background_routes),
            "upstream_signals_each_approach": [r["upstream_signals"] for r in layout.routes if r["direction"] == "s"],
            "camera_count": len(layout.cameras)}


class CorridorAudit:
    def __init__(self, layout):
        self.layout = layout
        self.previous = {}
        self.crossed = defaultdict(set)
        self.central = {}
        self.red_waiters = defaultdict(set)
        self.red_wait_seconds = Counter()
        self.arrival_bins = defaultdict(Counter)
        self.upstream_events = []
        self.crossing_events = []
        self.connections = defaultdict(list)
        for edge in layout.net.getEdges():
            for connection_list in edge.getOutgoing().values():
                for c in connection_list:
                    if c.getTLSID():
                        self.connections[c.getFromLane().getID()].append((c.getToLane().getEdge().getID(), c.getTLSID(), c.getTLLinkIndex()))

    def tick(self, simulation):
        for vehicle, values in simulation.snapshots.items():
            lane = values.get(tc.VAR_LANE_ID, "")
            if not lane or lane.startswith(":"):
                continue
            edge = self.layout.net.getLane(lane).getEdge()
            old = self.previous.get(vehicle)
            if old and old[0] != edge.getID():
                crossed = {tls for to_edge, tls, link in self.connections.get(old[1], []) if to_edge == edge.getID()}
                # Algunos segmentos OSM miden menos de un metro. Un paso SUMO
                # puede atravesar varios: reconstruimos sólo el tramo de ruta
                # entre dos posiciones realmente observadas del vehículo.
                route = simulation.annotations.get(vehicle, {}).get("edges", [])
                try:
                    old_index = route.index(old[0])
                    new_index = route.index(edge.getID(), old_index + 1)
                    for i in range(old_index, new_index):
                        a, b = self.layout.net.getEdge(route[i]), self.layout.net.getEdge(route[i + 1])
                        crossed.update(c.getTLSID() for c in passenger_connections(a, b) if c.getTLSID()
                                       and (i != old_index or c.getFromLane().getID() == old[1]))
                except (ValueError, IndexError):
                    pass
                for tls in crossed:
                    if tls == TLS_ID:
                        annotation = simulation.annotations.get(vehicle, {})
                        camera = annotation.get("camera")
                        prior = sorted(self.crossed[vehicle] - {TLS_ID})
                        self.central[vehicle] = {"camera": camera, "prior_signals": prior}
                        self.arrival_bins[camera][simulation.time // 10] += 1
                        self.crossing_events.append({"time_s": simulation.time, "camera": camera,
                                                     "vehicle": vehicle, "prior_signals": prior})
                    else:
                        if tls not in self.crossed[vehicle]:
                            self.upstream_events.append({"time_s": simulation.time, "vehicle": vehicle, "tls": tls})
                    self.crossed[vehicle].add(tls)
            self.previous[vehicle] = edge.getID(), lane
            if values.get(tc.VAR_SPEED, 1) >= .1 or edge.getLength() - values.get(tc.VAR_LANEPOSITION, 0) > 25:
                continue
            annotation = simulation.annotations.get(vehicle, {})
            route = annotation.get("edges", [])
            try:
                next_edge = route[route.index(edge.getID()) + 1]
            except (ValueError, IndexError):
                continue
            for to_edge, tls, link in self.connections.get(lane, []):
                if tls == TLS_ID or to_edge != next_edge:
                    continue
                state = simulation.conn.trafficlight.getRedYellowGreenState(tls)
                if state[link].lower() == "r":
                    self.red_waiters[tls].add(vehicle)
                    self.red_wait_seconds[tls] += 1

    def signal_snapshot(self, simulation):
        return [{"id": tls, "state": simulation.conn.trafficlight.getRedYellowGreenState(tls),
                 "remaining_s": round(max(0, simulation.conn.trafficlight.getNextSwitch(tls) - simulation.time), 1),
                 "phase": simulation.conn.trafficlight.getPhase(tls),
                 "phase_duration_s": simulation.conn.trafficlight.getPhaseDuration(tls)}
                for tls in self.layout.focus_signals]

    def metrics(self, simulation):
        per_camera = []
        for camera in range(4):
            entries = [v for v in self.central.values() if v["camera"] == camera]
            per_camera.append({"camera": f"C{camera + 1}", "central_crossings": len(entries),
                "with_prior_signal": sum(bool(v["prior_signals"]) for v in entries),
                "arrivals_per_10s": {str(k * 10): count for k, count in sorted(self.arrival_bins[camera].items())}})
        return {"scenario_profile": self.layout.profile, "scenario_schema": self.layout.scenario_schema,
            "network_audit": {"central_crossings": len(self.central),
                "central_crossings_after_prior_signal": sum(bool(v["prior_signals"]) for v in self.central.values()),
                "by_camera": per_camera, "upstream_red_waiters": {s: len(v) for s, v in self.red_waiters.items()},
                "upstream_red_stopped_vehicle_seconds": dict(self.red_wait_seconds),
                "crossings_by_tls": dict(Counter(e["tls"] for e in self.upstream_events)),
                "central_events": self.crossing_events},
            "neighbor_programs": "SUMO-generated fixed cycles; no municipal timings; emergency priority only at central junction"}
