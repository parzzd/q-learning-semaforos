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
    runs = []
    start, previous = 0, trace[0]["signals"]
    for index, row in enumerate(trace[1:], 1):
        state = row["signals"]
        assert state in phases or set(state) <= {"r", "y"}, state
        if state != previous:
            runs.append((previous, index - start))
            start, previous = index, state
    runs.append((previous, len(trace) - start))
    completed_runs = runs[:-1]  # La última fase puede quedar truncada por el horizonte.
    assert all(10 <= duration <= 60 for state, duration in completed_runs if state in phases)
    assert all(duration >= 2 for state, duration in completed_runs if set(state) == {"r"})
    assert all(duration >= 3 for state, duration in completed_runs if "y" in state)
    for index, (state, duration) in enumerate(runs):
        if state in phases and index:
            assert set(runs[index - 1][0]) == {"r"}, "Falta todo rojo antes del verde."
    assert sum(any(row["blocked"]) for row in trace) > 0, "La demostración no ejercitó el bloqueo de salidas."
    metrics = json.loads((ROOT / "outputs/demo/metrics.json").read_text())
    assert metrics["colliding_vehicle_count"] == 0
    assert metrics["teleports"] == 0
    result = {"samples": len(trace), "blocked_samples": sum(any(row["blocked"]) for row in trace),
              "allowed_phases_and_transitions": "passed", "green_limits": "passed",
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
