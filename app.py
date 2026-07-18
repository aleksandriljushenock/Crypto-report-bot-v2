import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {message}"

    print(line, flush=True)

    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def run_command(command, step_name):
    log(f"START: {step_name}")
    log(f"COMMAND: {' '.join(command)}")

    start = time.time()

    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )

    if result.stdout:
        log("STDOUT:")
        for line in result.stdout.splitlines():
            log(f"  {line}")

    if result.stderr:
        log("STDERR:")
        for line in result.stderr.splitlines():
            log(f"  {line}")

    elapsed = round(time.time() - start, 1)

    if result.returncode != 0:
        log(f"FAILED: {step_name} ({elapsed}s)")
        raise RuntimeError(f"FAILED: {step_name}")

    log(f"DONE: {step_name} ({elapsed}s)")


def find_latest_snapshot():
    files = sorted(Path("data").glob("binance_snapshot_*.json"), reverse=True)

    if not files:
        raise FileNotFoundError("Не найден binance_snapshot_*.json")

    return files[0]


def find_report_for_snapshot(snapshot_path):
    report_name = snapshot_path.stem.replace("binance_snapshot", "report") + ".txt"
    report_path = snapshot_path.with_name(report_name)

    if not report_path.exists():
        raise FileNotFoundError(f"Не найден отчет: {report_path}")

    return report_path


def main():
    log("Crypto Report Service started")
    log(f"Log file: {LOG_FILE}")

    try:
        run_command([sys.executable, "main.py"], "Binance data collection")

        snapshot_path = find_latest_snapshot()
        log(f"Snapshot ready: {snapshot_path}")

        run_command([sys.executable, "analyzer.py", str(snapshot_path)], "Market analysis")

        report_path = find_report_for_snapshot(snapshot_path)
        log(f"Report ready: {report_path}")

        run_command([sys.executable, "telegram_sender.py", str(report_path)], "Telegram delivery")

        log("SUCCESS: report sent to Telegram")

    except Exception as exc:
        log(f"ERROR: {exc}")
        raise


if __name__ == "__main__":
    main()