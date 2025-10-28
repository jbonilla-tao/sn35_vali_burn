"""
Shared CLI parsers for validator and miner scripts.
"""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

import bittensor as bt
from bittensor_wallet import Wallet
from bittensor.core.config import Config

# Miner defaults reused across scripts.
DEFAULT_NETUID = 35
DEFAULT_MINER_UID = 69
DEFAULT_AGGREGATOR_HOTKEY = "5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35"
DEFAULT_TRANSFER_DEST_COLDKEY = "5HLBDbdKfPCPKW33sPPyut8dPRTXA413Yp4ZRBgVKfrk4PcD"
DEFAULT_POLL_SECONDS = 30.0


def build_validator_parser() -> argparse.ArgumentParser:
    """
    Construct the argparse parser used by the validator script.
    """
    parser = argparse.ArgumentParser(
        description="Subnet Validator",
        usage="python3 burn.py <command> [options]",
        add_help=True,
    )
    command_parser = parser.add_subparsers(dest="command")
    run_command_parser = command_parser.add_parser(
        "run", help="Run the validator"
    )

    run_command_parser.add_argument(
        "--netuid", type=int, required=True, help="The chain subnet uid."
    )

    run_command_parser.add_argument(
        "--target_uid",
        type=int,
        default=DEFAULT_MINER_UID,
        help=(
            "Manually specify the target UID to burn weights to "
            "(overrides auto-detection)."
        ),
    )

    run_command_parser.add_argument(
        "--set_weights_interval",
        type=int,
        default=360 * 2,  # 2 epochs
        help="The interval to set weights in blocks.",
    )

    run_command_parser.add_argument(
        "--slack_webhook_url",
        type=str,
        default=None,
        help="Send Slack alerts here",
    )

    bt.subtensor.add_args(run_command_parser)
    Wallet.add_args(run_command_parser)
    bt.logging.add_args(run_command_parser)

    return parser


def parse_validator_config(argv: Optional[Sequence[str]] = None) -> Config:
    """
    Parse CLI arguments for the validator and return the resulting config.
    """
    parser = build_validator_parser()
    try:
        config = bt.config(parser, args=argv)
    except ValueError as exc:
        raise SystemExit(f"Error parsing config: {exc}") from exc

    if getattr(config, "command", None) == "run" and not hasattr(config, "netuid"):
        raise SystemExit("Error: --netuid is required but not specified")

    return config


def build_miner_parser() -> argparse.ArgumentParser:
    """
    Construct the argparse parser used by the miner script.
    """
    parser = argparse.ArgumentParser(
        description="Sweep stake to an aggregator hotkey and forward it each epoch."
    )

    parser.add_argument(
        "--netuid",
        type=int,
        default=DEFAULT_NETUID,
        help="Subnet netuid to operate on.",
    )
    parser.add_argument(
        "--aggregator-hotkey",
        default=DEFAULT_AGGREGATOR_HOTKEY,
        help="Hotkey SS58 that should accumulate all stake before transfers.",
    )
    parser.add_argument(
        "--destination-coldkey",
        default=DEFAULT_TRANSFER_DEST_COLDKEY,
        help="Coldkey SS58 that receives the transferred stake each epoch.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="Seconds between epoch checks.",
    )
    parser.add_argument(
        "--wait-finalization",
        action="store_true",
        help="Wait for finalization on chain extrinsics (default waits for inclusion only).",
    )
    parser.add_argument(
        "--no-initial-transfer",
        action="store_true",
        help="Skip the immediate transfer after the first sweep.",
    )
    parser.add_argument(
        "--slack_webhook_url",
        type=str,
        default=None,
        help="Slack webhook URL for notifications and monitoring.",
    )

    bt.subtensor.add_args(parser)
    Wallet.add_args(parser)
    bt.logging.add_args(parser)

    return parser


def parse_miner_config(argv: Optional[Sequence[str]] = None) -> Config:
    """
    Parse CLI arguments for the miner script and return a config object.
    """
    parser = build_miner_parser()
    try:
        config = bt.config(parser, args=argv)
    except ValueError as exc:
        raise SystemExit(f"Error parsing config: {exc}") from exc

    return config
