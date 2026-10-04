"""Importa el extracto OSM local y prepara la red del piloto."""
from pathlib import Path
import json
import subprocess
import sys

import sumolib

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CENTER_LONLAT = (-77.0543726, -12.0951777)
BBOX = (-77.0595, -12.0995, -77.0490, -12.0898)


def binary(name):
    local = Path(sys.executable).parent / name
    return str(local) if local.exists() else sumolib.checkBinary(name)


def build():
    DATA.mkdir(exist_ok=True)
    osm = DATA / "javier_prado_salaverry.osm"
    if not osm.exists():
        raise FileNotFoundError(f"Falta {osm}. Consulta README.md para descargarlo.")
    command = [
        binary("netconvert"), "--osm-files", str(osm),
        "--node-files", str(DATA / "join.nod.xml"),
        "--output-file", str(DATA / "pilot.net.xml"),
        "--geometry.remove", "--junctions.join", "--tls.guess-signals",
        "--tls.discard-simple", "--tls.join", "--tls.join-exclude", "JP_SALAVERRY",
        "--keep-edges.by-vclass", "passenger",
        "--keep-edges.in-geo-boundary=" + ",".join(map(str, BBOX)),
        "--remove-edges.isolated",
        "--no-turnarounds", "--output.street-names", "--tls.allred.time", "2",
        "--tls.left-green.time", "8", "--tls.minor-left.max-speed", "0",
        "--osm.turn-lanes", "true",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    (DATA / "network_build.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    network = sumolib.net.readNet(str(DATA / "pilot.net.xml"), withPrograms=True)
    tls = network.getTLS("JP_SALAVERRY")
    node = network.getNode("JP_SALAVERRY")
    incoming = [edge for edge in node.getIncoming() if edge.allows("passenger")]
    outgoing = [edge for edge in node.getOutgoing() if edge.allows("passenger")]
    metadata = {
        "junction_id": tls.getID(), "center_lonlat": CENTER_LONLAT,
        "bbox_lonlat": BBOX,
        "source": "© OpenStreetMap contributors — ODbL 1.0",
        "source_url": "https://www.openstreetmap.org/copyright",
        "download_date_local": "2026-10-04",
        "incoming_edges": [edge.getID() for edge in incoming],
        "outgoing_edges": [edge.getID() for edge in outgoing],
        "traffic_lights": [signal.getID() for signal in network.getTrafficLights()],
        "assumptions": [
            "Geometría y sentidos importados de OSM; requieren revisión en campo.",
            "Conexiones de giro y fases inferidas por SUMO; no son planes municipales.",
            "Se usa una fase exclusiva por acceso como simplificación inicial.",
            "Demanda y operación de emergencias sintéticas, sin calibración local.",
            "Piloto vehicular: peatones y ciclistas todavía no se simulan.",
            "Cámaras virtuales ideales con cobertura por carriles; sin video ni oclusiones.",
        ],
    }
    (DATA / "network.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
