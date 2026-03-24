"""API key management commands."""

from __future__ import annotations

import argparse
import sys

import lore.cli._helpers as _helpers


def cmd_keys_create(args: argparse.Namespace) -> None:
    api_url, api_key = _helpers._get_api_config(args)
    payload: dict = {"name": args.name}
    if args.project:
        payload["project"] = args.project
    if getattr(args, "root", False):
        payload["is_root"] = True
    result = _helpers._api_request("POST", f"{api_url}/v1/keys", api_key, payload)
    print(f"Created key: {result['id']}")
    print(f"  Name:    {result['name']}")
    print(f"  Project: {result.get('project') or '(all)'}")
    print(f"  Key:     {result['key']}")


def cmd_keys_list(args: argparse.Namespace) -> None:
    api_url, api_key = _helpers._get_api_config(args)
    result = _helpers._api_request("GET", f"{api_url}/v1/keys", api_key)
    keys = result.get("keys", [])
    if not keys:
        print("No keys.")
        return
    print(f"{'ID':<28} {'Name':<20} {'Prefix':<14} {'Project':<15} {'Root':<6} {'Revoked'}")
    print("-" * 100)
    for k in keys:
        print(
            f"{k['id']:<28} {k['name']:<20} {k['key_prefix']:<14} "
            f"{(k.get('project') or '-'):<15} {'yes' if k['is_root'] else 'no':<6} "
            f"{'yes' if k['revoked'] else 'no'}"
        )


def cmd_keys_revoke(args: argparse.Namespace) -> None:
    api_url, api_key = _helpers._get_api_config(args)
    _helpers._api_request("DELETE", f"{api_url}/v1/keys/{args.key_id}", api_key)
    print(f"Key {args.key_id} revoked.")
