"""Regenera y verifica el experimento completo con una sola conexión de trabajo."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pilot.cli import main


def audit_trace():
    trace = json.loads((ROOT / "outputs/demo/trace.json").read_text())
    phases = json.loads((ROOT / "data/cameras.json").read_text())["green_phases"]
    inventory = json.loads((ROOT / "data/cameras.json").read_text())
    runs = []
    start, previous = 0, trace[0]["signals"]
    paired_samples = 0
    for row in trace:
        if row["stage"] == "green":
            template = phases[row["phase"]]
            assert all(actual in (expected, "r") for actual, expected in zip(row["signals"], template))
            meta = inventory["phase_meta"][row["phase"]]
            if meta["kind"] == "through":
                heads = {h["id"]: h for h in row["signal_timings"]}
                values = [(heads[f"C{camera + 1}_s"]["state"], heads[f"C{camera + 1}_s"]["assigned_green_s"],
                           heads[f"C{camera + 1}_s"]["remaining_s"]) for camera in meta["cameras"]]
                assert len(set(values)) == 1, "Los tiempos de frente de los sentidos opuestos difieren."
                assert values[0][0] == "G"
                paired_samples += 1
        else:
            assert set(row["signals"]) <= {"r", "y"}
    for index, row in enumerate(trace[1:], 1):
        state = row["signals"]
        if state != previous:
            runs.append((previous, index - start))
            start, previous = index, state
    runs.append((previous, len(trace) - start))
    completed_runs = runs[:-1]  # La última fase puede quedar truncada por el horizonte.
    assert all(10 <= duration <= 60 for state, duration in completed_runs if any(color in "Gg" for color in state))
    assert all(duration >= 2 for state, duration in completed_runs if set(state) == {"r"})
    assert all(duration >= 3 for state, duration in completed_runs if "y" in state)
    for index, (state, duration) in enumerate(runs):
        if any(color in "Gg" for color in state) and index:
            assert set(runs[index - 1][0]) == {"r"}, "Falta todo rojo antes del verde."
    assert sum(any(row["blocked"]) for row in trace) > 0, "La demostración no ejercitó el bloqueo de salidas."
    metrics = json.loads((ROOT / "outputs/demo/metrics.json").read_text())
    assert metrics["colliding_vehicle_count"] == 0
    assert metrics["teleports"] == 0
    result = {"samples": len(trace), "blocked_samples": sum(any(row["blocked"]) for row in trace),
              "allowed_phases_and_transitions": "passed", "green_limits": "passed",
              "opposite_through_timings": "passed", "paired_samples": paired_samples,
              "turn_relief_samples": sum(row["reason"] == "cuello_de_botella_giro" and row["stage"] == "green" for row in trace),
              "colliding_vehicle_count": 0, "teleports": 0}
    (ROOT / "outputs/verification.json").write_text(json.dumps(result, indent=2))
    print("Verificación de la demostración:", result, flush=True)


if __name__ == "__main__":
    main(["map"])
    main(["demo", "--seconds", "600"])
    audit_trace()
    main(["experiment", "--episodes", "25", "--seconds", "600", "--seed", "7", "--seeds", "101,102,103"])
    evaluation = json.loads((ROOT / "outputs/evaluation.json").read_text())
    assert all(row["colliding_vehicle_count"] == 0 and row["teleports"] == 0 for row in evaluation["results"])
    print("Evaluación completada: nueve ejecuciones con semillas reservadas.", flush=True)
