"""Valida corredores, señales, rutas y SUMO. No ejecuta aprendizaje."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pilot.expanded import ExpandedLayout, EXPANDED, validate_structure
from pilot.learning import run_episode
from pilot.network import OUTPUTS
from pilot.replay import export_replay

FOLDER = OUTPUTS / "expanded"


def draw_map(layout):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 10), layout="constrained")
    cx, cy = layout.center
    for edge in layout.net.getEdges():
        shape = edge.getShape()
        x, y = zip(*[(p[0] - cx, p[1] - cy) for p in shape])
        active = edge.getID() in layout.corridor_edges
        ax.plot(x, y, color="#88a9c4" if active else "#e0e6ec",
                linewidth=2.4 if active else .6, zorder=2 if active else 1)
    neighbors = [s for s in layout.inventory["signals"] if s["id"] in layout.focus_signals]
    for signal in layout.inventory["signals"]:
        x, y = signal["xy"][0] - cx, signal["xy"][1] - cy
        ax.scatter(x, y, s=22, color="#aebbc8", zorder=3)
    legend = []
    for i, signal in enumerate(neighbors):
        x, y = signal["xy"][0] - cx, signal["xy"][1] - cy
        ax.scatter(x, y, marker="s", s=62, color="#256993", zorder=5)
        ax.annotate(f"N{i + 1}", (x, y), xytext=(8, 7), textcoords="offset points", weight="bold",
                    color="#17405e", bbox=dict(facecolor="white", edgecolor="none", alpha=.9))
        roads = " / ".join(r.replace("Avenida General ", "").replace("Avenida ", "").replace("Calle ", "") for r in signal["roads"])
        legend.append(f"N{i + 1}: {roads}")
    for c in layout.corridors:
        if not c["upstream"]:
            continue
        block = c["blocks"][4]
        x, y = block["xy"][0] - cx, block["xy"][1] - cy
        ax.scatter(x, y, s=130, facecolors="none", edgecolors="#426d8b", zorder=4)
        ax.annotate(f"{c['id']} · cuadra 5", (x, y), xytext=(10, -15), textcoords="offset points",
                    fontsize=9, color="#34556c", bbox=dict(facecolor="white", edgecolor="none", alpha=.8))
    ax.scatter(0, 0, marker="*", s=210, color="#d97637", zorder=6)
    ax.annotate("Javier Prado × Salaverry\n5 cámaras virtuales", (0, 0), xytext=(15, 18), textcoords="offset points",
                fontsize=11, weight="bold", bbox=dict(facecolor="white", edgecolor="none", alpha=.9))
    ax.set(xlim=(-1300, 1350), ylim=(-1250, 1250), xlabel="Metros al este del cruce central", ylabel="Metros al norte del cruce central")
    ax.set_aspect("equal")
    ax.grid(alpha=.12)
    ax.set_title("Red ampliada: cinco cuadras por acceso y semáforos anteriores", fontsize=15, loc="left", pad=16)
    text = "\n".join(legend) + "\n\nAzul: corredores activos · gris: contexto OSM\nPlanes y demanda sintéticos; sin validación municipal.\n© OpenStreetMap contributors · ODbL 1.0"
    ax.text(.015, .015, text, transform=ax.transAxes, va="bottom", fontsize=9,
            bbox=dict(facecolor="white", edgecolor="#dce5ed", alpha=.96, boxstyle="round,pad=.6"))
    for suffix in ("png", "svg"):
        fig.savefig(FOLDER / f"mapa_corredores.{suffix}", dpi=160)
    plt.close(fig)


def audit_recording(layout, directory):
    """Contrasta estados y tiempos visibles con el plan permitido."""
    frames = json.loads((directory / "trace.json").read_text())
    errors, paired, turn_relief, blocked = [], 0, 0, 0
    previous = None
    neighbors = {s["id"]: s for s in layout.inventory["signals"] if s["id"] in layout.focus_signals}
    for frame in frames:
        state, phase = frame["signals"], frame["phase"]
        if frame["stage"] == "green":
            if phase is None or any(c not in (layout.phases[phase][i], "r") for i, c in enumerate(state)):
                errors.append({"time_s": frame["time_s"], "error": "unsafe central green"})
            for head in frame["signal_timings"]:
                if not 10 <= head["assigned_green_s"] <= 60:
                    errors.append({"time_s": frame["time_s"], "error": "green allocation outside limits"})
            meta = layout.phase_meta[phase]
            if meta["kind"] == "through":
                straight = [h for h in frame["signal_timings"] if h["id"] in [f"C{c + 1}_s" for c in meta["cameras"]]]
                if len({(h["state"], h["assigned_green_s"], h["remaining_s"]) for h in straight}) != 1:
                    errors.append({"time_s": frame["time_s"], "error": "opposite through timings differ"})
                paired += 1
            turn_relief += int(frame["reason"] == "cuello_de_botella_giro")
        blocked += int(any(frame["blocked"]))
        if frame["stage"] == "all_red" and any(c != "r" for c in state):
            errors.append({"time_s": frame["time_s"], "error": "clearance not all red"})
        if previous and previous["stage"] == "green" and frame["stage"] == "green" and previous["phase"] != phase:
            errors.append({"time_s": frame["time_s"], "error": "phase change without clearance"})
        previous = frame
        if {s["id"] for s in frame["neighbor_signals"]} != set(neighbors):
            errors.append({"time_s": frame["time_s"], "error": "incomplete neighbor telemetry"})
        for entry in frame["neighbor_signals"]:
            program = neighbors[entry["id"]]["program"]
            if entry["state"] != program[entry["phase"]]["state"] or entry["remaining_s"] < 0:
                errors.append({"time_s": frame["time_s"], "error": "neighbor state mismatch"})
    return {"passed": not errors, "errors": errors, "samples": len(frames),
            "paired_through_samples": paired, "turn_bottleneck_relief_samples": turn_relief,
            "blocked_output_samples": blocked}


def validate(seeds=(201, 202, 203), horizon=1200, reuse=False):
    FOLDER.mkdir(parents=True, exist_ok=True)
    layout = ExpandedLayout()
    layout.save()
    structure = validate_structure(layout)
    if not structure["passed"]:
        raise RuntimeError(structure["errors"])
    model = OUTPUTS / "model.json"
    model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
    results, recordings = [], {}
    for seed in seeds:
        for controller in ("adaptive", "fixed"):
            directory = FOLDER / "runs" / f"{controller}_{seed}"
            metrics = None
            if reuse and (directory / "metrics.json").exists() and (directory / "trace.json").exists():
                cached = json.loads((directory / "metrics.json").read_text())
                if (cached.get("scenario_schema") == layout.scenario_schema and cached.get("seed") == seed
                        and cached.get("controller") == controller and cached.get("horizon_s") == horizon):
                    metrics = cached
            if metrics is None:
                metrics = run_episode(layout, directory, seed, horizon, controller=controller, trace=True)
            trace_audit = audit_recording(layout, directory)
            audit = metrics["network_audit"]
            per_camera = audit["by_camera"]
            passed = (trace_audit["passed"] and metrics["colliding_vehicle_count"] == 0
                      and metrics["teleports"] == 0 and metrics["pending_departures"] == 0
                      and all(c["central_crossings"] > 0 and c["with_prior_signal"] == c["central_crossings"] for c in per_camera)
                      and set(layout.focus_signals) <= set(audit["crossings_by_tls"])
                      and all(any(s in audit["upstream_red_waiters"] for s in r["upstream_signals"])
                              for r in layout.routes if r["direction"] == "s"))
            # Eventos completos en runs/; el resumen guarda sólo estadísticas.
            audit.pop("central_events", None)
            results.append({**metrics, "passed": passed, "trace_audit": trace_audit})
            print(f"{controller} · {seed}: {audit['central_crossings']} cruces centrales, "
                  f"{audit['central_crossings_after_prior_signal']} tras TLS anterior; "
                  f"colisiones {metrics['colliding_vehicle_count']}, teleports {metrics['teleports']}; "
                  f"validación {'OK' if passed else 'FALLÓ'}", flush=True)
            if seed == seeds[0]:
                recordings[controller] = {"directory": directory, "metrics": metrics}
    unchanged = model_hash == hashlib.sha256(model.read_bytes()).hexdigest()
    paired_demand = {str(seed): (FOLDER / "runs" / f"adaptive_{seed}" / "demand.rou.xml").read_bytes()
                    == (FOLDER / "runs" / f"fixed_{seed}" / "demand.rou.xml").read_bytes() for seed in seeds}
    report = {"profile": layout.profile, "scenario_schema": layout.scenario_schema,
        "passed": structure["passed"] and all(r["passed"] for r in results) and unchanged and all(paired_demand.values()),
        "paired_demands_identical": paired_demand,
        "model_retrained": False, "previous_model_unchanged": unchanged,
        "previous_model_sha256": model_hash, "seeds": list(seeds), "seconds_per_run": horizon,
        "source": layout.inventory["source"], "imported_signal_controls": len(layout.inventory["signals"]),
        "active_neighbor_controls": len(layout.focus_signals), "structure": structure, "results": results,
        "limits": ["La asociación de puntos OSM a controles es espacial, no una inspección física.",
            "Planes vecinos y conexiones de carriles generados por SUMO; no se dispone de tiempos municipales.",
            "Demanda sintética sin conteos locales, peatones, buses ni oclusiones de cámara.",
            "La prioridad de emergencias se conserva sólo en el cruce central; vecinos con ciclos fijos.",
            "Cinco cruces de calles por sentido, con margen; el acceso este añade un cruce para incluir TLS previo.",
            "No comparar estas colas directamente con el piloto corto: cambió la red y la demanda."]}
    (FOLDER / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with (EXPANDED / "signals.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["tls_id", "streets", "longitude", "latitude", "osm_tag_associations", "corridor_active", "program_source"])
        for signal in layout.inventory["signals"]:
            writer.writerow([signal["id"], " / ".join(signal["roads"]), *signal["lonlat"],
                             len(signal["osm_nodes"]), signal["focus"], signal["program_source"]])
    draw_map(layout)
    export_replay(layout, recordings, FOLDER / "simulacion.html")
    write_report(layout, report)
    print(f"Informe: {FOLDER / 'validation.md'}\nVisor: {FOLDER / 'simulacion.html'}", flush=True)
    if not report["passed"]:
        raise RuntimeError("La validación no pasó; revisa validation.json antes de entrenar.")
    return report


def write_report(layout, report):
    lines = ["# Validación de la red ampliada", "", f"Resultado: **{'PASÓ' if report['passed'] else 'REVISAR'}** en simulación, con demanda sintética.",
        "", "El modelo anterior no se modificó ni se volvió a entrenar. Se probaron reglas de asignación y verdes centrales fijos de 20 s.",
        "", "## Red y extracción", "", f"Área OSM: `{report['source']['bbox_lonlat']}`. Descarga: {report['source']['downloaded_at_utc']}.",
        "", f"{len(layout.net.getEdges())} tramos; {report['source']['osm_signal_nodes']} puntos OSM con `highway=traffic_signals`; "
              f"{report['imported_signal_controls']} controles importados, de los cuales {len(layout.focus_signals)} vecinos y el central están en los corredores activos.",
        "", "Los puntos OSM pueden representar señales de aproximación o cruces. Se agrupan espacialmente y no equivalen uno a uno a intersecciones.",
        "", "| Acceso | Quinta calle transversal | Distancia a quinta calle | Inicio de ruta | Semáforos previos |",
        "| --- | --- | ---: | ---: | ---: |"]
    for corridor in layout.corridors:
        if corridor["upstream"]:
            fifth = corridor["blocks"][4]
            lines.append(f"| {corridor['id']} · {corridor['label']} | {' / '.join(fifth['streets'])} | {fifth['distance_m']} m | {corridor['length_m']} m | {len(corridor['traffic_lights'])} |")
    lines += ["", "Cada ruta se inicia antes de un semáforo previo. Se cuentan cruces con calles con nombre, excluyendo nodos de geometría y accesos sin nombre. "
              "El acceso este necesita un sexto cruce y un segmento de margen para atravesar el semáforo de Las Flores.",
              "", f"{len(layout.routes)} rutas centrales y {len(layout.background_routes)} rutas laterales tienen conexiones válidas para automóviles. "
              "Se mantienen cinco cámaras: su cobertura local no se amplía artificialmente.",
              "", "## Semáforos vecinos del ensayo", "", "| Control | Cruce según red OSM | Puntos OSM asociados | Plan |", "| --- | --- | ---: | --- |"]
    neighbors = [s for s in layout.inventory["signals"] if s["id"] in layout.focus_signals]
    for i, signal in enumerate(neighbors):
        lines.append(f"| N{i+1} | {' / '.join(signal['roads'])} | {len(signal['osm_nodes'])} | Generado por SUMO |")
    lines += ["", "## Pruebas SUMO", "", "| Controlador | Semilla | Vehículos insertados / completados | Cruces centrales tras TLS previo | Cola central media | Colisiones / teleports | Pendientes de insertar |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in report["results"]:
        audit = run["network_audit"]
        lines.append(f"| {run['controller']} | {run['seed']} | {run['departed']} / {run['arrived']} | "
            f"{audit['central_crossings_after_prior_signal']} / {audit['central_crossings']} | {run['observed_mean_queue_vehicles']} | "
            f"{run['colliding_vehicle_count']} / {run['teleports']} | {run['pending_departures']} |")
    total = sum(r["network_audit"]["central_crossings"] for r in report["results"])
    means = {c: sum(r["observed_mean_queue_vehicles"] for r in report["results"] if r["controller"] == c) / len(report["seeds"])
             for c in ("adaptive", "fixed")}
    lines += ["", f"Se observaron {total} cruces centrales en {len(report['results'])} ejecuciones de {report['seconds_per_run']} s. "
              "Cada uno tuvo un cruce semafórico previo registrado por sus transiciones reales entre carriles. Los cuatro accesos recibieron tráfico en cada ejecución.",
              "", f"Cola central media entre semillas: reglas **{means['adaptive']:.2f} vehículos**, verde fijo **{means['fixed']:.2f} vehículos**. "
              "La demanda de cada pareja de controladores se comprobó idéntica byte a byte. El verde fijo es la referencia para evaluar el próximo entrenamiento.",
              "", "Se verificaron las fases permitidas, tiempos de 10–60 s, igualdad de tiempos en sentidos opuestos, transiciones con despeje, "
              f"estados de los {len(layout.focus_signals)} semáforos vecinos y al menos un control previo con vehículos detenidos ante rojo por acceso. "
              "Un semáforo puede recibir un pelotón completamente durante su verde; no se exige que todos detengan vehículos en cada semilla. "
              "Las llegadas por acceso se registran en ventanas de 10 s para revisar los pelotones.",
              "", "## Revisar y decidir el entrenamiento", "", "Abre [simulacion.html](simulacion.html): reglas y verde fijo usan la misma demanda para la primera semilla. "
              f"El mapa permite zoom y arrastre, muestra la quinta cuadra y los estados/tiempos de N1–N{len(layout.focus_signals)}.",
              "", "La validación técnica permite usar esta red como siguiente escenario de entrenamiento sintético. Antes de interpretarla como tráfico real, "
              "hay que contrastar en campo las señales, sentidos, carriles de giro y tiempos, y calibrar la demanda con conteos. "
              "El modelo anterior se bloquea para esta red porque corresponde a otro escenario.",
              "", "## Límites de la validación", ""]
    lines += [f"- {limit}" for limit in report["limits"]]
    lines += ["", "Fuentes: [OpenStreetMap y ODbL](https://www.openstreetmap.org/copyright), "
              "[importación y fases inferidas de SUMO](https://sumo.dlr.de/userdoc/Networks/Import/OpenStreetMap.html).", ""]
    (FOLDER / "validation.md").write_text("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--seeds", default="201,202,203")
    parser.add_argument("--reuse", action="store_true", help="Revisar grabaciones existentes sólo si coincide escenario, semilla, controlador y duración")
    args = parser.parse_args()
    seeds = tuple(int(x) for x in args.seeds.split(","))
    if args.seconds < 900 or not seeds:
        parser.error("Usa al menos 900 s y una semilla para observar recorridos largos.")
    validate(seeds, args.seconds, args.reuse)
