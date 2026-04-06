#!/usr/bin/env python3
"""Interactive launcher for sniper-bot — run this file to get a menu of all commands."""
import os
import sys
import subprocess
from pathlib import Path

# Resolve paths
ROOT = Path(__file__).parent.resolve()
CONFIG = ROOT / "config" / "example.yaml"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = ROOT / ".venv" / "bin" / "python"  # Linux/Mac

SNIPER = [str(VENV_PYTHON), "-m", "sniper_bot"]

COMMANDS = {
    "1": ("Run Bot (paper)",       SNIPER + ["run", "-c", str(CONFIG)]),
    "2": ("Run Bot (demo)",        SNIPER + ["run", "-c", str(CONFIG), "--demo"]),
    "3": ("Run Single Cycle",      SNIPER + ["run", "-c", str(CONFIG), "--once"]),
    "4": ("Scan Market",           SNIPER + ["scan", "-c", str(CONFIG)]),
    "5": ("Dashboard",             SNIPER + ["dashboard", "-c", str(CONFIG)]),
    "6": ("Status",                SNIPER + ["status", "-c", str(CONFIG)]),
    "7": ("Balance",               SNIPER + ["balance", "-c", str(CONFIG)]),
    "8": ("Report",                SNIPER + ["report", "-c", str(CONFIG)]),
    "9": ("Backtest (7d)",         SNIPER + ["backtest", "-c", str(CONFIG)]),
    "10": ("AI Analyze",           SNIPER + ["analyze", "-c", str(CONFIG)]),
    "11": ("Tune History",         SNIPER + ["tune-history", "-c", str(CONFIG)]),
    "12": ("Export CSV",           SNIPER + ["export", "-c", str(CONFIG)]),
    "13": ("Healthcheck",          SNIPER + ["healthcheck", "-c", str(CONFIG)]),
    "14": ("Reset Drawdown",       SNIPER + ["reset-drawdown", "-c", str(CONFIG)]),
    "15": ("Rollback Config",      SNIPER + ["rollback", "-c", str(CONFIG)]),
    "16": ("Delete DB & Restart",  None),  # special handler
}

BANNER = r"""
  ___  _  _ _ ___  ___ ___   ___  ___ _____
 / __|| \| | | _ \| __| _ \ | _ )/ _ \_   _|
 \__ \| .` | |  _/| _||   / | _ \ (_) || |
 |___/|_|\_|_|_|  |___|_|_\ |___/\___/ |_|  v2.0
"""


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_menu():
    clear_screen()
    print(BANNER)
    print("=" * 52)
    print("  TRADING")
    print(f"    1)  Run Bot (paper mode)")
    print(f"    2)  Run Bot (demo mode)")
    print(f"    3)  Run Single Cycle")
    print(f"    4)  Scan Market Now")
    print()
    print("  MONITORING")
    print(f"    5)  Open Dashboard (web UI)")
    print(f"    6)  Show Status")
    print(f"    7)  Show Balance")
    print(f"    8)  Strategy Report")
    print()
    print("  ANALYSIS")
    print(f"    9)  Backtest (7 days)")
    print(f"   10)  AI Strategy Analyze")
    print(f"   11)  AI Tune History")
    print(f"   12)  Export Data to CSV")
    print()
    print("  SYSTEM")
    print(f"   13)  Healthcheck")
    print(f"   14)  Reset Drawdown Halt")
    print(f"   15)  Rollback AI Config")
    print(f"   16)  Delete DB & Fresh Start")
    print()
    print(f"    q)  Quit")
    print("=" * 52)


def handle_delete_db():
    db_path = ROOT / "config" / "data" / "paper.sqlite"
    if db_path.exists():
        confirm = input(f"\n  Delete {db_path}? [y/N]: ").strip().lower()
        if confirm == "y":
            db_path.unlink()
            print("  Deleted. Run the bot to create a fresh database.")
        else:
            print("  Cancelled.")
    else:
        print("  No database file found — already clean.")
    input("\n  Press Enter to continue...")


def run_command(cmd):
    print()
    try:
        subprocess.run(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        print("\n  Stopped.")
    input("\n  Press Enter to continue...")


def main():
    if not VENV_PYTHON.exists():
        print(f"Error: Python not found at {VENV_PYTHON}")
        print("Run: python -m venv .venv && .venv/Scripts/pip install -e .")
        sys.exit(1)

    while True:
        print_menu()
        choice = input("\n  Enter choice: ").strip().lower()

        if choice == "q":
            print("\n  Goodbye!\n")
            break

        if choice not in COMMANDS:
            input("  Invalid choice. Press Enter...")
            continue

        label, cmd = COMMANDS[choice]
        if cmd is None:
            handle_delete_db()
        else:
            print(f"\n  Starting: {label}...")
            run_command(cmd)


if __name__ == "__main__":
    main()
