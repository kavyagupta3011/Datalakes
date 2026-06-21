"""
orchestrator.py
----------------
Lightweight, dependency-free orchestration for the pipeline steps:
  build_catalog -> bronze_to_silver -> silver_to_gold -> gold_olap -> validate_olap

Each step runs as its own subprocess (the same as invoking it directly),
with retry + exponential backoff on failure. Progress is persisted to
catalog/orchestration_state.json after every step, so a crashed or
interrupted run can be resumed with --resume instead of restarting from
step 1.

This intentionally stays a single readable script rather than pulling in
a scheduler/DAG framework - that tradeoff is documented in the README.

Run:
  python src/orchestrator.py                       # run every step
  python src/orchestrator.py --resume               # skip steps that already succeeded
  python src/orchestrator.py --full-reload          # pass --full-reload to silver_to_gold.py
  python src/orchestrator.py --steps gold_olap validate_olap   # run a subset, in order given
  python src/orchestrator.py --max-retries 3 --backoff-seconds 5
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common import CATALOG_DIR, log, read_json, write_json

SRC_DIR = Path(__file__).resolve().parent
STATE_FILE = CATALOG_DIR / "orchestration_state.json"

STEPS = ["build_catalog", "bronze_to_silver", "silver_to_gold", "gold_olap", "validate_olap"]


def step_args(step: str, full_reload: bool) -> list:
    if step == "silver_to_gold" and full_reload:
        return ["--full-reload"]
    return []


def run_step(step: str, full_reload: bool, max_retries: int, backoff_seconds: float) -> dict:
    script = SRC_DIR / f"{step}.py"
    args = [sys.executable, str(script)] + step_args(step, full_reload)
    attempt = 0
    last_error = None
    started_at = datetime.now(timezone.utc).isoformat()

    while attempt <= max_retries:
        attempt += 1
        log(f">>> [{step}] attempt {attempt}/{max_retries + 1}: {' '.join(args)}")
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout, end="")

        if proc.returncode == 0:
            return {
                "status": "success",
                "attempts": attempt,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "last_error": None,
            }

        tail = (proc.stderr or proc.stdout or "").strip()
        last_error = tail.splitlines()[-1] if tail else f"non-zero exit code {proc.returncode}"
        log(f"    [{step}] FAILED (exit {proc.returncode}): {last_error}")
        if attempt <= max_retries:
            sleep_for = backoff_seconds * (2 ** (attempt - 1))
            log(f"    retrying [{step}] in {sleep_for:.1f}s ...")
            time.sleep(sleep_for)

    return {
        "status": "failed",
        "attempts": attempt,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "last_error": last_error,
    }


def run(steps=None, resume=False, full_reload=False, max_retries=2, backoff_seconds=2.0):
    steps = steps or STEPS
    state = read_json(STATE_FILE, {"steps": {}}) if resume else {"steps": {}}
    state.setdefault("steps", {})

    for step in steps:
        prior = state["steps"].get(step)
        if resume and prior and prior.get("status") == "success":
            log(f">>> [{step}] skipped (already succeeded in a previous run - --resume)")
            continue

        result = run_step(step, full_reload, max_retries, backoff_seconds)
        state["steps"][step] = result
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(STATE_FILE, state)

        if result["status"] != "success":
            log(
                f">>> Orchestration STOPPED at step '{step}' after {result['attempts']} attempt(s). "
                f"Fix the issue, then re-run with --resume to continue from here."
            )
            sys.exit(1)

    log(f"Orchestration complete: {len(steps)} step(s) processed -> {STATE_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the lakehouse pipeline steps with per-step retry and resume support."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip steps that already succeeded in the last run (per catalog/orchestration_state.json).",
    )
    parser.add_argument(
        "--full-reload", action="store_true",
        help="Pass --full-reload through to silver_to_gold.py (rebuild Gold from scratch).",
    )
    parser.add_argument(
        "--max-retries", type=int, default=2,
        help="Retries per step on failure, with exponential backoff (default: 2).",
    )
    parser.add_argument(
        "--backoff-seconds", type=float, default=2.0,
        help="Base seconds for exponential backoff between retries (default: 2.0).",
    )
    parser.add_argument(
        "--steps", nargs="+", choices=STEPS,
        help=f"Run only this subset of steps, in the order given. Choices: {STEPS}",
    )
    args = parser.parse_args()
    run(
        steps=args.steps,
        resume=args.resume,
        full_reload=args.full_reload,
        max_retries=args.max_retries,
        backoff_seconds=args.backoff_seconds,
    )
