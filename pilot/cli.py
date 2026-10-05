import argparse
import json

from .network import Layout, OUTPUTS
from .learning import Learner, run_episode
from .visuals import draw_map, draw_episode
from .replay import export_replay


def main(argv=None):
    parser = argparse.ArgumentParser(description="Piloto sintético de Javier Prado × Salaverry")
    parser.add_argument("command", choices=["map", "demo", "train", "evaluate", "experiment", "visual"])
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--episodes", type=int, default=25)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--seeds", default="101,102,103")
    args = parser.parse_args(argv)
    if args.seconds < 120 or args.episodes < 1:
        parser.error("Usa al menos 120 segundos y un episodio.")
    if args.command == "experiment":
        main(["train", "--episodes", str(args.episodes), "--seconds", str(args.seconds), "--seed", str(args.seed)])
        main(["evaluate", "--seconds", str(args.seconds), "--seeds", args.seeds])
        return
    layout = Layout()
    layout.save()
    OUTPUTS.mkdir(exist_ok=True)
    if args.command == "map":
        print(draw_map(layout))
    elif args.command == "demo":
        metrics = run_episode(layout, OUTPUTS / "demo", args.seed, args.seconds, trace=True)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        print(draw_episode(layout, OUTPUTS / "demo"))
    elif args.command == "visual":
        recordings = {}
        for controller in ("qlearning", "adaptive"):
            directory = OUTPUTS / "visual" / f"{controller}_{args.seed}"
            learner = Learner.load(OUTPUTS / "model.json", layout) if controller == "qlearning" else None
            metrics = run_episode(layout, directory, args.seed, args.seconds,
                                  controller=controller, learner=learner, trace=True)
            recordings[controller] = {"directory": directory, "metrics": metrics}
            print(f"Grabación {controller}: {metrics['horizon_s']} s · {metrics['arrived']} llegadas", flush=True)
        print(export_replay(layout, recordings))
    elif args.command == "train":
        learner = Learner(args.seed)
        results = []
        for episode in range(args.episodes):
            seed = args.seed + episode
            epsilon = max(0.1, 0.9 * (1 - episode / args.episodes))
            metrics = run_episode(layout, OUTPUTS / "episodes" / f"train_{seed}", seed,
                                  args.seconds, controller="qlearning", learner=learner,
                                  epsilon=epsilon, train=True)
            results.append(metrics)
            print(f"Episodio {episode + 1}/{args.episodes} · cola media {metrics['observed_mean_queue_vehicles']} · estados {len(learner.q)}", flush=True)
        learner.save(OUTPUTS / "model.json")
        (OUTPUTS / "training.json").write_text(json.dumps({
            "training_seeds": [row["seed"] for row in results], "episodes": results,
            "unvalidated_initial_training": True,
        }, ensure_ascii=False, indent=2))
        print(f"Modelo: {OUTPUTS / 'model.json'} · {learner.updates} actualizaciones")
    else:
        learner = Learner.load(OUTPUTS / "model.json", layout)
        training_seeds = set(json.loads((OUTPUTS / "training.json").read_text())["training_seeds"])
        seeds = [int(seed) for seed in args.seeds.split(",")]
        if training_seeds.intersection(seeds):
            parser.error("Los escenarios de evaluación deben usar semillas distintas del entrenamiento.")
        results = []
        for seed in seeds:
            for controller in ["fixed", "adaptive", "qlearning"]:
                metrics = run_episode(layout, OUTPUTS / "evaluation" / f"{controller}_{seed}",
                                      seed, args.seconds, controller=controller, learner=learner)
                results.append(metrics)
                print(f"{controller} · semilla {seed} · cola {metrics['observed_mean_queue_vehicles']} · llegadas {metrics['arrived']}", flush=True)
        (OUTPUTS / "evaluation.json").write_text(json.dumps({
            "note": "Los tres controladores comparten reglas de prioridad, despeje y bloqueo; fixed usa verde fijo de 20 s.",
            "results": results,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
