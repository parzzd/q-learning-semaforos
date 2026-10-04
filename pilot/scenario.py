from pathlib import Path
import json
import random
import xml.etree.ElementTree as ET


def generate(layout, directory: Path, seed: int, horizon: int):
    """Demanda reproducible, giros, emergencias activas e inactivas y una salida bloqueada."""
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    root = ET.Element("routes")
    for type_id, shape, vclass, color in [
        ("car", "passenger", "passenger", "0.25,0.55,0.85"),
        ("ambulance_active", "emergency", "emergency", "1,0.35,0.2"),
        ("police_active", "police", "emergency", "0.5,0.35,1"),
        ("police_idle", "police", "passenger", "0.5,0.5,0.6"),
    ]:
        ET.SubElement(root, "vType", id=type_id, vClass=vclass, guiShape=shape,
                      color=color, accel="2.6", decel="4.5", sigma="0.5", length="5", minGap="2.5")
    for route in layout.routes:
        ET.SubElement(root, "route", id=route["id"], edges=" ".join(route["edges"]))
    specifications = []
    annotations = {}
    base_rates = [rng.uniform(0.11, 0.20) for _ in range(4)]
    for camera in range(4):
        routes = [route for route in layout.routes if route["camera"] == camera]
        weights = [0.65 if route["direction"] == "s" else 0.175 for route in routes]
        t = 0.0
        serial = 0
        while t < horizon - 60:
            rush = 1.7 if horizon * 0.35 < t < horizon * 0.65 and camera % 2 == seed % 2 else 1.0
            t += rng.expovariate(base_rates[camera] * rush)
            if t >= horizon - 60:
                break
            route = rng.choices(routes, weights=weights)[0]
            vehicle_id = f"car_{camera}_{serial}"
            specifications.append((t, vehicle_id, "car", route["id"], {}))
            annotations[vehicle_id] = {**route, "active": False}
            serial += 1
    events = [(0.16, 0, "police_idle"), (0.27, 1, "ambulance_active"),
              (0.49, 2, "police_active"), (0.71, 0, "ambulance_active"),
              (0.71, 3, "police_active")]
    for index, (fraction, camera, vehicle_type) in enumerate(events):
        candidates = [route for route in layout.routes if route["camera"] == camera]
        route = rng.choice(candidates)
        vehicle_id = f"special_{index}"
        specifications.append((horizon * fraction, vehicle_id, vehicle_type, route["id"], {}))
        annotations[vehicle_id] = {**route, "active": vehicle_type.endswith("_active"), "type": vehicle_type}
    # Un obstáculo temporal en cada carril de una calle receptora genera congestión real en la simulación.
    blocked_edge = layout.outgoing[seed % len(layout.outgoing)]
    for lane in blocked_edge.getLanes():
        if not lane.allows("passenger"):
            continue
        vehicle_id = f"blocker_{lane.getIndex()}"
        specifications.append((horizon * 0.40, vehicle_id, "car", None,
                               {"lane": str(lane.getIndex()), "edge": blocked_edge.getID(),
                                "endPos": str(min(20, blocked_edge.getLength() - 5)), "duration": "75"}))
        annotations[vehicle_id] = {"active": False, "blocker": True}
    for depart, vehicle_id, vehicle_type, route_id, stop in sorted(specifications):
        attributes = dict(id=vehicle_id, type=vehicle_type, depart=f"{depart:.2f}",
                          departLane=stop.get("lane", "best"), departSpeed="0")
        if route_id:
            attributes["route"] = route_id
        vehicle = ET.SubElement(root, "vehicle", **attributes)
        if stop:
            ET.SubElement(vehicle, "route", edges=stop["edge"])
            ET.SubElement(vehicle, "stop", lane=stop["edge"] + "_" + stop["lane"],
                          endPos=stop["endPos"], duration=stop["duration"])
    ET.indent(root)
    path = directory / "demand.rou.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    (directory / "scenario.json").write_text(json.dumps({
        "seed": seed, "horizon_s": horizon, "synthetic": True,
        "base_arrivals_veh_per_s": base_rates,
        "blocked_output_edge": blocked_edge.getID(), "vehicles": annotations,
    }, ensure_ascii=False, indent=2))
    return path, annotations
