"""Artefactos de revisión: mapa y evolución del tráfico, sin servicios externos."""
import json
import os
from pathlib import Path

from .network import OUTPUTS, ROOT

os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection


def draw_map(layout):
    OUTPUTS.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(15, 8), gridspec_kw={"width_ratios": [1.35, 1]}, facecolor="#f5f7fa")
    cx, cy = layout.center
    for axis in axes:
        axis.set_facecolor("#f5f7fa")
        minor, major = [], []
        for edge in layout.net.getEdges():
            shape = [(x - cx, y - cy) for x, y in edge.getShape()]
            (major if "Prado" in edge.getName() or "Salaverry" in edge.getName() else minor).append(shape)
        axis.add_collection(LineCollection(minor, colors="#c5cdd7", linewidths=1.1, zorder=1))
        axis.add_collection(LineCollection(major, colors="#64748b", linewidths=2.5, zorder=2))
        for camera, chain in enumerate(layout.inchains):
            shapes = [[(x - cx, y - cy) for x, y in edge.getShape()] for edge in chain]
            axis.add_collection(LineCollection(shapes, colors="#1b86cf", linewidths=4, alpha=0.55, zorder=3))
        for chain in layout.outchains:
            shapes = [[(x - cx, y - cy) for x, y in edge.getShape()] for edge in chain]
            axis.add_collection(LineCollection(shapes, colors="#16a089", linewidths=3, alpha=0.5, zorder=3))
        axis.scatter([0], [0], color="#f0b429", marker="s", s=80, edgecolor="white", linewidth=1.5, zorder=5)
        for edge in layout.incoming:
            x, y = edge.getLanes()[0].getShape()[-1]
            axis.scatter(x - cx, y - cy, s=30, marker="s", color="#d95050", edgecolor="white", zorder=5)
        for camera in layout.cameras:
            x, y = camera["position_xy"]
            axis.scatter(x - cx, y - cy, s=60, color="#0b4f88" if camera["id"] != "C5" else "#087f6d", zorder=6)
            if axis is axes[1]:
                axis.annotate(camera["id"], (x - cx, y - cy), xytext=(7, 7), textcoords="offset points", weight="bold",
                              color="#0f2b46", bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "none"}, zorder=7)
        axis.set_aspect("equal")
        axis.set_xlabel("Distancia este/oeste desde el cruce (m)")
        axis.set_ylabel("Distancia norte/sur desde el cruce (m)")
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(alpha=0.15)
    axes[0].set_xlim(-550, 550)
    axes[0].set_ylim(-450, 590)
    axes[0].set_title("Red vial y cuadras adyacentes", loc="left", weight="bold", pad=18)
    axes[1].set_xlim(-155, 185)
    axes[1].set_ylim(-165, 175)
    axes[1].set_title("Cinco cámaras virtuales · posiciones propuestas", loc="left", weight="bold", pad=18)
    labels = set()
    for edge in layout.net.getEdges():
        name = edge.getName().replace("Avenida ", "Av. ").replace("Calle ", "")
        if not name or name in labels or edge.getLength() < 80:
            continue
        x, y = edge.getShape()[len(edge.getShape()) // 2]
        if abs(x - cx) > 490 or y - cy < -390 or y - cy > 520:
            continue
        if abs(x - cx) < 150 and abs(y - cy) < 100:
            continue
        if name not in ("Av. Javier Prado Oeste", "Av. General Salaverry") and len(labels) > 11:
            continue
        labels.add(name)
        axes[0].text(x - cx, y - cy + 9, name, fontsize=8, color="#34445c",
                     bbox={"fc": "#f5f7fa", "ec": "none", "alpha": 0.85, "pad": 1})
    fig.suptitle("Javier Prado × Salaverry | Piloto de control semafórico", x=0.065, ha="left", fontsize=20, weight="bold", color="#132b45")
    fig.text(0.065, 0.075, "Azul: accesos (C1–C4)     Verde: salidas (C5)     Rojo: señales virtuales     Amarillo: cruce principal", color="#34445c")
    fig.text(0.065, 0.042, "© OpenStreetMap contributors · ODbL 1.0 | Cámaras ideales por carriles; ubicación y cobertura física pendientes de validar.", fontsize=9, color="#64748b")
    fig.subplots_adjust(left=0.065, right=0.98, bottom=0.16, top=0.86, wspace=0.25)
    path = OUTPUTS / "mapa_camaras.png"
    fig.savefig(path, dpi=160)
    fig.savefig(OUTPUTS / "mapa_camaras.svg")
    plt.close(fig)
    return path


def draw_episode(layout, directory):
    trace = json.loads((directory / "trace.json").read_text())
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, facecolor="white")
    times = [row["time_s"] for row in trace]
    for camera, label in enumerate(layout.camera_labels):
        axes[0].plot(times, [row["queues"][camera] for row in trace], label=f"C{camera + 1} · {label}", linewidth=1.4)
    axes[0].set_ylabel("Vehículos detenidos observados")
    axes[0].legend(ncol=2, frameon=False, fontsize=9)
    phases = []
    for row in trace:
        phases.append(row["phase"] + 1 if row.get("stage") == "green" else 0)
    axes[1].step(times, phases, where="post", color="#0b4f88", linewidth=1.2)
    axes[1].set_yticks(range(len(layout.phases) + 1), ["Despeje"] + [meta["label"] for meta in layout.phase_meta])
    axes[1].tick_params(axis="y", labelsize=8)
    axes[1].set_ylabel("Fase con verde")
    axes[1].set_xlabel("Tiempo simulado (s)")
    for axis in axes:
        axis.grid(alpha=0.16)
        axis.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Tráfico y fases observadas | Demostración con datos sintéticos", fontsize=15, weight="bold")
    fig.tight_layout()
    path = directory / "trafico.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def snapshot_figure(layout, row):
    """Vista cenital de una muestra; los colores de las señales son observaciones ideales."""
    fig, (axis, panel) = plt.subplots(1, 2, figsize=(12, 6), gridspec_kw={"width_ratios": [1.5, 1]})
    cx, cy = layout.center
    for edge in layout.net.getEdges():
        shape = edge.getShape()
        x = [point[0] - cx for point in shape]
        y = [point[1] - cy for point in shape]
        major = "Prado" in edge.getName() or "Salaverry" in edge.getName()
        axis.plot(x, y, color="#8a98a9" if major else "#d3d9e2", linewidth=2.5 if major else 1, zorder=1)
    for vehicle in row["vehicles"]:
        x, y = vehicle["xy"]
        kind = vehicle["type"]
        color = "#ee7046" if kind == "ambulance_active" else "#8057cb" if kind == "police_active" else "#53769a"
        axis.scatter(x - cx, y - cy, s=20 if kind.endswith("active") else 7, color=color, zorder=4)
    labels = []
    for index, camera in enumerate(layout.cameras[:4]):
        x, y = camera["position_xy"]
        edge = layout.incoming[index]
        sx, sy = edge.getLanes()[0].getShape()[-1]
        states = {row["signals"][link].lower() for link in camera["visible_signal_links"]}
        state = "verde" if "g" in states else "amarillo" if "y" in states else "rojo"
        color = {"verde": "#16a085", "amarillo": "#e5b229", "rojo": "#dc5757"}[state]
        axis.plot([x - cx, sx - cx], [y - cy, sy - cy], ":", linewidth=1, color="#3579ad", zorder=2)
        axis.scatter(sx - cx, sy - cy, marker="s", s=65, color=color, edgecolor="white", zorder=6)
        axis.scatter(x - cx, y - cy, s=35, color="#0b4f88", zorder=5)
        axis.annotate(camera["id"], (x - cx, y - cy), xytext=(5, 7), textcoords="offset points", weight="bold",
                      bbox={"fc": "white", "ec": "none", "pad": 1}, zorder=6)
        heads = {h["id"]: h for h in row.get("signal_timings", [])}
        timing = ""
        if heads:
            front, left = heads[f"C{index + 1}_s"], heads[f"C{index + 1}_l"]
            timing = f"\nVerde asignado: frente {front['assigned_green_s']} s · giro izq. {left['assigned_green_s']} s"
        labels.append(f"{camera['id']} · {camera['label']}\nSeñal: {state} | Cola: {row['queues'][index]} | Giros: {row['turn_queues'][index]}{timing}")
    camera = layout.cameras[4]
    x, y = camera["position_xy"]
    axis.scatter(x - cx, y - cy, s=35, color="#087f6d", zorder=5)
    axis.annotate("C5", (x - cx, y - cy), xytext=(5, 7), textcoords="offset points", weight="bold", zorder=6)
    axis.set_xlim(-320, 320)
    axis.set_ylim(-310, 340)
    axis.set_aspect("equal")
    axis.set_xlabel("Este/oeste (m desde el cruce)")
    axis.set_ylabel("Norte/sur (m desde el cruce)")
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(alpha=0.15)
    panel.axis("off")
    panel.text(0, 0.98, "Observaciones de las cámaras", fontsize=13, weight="bold", va="top", color="#132b45")
    for index, label in enumerate(labels):
        panel.text(0, 0.85 - index * 0.16, label, va="top", fontsize=9, linespacing=1.4)
    panel.text(0, 0.20, "C5 · Salidas\n" + ("Congestión detectada" if any(row["blocked"]) else "Sin bloqueo detectado"), va="top", fontsize=10)
    active = ", ".join(event["id"] for event in row["emergencies"]) or "ninguna visible"
    panel.text(0, 0.075, "Emergencia activa: " + active, va="top", fontsize=10, wrap=True)
    fig.suptitle(f"Javier Prado × Salaverry | t = {row['time_s']} s", fontsize=15, weight="bold")
    fig.text(0.08, 0.025, "Vista cenital sintética · Cuadrados: señales observadas · Naranja/violeta: emergencias activas · © OpenStreetMap contributors", fontsize=8, color="#64748b")
    fig.tight_layout(rect=[0, 0.05, 1, 0.95])
    return fig
