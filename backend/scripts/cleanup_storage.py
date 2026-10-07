'''Command line storage cleanup. Dry-run is the default and never writes files.'''
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402 — CLI 入口先将 backend 加入 sys.path
from services.storage_cleanup import CleanupPolicy, CleanupSafetyError, StorageCleanup  # noqa: E402 — CLI 入口先将 backend 加入 sys.path


def _backend_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Inspect and safely clean YouTube Tool storage')
    parser.add_argument('--apply', action='store_true', help='execute the displayed cleanup plan')
    parser.add_argument('--confirm', help='required literal DELETE when --apply is used')
    parser.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    parser.add_argument('--prune-url-audio', action='store_true', help='opt in to completed URL media pruning')
    parser.add_argument('--port', type=int, default=config.SERVER_PORT, help='backend port to protect during apply')
    parser.add_argument('--staged-ttl-hours', type=int, default=config.CLEANUP_STAGED_TTL_HOURS)
    parser.add_argument('--orphan-ttl-days', type=int, default=config.CLEANUP_ORPHAN_TTL_DAYS)
    parser.add_argument('--export-ttl-days', type=int, default=config.CLEANUP_EXPORT_TTL_DAYS)
    parser.add_argument('--media-ttl-days', type=int, default=config.CLEANUP_MEDIA_TTL_DAYS)
    parser.add_argument('--quarantine-ttl-days', type=int, default=config.CLEANUP_QUARANTINE_TTL_DAYS)
    parser.add_argument('--max-items', type=int, default=config.CLEANUP_MAX_ITEMS)
    parser.add_argument('--max-bytes', type=int, default=config.CLEANUP_MAX_BYTES)
    return parser


def _policy(args: argparse.Namespace) -> CleanupPolicy:
    return CleanupPolicy(
        staged_ttl_hours=args.staged_ttl_hours,
        orphan_ttl_days=args.orphan_ttl_days,
        export_ttl_days=args.export_ttl_days,
        media_ttl_days=args.media_ttl_days,
        quarantine_ttl_days=args.quarantine_ttl_days,
        prune_url_audio=args.prune_url_audio,
        max_items=args.max_items,
        max_bytes=args.max_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply and args.confirm != 'DELETE':
        parser.error('--apply requires --confirm DELETE')
    if args.apply and _backend_is_listening(args.port):
        print(f'refusing apply while backend is listening on 127.0.0.1:{args.port}', file=sys.stderr)
        return 2
    try:
        cleanup = StorageCleanup(config.DATA_DIR, config.DOWNLOAD_DIR, policy=_policy(args))
        plan = cleanup.scan()
        if not args.apply:
            payload = plan.to_dict()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f'dry-run: {len(plan.candidates)} candidates, {plan.total_bytes} bytes')
                for item in plan.candidates:
                    print(f'  {item.category}: {item.path} ({item.size} bytes)')
            return 0
        results = cleanup.apply(plan)
        payload = {
            'dry_run': False,
            'candidate_count': len(plan.candidates),
            'results': [result.__dict__ for result in results],
            'bytes_reclaimed': sum(result.bytes_reclaimed for result in results),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            reclaimed = payload['bytes_reclaimed']
            print(f'applied: {len(results)} items, {reclaimed} bytes reclaimed')
            for result in results:
                print(f'  {result.status}: {result.category}: {result.path}')
        return 1 if any(result.status == 'skipped' for result in results) else 0
    except (CleanupSafetyError, ValueError) as exc:
        print(f'cleanup refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
