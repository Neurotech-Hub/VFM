#!/usr/bin/env python3
"""
run_experiment.py — CLI to load and run a SFM experiment template/script.

Examples::

    # Free-feeding against the node simulator on vcan0:
    python run_experiment.py free_feeding --interface vcan0 --nodes 3 --seconds 60

    # Load a user script that exposes ``exp`` (an Experiment instance):
    python run_experiment.py path/to/my_task.py --interface can0

    # Or a script that exposes ``build(**kwargs) -> Experiment``:
    python run_experiment.py path/to/my_task.py --nodes 1,2,4 --hours 12
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

# Ensure the package is importable when run as a script from tools/dev_gui/.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from sfm_gui.experiment import Experiment  # noqa: E402
from sfm_gui.experiment.templates import free_feeding  # noqa: E402


BUILTIN_TEMPLATES = {
    "free_feeding": free_feeding,  # factory: build(**kwargs) -> Experiment
}


def _parse_nodes(value: Optional[str], count: Optional[int]) -> List[int]:
    if value:
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    n = count if count is not None else 3
    return list(range(1, n + 1))


def _load_script(path: Path):
    """Load a .py file; return the module object."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_experiment(args: argparse.Namespace) -> Experiment:
    nodes = _parse_nodes(args.nodes, args.node_count)
    target = args.target

    # Built-in template name?
    if target in BUILTIN_TEMPLATES:
        return BUILTIN_TEMPLATES[target](
            nodes=nodes,
            reload_delay_s=args.reload_delay,
            hours=args.hours,
            minutes=args.minutes,
            seconds=args.seconds,
            max_pellets=args.max_pellets,
        )

    path = Path(target)
    if not path.exists():
        # Try as dotted module under sfm_gui.experiment.templates
        try:
            mod = importlib.import_module(f"sfm_gui.experiment.templates.{target}")
        except ImportError as exc:
            raise SystemExit(
                f"Unknown template/script '{target}'. "
                f"Built-ins: {', '.join(BUILTIN_TEMPLATES)}. "
                f"Or pass a .py path."
            ) from exc
        if hasattr(mod, "build"):
            return mod.build(
                nodes=nodes,
                hours=args.hours,
                minutes=args.minutes,
                seconds=args.seconds,
                max_pellets=args.max_pellets,
            )
        if hasattr(mod, "exp") and isinstance(mod.exp, Experiment):
            return mod.exp
        raise SystemExit(f"Module {target} has neither build() nor exp")

    module = _load_script(path)
    if hasattr(module, "exp") and isinstance(module.exp, Experiment):
        return module.exp
    if hasattr(module, "build") and callable(module.build):
        return module.build(
            nodes=nodes,
            hours=args.hours,
            minutes=args.minutes,
            seconds=args.seconds,
            max_pellets=args.max_pellets,
            reload_delay_s=getattr(args, "reload_delay", 2.0),
        )
    raise SystemExit(
        f"{path} must define either `exp = Experiment(...)` or `def build(...) -> Experiment`"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a SFM experiment (template or user script) against SocketCAN.",
    )
    parser.add_argument(
        "target",
        help="Built-in template name (e.g. free_feeding) or path to a .py script",
    )
    parser.add_argument(
        "--interface", "-i", default="can0", help="SocketCAN interface (default: can0)"
    )
    parser.add_argument(
        "--bitrate", "-b", type=int, default=250_000, help="CAN bitrate (default: 250000)"
    )
    parser.add_argument(
        "--nodes",
        default=None,
        help="Comma-separated node IDs (default: 1..N from --node-count)",
    )
    parser.add_argument(
        "--node-count",
        "-n",
        type=int,
        default=3,
        dest="node_count",
        help="Number of nodes when --nodes is omitted (default: 3)",
    )
    parser.add_argument("--hours", type=float, default=0.0, help="Session duration hours")
    parser.add_argument("--minutes", type=float, default=0.0, help="Session duration minutes")
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="Session duration seconds (0 + no pellet cap = run until Ctrl+C)",
    )
    parser.add_argument(
        "--max-pellets",
        type=int,
        default=None,
        help="End after this many pellets presented",
    )
    parser.add_argument(
        "--reload-delay",
        type=float,
        default=2.0,
        help="Free-feeding: seconds after dome close before re-dispense (default: 2)",
    )
    parser.add_argument(
        "--log-dir",
        default="~/sfm_logs",
        help="Directory for experiment CSV logs (default: ~/sfm_logs)",
    )
    parser.add_argument(
        "--no-io",
        action="store_true",
        help="Do not open GPIO / BNC (useful on non-Pi hosts)",
    )

    args = parser.parse_args(argv)
    exp = _resolve_experiment(args)

    # If user gave no duration and no pellet cap, leave end conditions unset
    # so Ctrl+C is the only stop — but free_feeding.build already called
    # end_after with zeros (no-op). Fine.

    print(
        f"Starting experiment '{exp.name}' on {args.interface} "
        f"nodes={exp.nodes}  (Ctrl+C to stop)"
    )
    ctx = exp.run(
        interface=args.interface,
        bitrate=args.bitrate,
        log_dir=args.log_dir,
        use_io=not args.no_io,
    )
    print(
        f"Session ended. pellets={ctx.counter('pellets')} "
        f"elapsed={ctx.elapsed():.1f}s"
    )
    if ctx.log_path:
        print(f"Log: {ctx.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
