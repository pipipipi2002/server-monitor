"""CLI entry: `server-monitor-agent run` and `server-monitor-agent enroll`."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

from server_monitor_agent.client import Client
from server_monitor_agent.run import run_loop
from server_monitor_agent.token_store import default_token_path, load_token, save_token


def _build_client(base_url: str, ca_bundle: str | None) -> Client:
    verify: bool | str = ca_bundle if ca_bundle else True
    return Client(base_url=base_url, verify=verify)


async def _cmd_enroll(args: argparse.Namespace) -> int:
    client = _build_client(args.monitor_url, args.ca_bundle)
    try:
        token = await client.enroll(
            hostname=args.hostname, enrollment_token=args.enrollment_token
        )
    finally:
        await client.aclose()
    save_token(args.token_file, token)
    print(f"enrolled; token saved to {args.token_file}")
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    token = load_token(args.token_file)
    if not token:
        print(f"no token at {args.token_file}; run 'enroll' first", file=sys.stderr)
        return 2
    client = _build_client(args.monitor_url, args.ca_bundle)
    try:
        await run_loop(client=client, hostname=args.hostname, token=token, interval=args.interval)
    finally:
        await client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # When SCM launches a Windows service, the binary is invoked with no extra
    # arguments. Detect that and hand off to pywin32's service control
    # dispatcher (defined in service_windows.py). Configuration flows in via
    # environment variables that install.ps1 writes into the service's
    # registry Environment block.
    if not argv and sys.platform == "win32":
        try:
            from server_monitor_agent.service_windows import enter_service_dispatcher
        except ImportError as e:
            print(f"service mode requires pywin32: {e!r}", file=sys.stderr)
            return 2
        return enter_service_dispatcher()

    p = argparse.ArgumentParser(prog="server-monitor-agent")
    p.add_argument(
        "--monitor-url",
        default=os.environ.get("MONITOR_URL", "https://monitor.lan"),
    )
    p.add_argument("--hostname", default=socket.gethostname())
    p.add_argument("--token-file", type=Path, default=default_token_path())
    p.add_argument("--ca-bundle", default=None, help="path to monitor CA cert")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("enroll")
    pe.add_argument("--enrollment-token", required=True)
    pe.set_defaults(func=_cmd_enroll)

    pr = sub.add_parser("run")
    pr.add_argument("--interval", type=float, default=5.0)
    pr.set_defaults(func=_cmd_run)

    args = p.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
