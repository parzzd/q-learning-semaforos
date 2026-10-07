"""Descarga OSM y construye la red de cinco cuadras sin modificar el piloto."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pilot.expanded import EXPANDED, ExpandedLayout, validate_structure
from pilot.network import binary

BBOX = (-77.0665, -12.1075, -77.0420, -12.0825)
URL = "https://www.openstreetmap.org/api/0.6/map?bbox=" + ",".join(map(str, BBOX))


def build(refresh=False):
    EXPANDED.mkdir(parents=True, exist_ok=True)
    osm = EXPANDED / "area.osm"
    if refresh or not osm.exists():
        temporary = osm.with_suffix(".download")
        subprocess.run(["curl", "--fail", "--location", "--max-time", "90", "--retry", "2",
                        "--output", str(temporary), URL], check=True)
        root = ET.parse(temporary).getroot()
        temporary.replace(osm)
        (EXPANDED / "source.json").write_text(json.dumps({"source_url": URL,
            "bbox_lonlat": BBOX, "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "© OpenStreetMap contributors — ODbL 1.0",
            "source_license_url": "https://www.openstreetmap.org/copyright", "bytes": osm.stat().st_size,
            "osm_signal_nodes": sum(any(t.get("k") == "highway" and t.get("v") == "traffic_signals"
                                         for t in n.findall("tag")) for n in root.findall("node"))},
            ensure_ascii=False, indent=2) + "\n")
    command = [binary("netconvert"), "--osm-files", str(osm),
        "--node-files", str(ROOT / "data/join.nod.xml"),
        "--output-file", str(EXPANDED / "network.net.xml"),
        "--geometry.remove", "--junctions.join", "--tls.guess-signals", "--tls.discard-simple",
        "--tls.join", "--tls.join-exclude", "JP_SALAVERRY", "--keep-edges.by-vclass", "passenger",
        "--keep-edges.in-geo-boundary=" + ",".join(map(str, BBOX)), "--remove-edges.isolated",
        "--no-turnarounds", "--output.street-names", "--tls.allred.time", "2",
        "--tls.left-green.time", "8", "--tls.minor-left.max-speed", "0", "--osm.turn-lanes", "true"]
    result = subprocess.run(command, capture_output=True, text=True)
    (EXPANDED / "network_build.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stderr)
    layout = ExpandedLayout()
    layout.save()
    checks = validate_structure(layout)
    if not checks["passed"]:
        raise RuntimeError(checks["errors"])
    print(f"Red: {len(layout.net.getEdges())} tramos; {len(layout.inventory['signals'])} controles; "
          f"{len(layout.focus_signals)} vecinos en los corredores; {checks['central_routes']} rutas centrales.")
    return layout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Volver a descargar OSM antes de importar")
    build(parser.parse_args().refresh)
