"""Verifica el visor ampliado en Chrome aislado, incluyendo telemetría SUMO."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_replay import browser, result_value

FOLDER = ROOT / "outputs/expanded"


def check():
    config = ROOT / ".cache/replay-browser.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"headed": False}))
    checks = []
    def evaluate(expression):
        return result_value(browser("eval", expression))
    try:
        browser("--config", str(config), "--executable-path", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--profile", str(ROOT / ".cache/replay-chrome-profile"), "--allow-file-access",
                "open", (FOLDER / "simulacion.html").as_uri())
        browser("set", "viewport", "1440", "1050")
        browser("snapshot", "-i")
        assert evaluate("Array.from(document.querySelectorAll('#controller option')).map(o=>o.value)") == ["adaptive", "fixed"]
        assert evaluate("document.querySelectorAll('.cards>.card').length") == 5
        assert evaluate("document.querySelectorAll('.movement-row').length") == 12
        assert evaluate("document.querySelectorAll('.neighbor-time').length") == 6
        checks.append("cinco cámaras, doce movimientos y seis controles vecinos; sin modelo antiguo")
        browser("click", "#play")
        browser("wait", "1200")
        browser("click", "#play")
        assert evaluate("Number(document.getElementById('timeline').value)") > 1
        checks.append("reproducción y pausa avanzan el registro")
        trace = json.loads((FOLDER / "runs/adaptive_201/trace.json").read_text())
        frame = max((f for f in trace if f["stage"] == "green" and f["phase"] in (0, 1)
                     and any(h["remaining_s"] is not None and h["remaining_s"] >= 5 for h in f["signal_timings"])),
                    key=lambda f: sum(f["queues"]))
        evaluate(f"seek({frame['time_s']})")
        heads = evaluate("Array.from(document.querySelectorAll('.movement-row')).map(e=>({id:e.id.slice(7),assigned:e.querySelector('.assigned').textContent,remaining:e.querySelector('.remaining').textContent}))")
        for head in heads:
            recorded = next(h for h in frame["signal_timings"] if h["id"] == head["id"])
            assert head["assigned"] == f"{recorded['assigned_green_s']} s"
            expected = "asignación de su fase" if recorded["remaining_s"] is None else f"{recorded['remaining_s']} s restantes previstos"
            assert head["remaining"] == expected
        neighbors = evaluate("Array.from(document.querySelectorAll('[id^=neighbor-]')).map(e=>({id:expanded.neighbors[Number(e.id.slice(9))].id,time:e.querySelector('.neighbor-time').textContent,heads:Array.from(e.querySelectorAll('.neighbor-heads span')).map(h=>h.title)}))")
        for entry in neighbors:
            recorded = next(s for s in frame["neighbor_signals"] if s["id"] == entry["id"])
            assert entry["time"] == f"Fase: {recorded['phase_duration_s']:g} s · cambio en {int(recorded['remaining_s'])} s"
            assert entry["heads"] == [f"Enlace {i}: {state}" for i, state in enumerate(recorded["state"])]
        checks.append("doce tiempos centrales y seis estados/tiempos vecinos coinciden con SUMO")
        browser("screenshot", "--full", str(FOLDER / "preview.png"))
        browser("click", '[aria-label="Ver cruce central"]')
        assert evaluate("zoom") == 4
        browser("screenshot", str(FOLDER / "cruce_central.png"))
        browser("click", "#zoom-reset")
        assert evaluate("({zoom,pan})") == {"zoom": 1, "pan": [0, 0]}
        browser("mouse", "move", "300", "600")
        browser("mouse", "down", "left")
        browser("mouse", "move", "350", "630")
        browser("mouse", "up", "left")
        assert evaluate("pan") == [50, 30]
        browser("click", "#zoom-reset")
        checks.append("acercar al cruce, arrastrar y restablecer el mapa")
        browser("select", "#controller", "fixed")
        fixed = json.loads((FOLDER / "runs/fixed_201/metrics.json").read_text())
        assert str(fixed["arrived"]) in evaluate("document.getElementById('metrics').textContent")
        assert evaluate("document.getElementById('timeline').value") == "1"
        browser("select", "#controller", "adaptive")
        browser("click", ".event:first-child")
        assert evaluate("document.getElementById('clock').textContent.slice(0,5)") == evaluate("document.querySelector('.event').textContent.slice(0,5)")
        checks.append("comparación por reglas/verde fijo y salto a emergencia registrada")
        browser("set", "viewport", "390", "844")
        evaluate("window.scrollTo(0,0)")
        assert evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        browser("screenshot", str(FOLDER / "movil.png"))
        checks.append("ancho móvil de 390 px sin desbordamiento")
        errors = browser("errors")
        assert not errors.get("data", {}).get("errors"), errors
        report = {"passed": True, "checks": checks, "browser_errors": errors,
                  "source": "actual SUMO trace, seed 201, 1200 s, adaptive/fixed"}
        (FOLDER / "visual_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        browser("close")


if __name__ == "__main__":
    check()
