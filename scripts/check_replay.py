"""Revisa el visor en una sesión de Chrome aislada mediante agent-browser."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SESSION = "semaforos-replay"


def browser(*arguments):
    executable = next((ROOT / ".cache/npm/_npx").glob("*/node_modules/.bin/agent-browser"))
    command = [str(executable), "--session", SESSION, "--json", *arguments]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    payload = json.loads(result.stdout)
    if not payload.get("success"):
        raise RuntimeError(payload)
    return payload


def result_value(result):
    assert result.get("success"), result
    return result.get("data", {}).get("result")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("open", "check", "debug", "close"))
    args = parser.parse_args()
    if args.stage == "open":
        config = ROOT / ".cache/replay-browser.json"
        config.write_text(json.dumps({"headed": False}))
        print(json.dumps(browser(
            "--config", str(config), "--executable-path", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "--profile", str(ROOT / ".cache/replay-chrome-profile"), "--allow-file-access",
            "open", (ROOT / "outputs/simulacion.html").as_uri()), ensure_ascii=False))
        print(json.dumps(browser("set", "viewport", "1440", "1050")))
        print(json.dumps(browser("snapshot", "-i"), ensure_ascii=False))
        print(json.dumps(browser("screenshot", str(ROOT / "outputs/simulacion_preview.png"))))
    elif args.stage == "check":
        checks = []
        def evaluate(expression):
            return result_value(browser("eval", expression))
        initial = evaluate("({cards:document.querySelectorAll('.card').length,controller:document.getElementById('controller').value,clock:document.getElementById('clock').textContent,events:document.querySelectorAll('.event').length})")
        expected = {c: json.loads((ROOT / f"outputs/visual/{c}_101/metrics.json").read_text()) for c in ("qlearning", "adaptive")}
        trace = json.loads((ROOT / "outputs/visual/qlearning_101/trace.json").read_text())
        inventory = json.loads((ROOT / "data/cameras.json").read_text())
        assert initial["cards"] == 5 and initial["controller"] == "qlearning"
        assert initial["events"] >= 4
        assert evaluate("document.querySelectorAll('.movement-row').length") == len(inventory["movements"])
        checks.append("cinco cámaras y eventos del modelo disponibles")
        browser("click", "#restart")
        browser("click", "#play")
        assert evaluate("document.getElementById('play').getAttribute('aria-label')") == "Pausar simulación"
        browser("wait", "1500")
        browser("click", "#play")
        paused = evaluate("({time:Number(document.getElementById('timeline').value),play:document.getElementById('play').getAttribute('aria-label')})")
        assert paused["time"] > 1 and paused["play"] == "Reproducir simulación", paused
        checks.append("reproducir y pausar avanzan el tiempo")
        emergency_index = evaluate("Array.from(document.querySelectorAll('.event')).findIndex(e=>e.textContent.includes('Ambulancia detectada'))") + 1
        browser("click", f".event:nth-child({emergency_index})")
        event = evaluate(f"({{clock:document.getElementById('clock').textContent,event:document.querySelector('.event:nth-child({emergency_index})').textContent,playing:document.getElementById('play').getAttribute('aria-label')}})")
        assert event["clock"].startswith(event["event"][:5])
        assert event["playing"] == "Reproducir simulación"
        checks.append("salto a una emergencia en el segundo registrado")
        browser("screenshot", "--full", str(ROOT / "outputs/simulacion_emergencia.png"))
        browser("select", "#speed", "20")
        assert evaluate("document.getElementById('speed').value") == "20"
        checks.append("selector de velocidad")
        browser("select", "#controller", "adaptive")
        rules = evaluate("({metrics:document.getElementById('metrics').textContent,time:document.getElementById('timeline').value})")
        assert str(expected["adaptive"]["arrived"]) in rules["metrics"] and rules["time"] == "1"
        assert f"{expected['adaptive']['observed_mean_queue_vehicles']:.2f}" in rules["metrics"]
        checks.append("cambiar de controlador carga la grabación por reglas")
        browser("click", "#zoom-in")
        browser("click", "#zoom-out")
        browser("click", "#zoom-reset")
        browser("select", "#controller", "qlearning")
        assert str(expected["qlearning"]["arrived"]) in evaluate("document.getElementById('metrics').textContent")
        paired = next(row for row in trace if row["stage"] == "green"
                      and inventory["phase_meta"][row["phase"]]["kind"] == "through"
                      and sum(row["queues"]) >= 15 and row["signal_timings"][0]["assigned_green_s"] >= 10
                      and any(h["remaining_s"] is not None and h["remaining_s"] >= 5 for h in row["signal_timings"]))
        evaluate(f"document.getElementById('timeline').value={paired['time_s']};document.getElementById('timeline').dispatchEvent(new Event('input'))")
        heads = evaluate("Array.from(document.querySelectorAll('.movement-row')).map(e=>({id:e.id.slice(7),assigned:e.querySelector('.assigned').textContent,remaining:e.querySelector('.remaining').textContent}))")
        for rendered in heads:
            recorded = next(h for h in paired["signal_timings"] if h["id"] == rendered["id"])
            assert rendered["assigned"] == f"{recorded['assigned_green_s']} s"
            if recorded["remaining_s"] is not None:
                assert rendered["remaining"] == f"{recorded['remaining_s']} s restantes previstos"
        checks.append("doce tiempos asignados y contadores coinciden con SUMO")
        cameras = inventory["phase_meta"][paired["phase"]]["cameras"]
        parallel = [h for h in heads if h["id"] in [f"C{c + 1}_s" for c in cameras]]
        assert len({(h["assigned"], h["remaining"]) for h in parallel}) == 1
        checks.append("ambos sentidos de frente muestran el mismo tiempo")
        browser("screenshot", "--full", str(ROOT / "outputs/simulacion_tiempos.png"))
        browser("click", "#restart")
        browser("set", "viewport", "390", "844")
        evaluate("window.scrollTo(0,0)")
        browser("screenshot", str(ROOT / "outputs/simulacion_movil.png"))
        assert evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        checks.append("visor sin desbordamiento horizontal a 390 px")
        browser("set", "viewport", "1440", "1050")
        evaluate(f"document.getElementById('timeline').value={paired['time_s']};document.getElementById('timeline').dispatchEvent(new Event('input'))")
        browser("screenshot", "--full", str(ROOT / "outputs/simulacion_preview.png"))
        errors = browser("errors")
        assert not errors.get("data", {}).get("errors"), errors
        report = {"checks": checks, "browser_errors": errors, "recordings_verified": ["qlearning", "adaptive"]}
        (ROOT / "outputs/visual_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.stage == "debug":
        print(json.dumps(browser("eval", "({time:document.getElementById('timeline').value,play:document.getElementById('play').getAttribute('aria-label'),visibility:document.visibilityState,clock:document.getElementById('clock').textContent})")))
        print(json.dumps(browser("click", "#play")))
        print(json.dumps(browser("eval", "({play:document.getElementById('play').getAttribute('aria-label'),clock:document.getElementById('clock').textContent})")))
        print(json.dumps(browser("wait", "1500")))
        print(json.dumps(browser("eval", "({play:document.getElementById('play').getAttribute('aria-label'),clock:document.getElementById('clock').textContent})")))
        print(json.dumps(browser("errors")))
        print(json.dumps(browser("snapshot", "-i"), ensure_ascii=False))
        print(json.dumps(browser("screenshot", "--full", str(ROOT / "outputs/simulacion_debug.png"))))
    else:
        print(json.dumps(browser("close")))
