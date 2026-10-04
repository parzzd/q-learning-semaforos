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
    return json.loads(result.stdout)


def result_value(result):
    assert result.get("success"), result
    return result.get("data", {}).get("result")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("open", "check", "close"))
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
        assert initial["cards"] == 5 and initial["controller"] == "qlearning"
        assert initial["events"] >= 4
        checks.append("cinco cámaras y eventos del modelo disponibles")
        browser("click", "#play")
        browser("wait", "450")
        browser("click", "#play")
        paused = evaluate("({time:Number(document.getElementById('timeline').value),play:document.getElementById('play').getAttribute('aria-label')})")
        assert paused["time"] > 1 and paused["play"] == "Reproducir simulación"
        checks.append("reproducir y pausar avanzan el tiempo")
        browser("click", ".event:first-child")
        event = evaluate("({clock:document.getElementById('clock').textContent,event:document.querySelector('.event').textContent,playing:document.getElementById('play').getAttribute('aria-label')})")
        assert event["clock"].startswith(event["event"][:5])
        assert event["playing"] == "Reproducir simulación"
        checks.append("salto a una emergencia en el segundo registrado")
        browser("screenshot", str(ROOT / "outputs/simulacion_emergencia.png"))
        browser("select", "#speed", "20")
        assert evaluate("document.getElementById('speed').value") == "20"
        checks.append("selector de velocidad")
        browser("select", "#controller", "adaptive")
        rules = evaluate("({metrics:document.getElementById('metrics').textContent,time:document.getElementById('timeline').value})")
        assert "331" in rules["metrics"] and rules["time"] == "1"
        checks.append("cambiar de controlador carga la grabación por reglas")
        browser("click", "#zoom-in")
        browser("click", "#zoom-out")
        browser("click", "#zoom-reset")
        browser("select", "#controller", "qlearning")
        assert "303" in evaluate("document.getElementById('metrics').textContent")
        browser("click", "#restart")
        browser("set", "viewport", "390", "844")
        browser("screenshot", str(ROOT / "outputs/simulacion_movil.png"))
        assert evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        checks.append("visor sin desbordamiento horizontal a 390 px")
        browser("set", "viewport", "1440", "1050")
        browser("screenshot", str(ROOT / "outputs/simulacion_preview.png"))
        errors = browser("errors")
        assert not errors.get("data", {}).get("errors"), errors
        report = {"checks": checks, "browser_errors": errors, "recordings_verified": ["qlearning", "adaptive"]}
        (ROOT / "outputs/visual_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(browser("close")))
