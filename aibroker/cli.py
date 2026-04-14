from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aibroker.config.loader import load_profile
from aibroker.llm.chat import chat_loop_placeholder
from aibroker.runner.orchestrator import run_once

# תיקיית הפרויקט (תיקיית `ai broker` כשהחבילה מותקנת editable מהריפו)
_PROJECT_DIR = Path(__file__).resolve().parent.parent


def _stdio_utf8() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, OSError, ValueError):
                pass


def resolve_profile_path(p: Path) -> Path:
    """מוצא את קובץ הפרופיל גם אם הרצת מספרייה אחרת מ־cwd."""
    p = Path(p)
    if p.is_file():
        return p.resolve()
    candidates = [
        Path.cwd() / p,
        _PROJECT_DIR / p,
        _PROJECT_DIR / "config" / "profiles" / p.name,
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    tried = "\n  ".join(str(x) for x in candidates)
    raise FileNotFoundError(
        f"לא נמצא קובץ פרופיל: {p}\n"
        f"ניסיתי:\n  {tried}\n\n"
        "פתרון: פתח טרמינל בתיקיית הפרויקט `ai broker` או ציין נתיב מלא:\n"
        f'  aibroker --profile "{_PROJECT_DIR / "config" / "profiles" / "paper_safe.yaml"}" web'
    )


def main(argv: list[str] | None = None) -> None:
    _stdio_utf8()
    p = argparse.ArgumentParser(prog="aibroker", description="AI broker trading scaffold")
    p.add_argument(
        "--profile",
        default="config/profiles/paper_safe.yaml",
        type=Path,
        help="Path to YAML profile",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profile", help="Print validated profile as JSON")
    sp.set_defaults(func=_cmd_profile)

    sr = sub.add_parser("run", help="Run one strategy tick")
    sr.add_argument(
        "--connect",
        action="store_true",
        help="Connect to broker when not dry_run (requires TWS/Gateway for IBKR)",
    )
    sr.set_defaults(func=_cmd_run)

    sc = sub.add_parser("chat", help="Interactive Grok chat (requires GROK_API_KEY)")
    sc.set_defaults(func=_cmd_chat)

    sw = sub.add_parser("web", help="Local dashboard in browser (install: pip install aibroker[web])")
    sw.add_argument("--host", default="127.0.0.1", help="Bind address")
    sw.add_argument("--port", type=int, default=8765, help="Port (default 8765; ignored if --auto-port)")
    sw.add_argument(
        "--auto-port",
        action="store_true",
        help="Pick first free port from 8765 upward (recommended on Windows if 8765 is stuck)",
    )
    sw.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    sw.set_defaults(func=_cmd_web)

    args = p.parse_args(argv)
    try:
        args.profile = resolve_profile_path(args.profile)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    args.func(args)


def _cmd_profile(args: argparse.Namespace) -> None:
    cfg = load_profile(args.profile)
    print(json.dumps(cfg.model_dump(), indent=2, default=str))


def _cmd_run(args: argparse.Namespace) -> None:
    cfg = load_profile(args.profile)
    run_once(cfg, connect_broker=args.connect)


def _cmd_chat(args: argparse.Namespace) -> None:
    cfg = load_profile(args.profile)
    from aibroker.state.runtime import RuntimeState

    state = RuntimeState(
        profile_name=cfg.profile_name,
        account_mode=cfg.account_mode,
        dry_run=cfg.execution.dry_run,
        kill_switch=cfg.risk.kill_switch,
    )
    chat_loop_placeholder(cfg, state)


def _cmd_web(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            'חסרות תלויות לממשק הדפדפן. הרץ:\n  pip install "aibroker[web]"',
            file=sys.stderr,
        )
        sys.exit(1)
    import aibroker.web.server as server_mod
    from aibroker.web.port_util import pick_dashboard_port

    port = pick_dashboard_port(args.host) if args.auto_port else args.port

    print(f"פרופיל: {args.profile}", flush=True)
    print(f"קובץ server.py נטען מ: {server_mod.__file__}", flush=True)
    if args.auto_port:
        print(f"פורט אוטומטי: {port} (--auto-port)", flush=True)
    app = server_mod.create_app(
        args.profile,
        port=port,
        open_browser=not args.no_browser,
    )
    base = f"http://127.0.0.1:{port}" if args.host in ("0.0.0.0", "::") else f"http://{args.host}:{port}"
    print(f"לוח בקרה: {base}/", flush=True)
    print(f"סימולציה: {base}/sim", flush=True)
    print(f"API דמו (קניות/מכירות): POST {base}/api/simulation/trade-demo", flush=True)
    print(f"נייר אוטונומי: POST {base}/api/paper/start · סטטוס GET {base}/api/paper/status", flush=True)
    print(f"בדיקה בדפדפן (GET): {base}/api/simulation/trade-demo", flush=True)
    print(f"אבחון גרסה: GET {base}/api/build-info", flush=True)
    print(f"Plan B (בדיקת נתיב): GET {base}/api/planb/status · GET {base}/api/planb/backtest/quick", flush=True)
    print("(Ctrl+C לעצירה) או הרץ עם --auto-port אם הפורט תפוס", flush=True)
    try:
        uvicorn.run(app, host=args.host, port=port, log_level="info")
    except OSError as e:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            print(
                f"\nהפורט {port} תפוס. נסה: aibroker web --auto-port\n",
                file=sys.stderr,
            )
        raise


if __name__ == "__main__":
    main(sys.argv[1:])
