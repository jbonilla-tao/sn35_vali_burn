#!/usr/bin/env python3
"""
Helper launcher that fetches the miner wallet password from Google Secret Manager
and then executes the standard neuron.miner entrypoint.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from google.cloud import secretmanager

DEFAULT_ENV_VAR = "MINER_WALLET_PASSWORD"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch miner wallet password from Secret Manager and run neuron.miner"
    )
    parser.add_argument(
        "--secret-id",
        required=True,
        help=(
            "Secret identifier. Either the full resource name "
            "'projects/<project>/secrets/<name>' or just the secret name "
            "(in which case --project must also be supplied)."
        ),
    )
    parser.add_argument(
        "--project",
        help="Optional Google Cloud project when --secret-id is only the secret name.",
    )
    parser.add_argument(
        "--version",
        default="latest",
        help="Secret Manager version to read (default: latest).",
    )
    parser.add_argument(
        "--password-env",
        default=DEFAULT_ENV_VAR,
        help=f"Environment variable used to pass the password (default: {DEFAULT_ENV_VAR}).",
    )
    parser.add_argument(
        "miner_args",
        nargs=argparse.REMAINDER,
        help=(
            "Arguments forwarded to neuron/miner.py. Separate them with '--', e.g. "
            "'run_miner_with_secret.py --secret-id ... -- --netuid 35 ...'"
        ),
    )
    return parser


def resolve_secret_path(secret_id: str, project: str | None, version: str) -> str:
    if secret_id.startswith("projects/"):
        if "/versions/" in secret_id:
            return secret_id
        return f"{secret_id}/versions/{version}"
    if not project:
        raise ValueError("When --secret-id is not a full resource name, --project must be supplied.")
    client = secretmanager.SecretManagerServiceClient()
    return client.secret_version_path(project_id=project, secret_id=secret_id, secret_version=version)


def fetch_secret(secret_path: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_path)
    return response.payload.data.decode("utf-8").strip()


def exec_miner(miner_args: Sequence[str]) -> None:
    args = list(miner_args)
    if args and args[0] == "--":
        args = args[1:]
    cmd = [sys.executable, "-m", "neuron.miner", *args]
    os.execvp(cmd[0], cmd)


def main() -> None:
    parser = build_parser()
    opts = parser.parse_args()

    secret_path = resolve_secret_path(opts.secret_id, opts.project, opts.version)
    password = fetch_secret(secret_path)
    if not password:
        raise SystemExit(f"Secret {secret_path} is empty.")

    os.environ[opts.password_env] = password
    exec_miner(opts.miner_args)


if __name__ == "__main__":
    main()
