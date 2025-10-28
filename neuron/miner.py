#!/usr/bin/env python3
"""
Automated stake sweeper for Bittensor validators.

This script moves all stake from the wallet-owned hotkeys to a designated aggregator
hotkey and, on each epoch, transfers the aggregated stake to a destination coldkey.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
from dataclasses import dataclass
from threading import Event
from typing import Iterable, Optional

from getpass import getpass
from pathlib import Path

import bittensor as bt
from bittensor.utils.balance import Balance
from bittensor_wallet.errors import KeyFileError, PasswordError

from utils.config import parse_miner_config
from utils.slack_notifier import SlackNotifier
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

INFO = Fore.CYAN
WARN = Fore.YELLOW
ERR = Fore.RED
OK = Fore.GREEN
ACCENT = Fore.MAGENTA

stop_event = Event()

_PASSWORD_CACHE: dict[str, str] = {}
_PRIMARY_PASSWORD: Optional[str] = None
_PASSWORD_ENV_VARS: set[str] = set()


def _clear_password_env_vars() -> None:
    """
    Remove cached wallet passwords from the environment on shutdown.
    """
    global _PRIMARY_PASSWORD
    for env_var in list(_PASSWORD_ENV_VARS):
        os.environ.pop(env_var, None)
    _PASSWORD_ENV_VARS.clear()
    _PASSWORD_CACHE.clear()
    _PRIMARY_PASSWORD = None


atexit.register(_clear_password_env_vars)


def _load_env_file(path: Path) -> None:
    """
    Load key=value pairs from a .env-style file into the process environment.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if value and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                os.environ[key] = value
        bt.logging.debug(f"Loaded environment variables from {path}.")
    except FileNotFoundError:
        bt.logging.debug(f"No .env file found at {path}; skipping.")
    except Exception as exc:  # pylint: disable=broad-except
        bt.logging.warning(f"{WARN}⚠️ Failed to load env file {path}:{Style.RESET_ALL} {exc}")


def load_wallet_password_from_env(env_var: str, env_file: Optional[str]) -> Optional[str]:
    """
    Resolve the wallet password from environment variables (optionally seeding from a .env file).
    """
    env_var = (env_var or "MINER_WALLET_PASSWORD").strip() or "MINER_WALLET_PASSWORD"

    password = os.environ.get(env_var)
    if password:
        return password.strip()

    candidate_paths: list[Path] = []
    if env_file:
        candidate_paths.append(Path(env_file).expanduser())
    env_hint = os.environ.get("MINER_ENV_FILE")
    if env_hint:
        candidate_paths.append(Path(env_hint).expanduser())
    candidate_paths.append(Path.cwd() / ".env")
    candidate_paths.append(Path(__file__).resolve().parents[1] / ".env")

    seen: set[Path] = set()
    for candidate in candidate_paths:
        try:
            resolved = candidate.resolve()
        except Exception:  # pylint: disable=broad-except
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        _load_env_file(resolved)
        password = os.environ.get(env_var)
        if password:
            return password.strip()
    return None


def ensure_wallet_password_cached(
    wallet: "bt.wallet",
    password_source: Optional[str] = None,
    password_value: Optional[str] = None,
) -> None:
    """Prompt for the wallet password once and cache it for subsequent keyfile unlocks."""

    global _PRIMARY_PASSWORD

    if password_value and _PRIMARY_PASSWORD is None:
        stripped = password_value.strip()
        if stripped:
            _PRIMARY_PASSWORD = stripped

    keyfiles: list = []

    for attr in ("coldkey_file", "hotkey_file"):
        try:
            file_obj = getattr(wallet, attr)
        except Exception:  # pylint: disable=broad-except
            file_obj = None
        if file_obj is None:
            continue
        try:
            if file_obj.exists_on_device() and file_obj.is_encrypted():
                keyfiles.append(file_obj)
        except Exception:  # pylint: disable=broad-except
            bt.logging.debug(f"Keyfile encryption check failed for {attr}; continuing without caching.")

    if not keyfiles:
        return

    for keyfile in keyfiles:
        env_attr = getattr(keyfile, "env_var_name", None)
        if not env_attr:
            continue

        if callable(env_attr):
            try:
                env_var = env_attr()
            except TypeError:
                env_var = None
        else:
            env_var = env_attr

        if not env_var:
            continue

        env_var = str(env_var)

        cached_password = _PASSWORD_CACHE.get(env_var)
        if cached_password is None:
            if _PRIMARY_PASSWORD is None:
                if password_source:
                    try:
                        password_path = Path(password_source).expanduser()
                        file_password = password_path.read_text(encoding="utf-8").strip()
                        if file_password:
                            _PRIMARY_PASSWORD = file_password
                            bt.logging.debug(f"Loaded wallet password from file {password_path}.")
                        else:
                            bt.logging.error(
                                f"{ERR}❌ Wallet password file {password_path} is empty; cannot unlock wallet.{Style.RESET_ALL}"
                            )
                    except Exception as exc:  # pylint: disable=broad-except
                        bt.logging.error(
                            f"{ERR}❌ Failed to read wallet password file {password_source}:{Style.RESET_ALL} {exc}"
                        )
                if _PRIMARY_PASSWORD is None:
                    wallet_label = getattr(wallet, "name", None)
                    if not wallet_label:
                        try:
                            wallet_label = wallet.coldkeypub.ss58_address
                        except Exception:  # pylint: disable=broad-except
                            wallet_label = None
                    wallet_label = wallet_label or "wallet"
                    while True:
                        try:
                            _PRIMARY_PASSWORD = getpass(f"Enter password to unlock wallet {wallet_label}: ")
                        except (EOFError, OSError) as exc:
                            bt.logging.error(
                                f"{ERR}❌ Unable to prompt for wallet password (no interactive TTY). "
                                f"Provide --wallet-password-file, --wallet-password-env, or a .env file.{Style.RESET_ALL}"
                            )
                            raise SystemExit(1) from exc
                        if _PRIMARY_PASSWORD:
                            break
                        bt.logging.warning(
                            f"{WARN}⚠️ Wallet password cannot be empty; please try again.{Style.RESET_ALL}"
                        )
            cached_password = _PRIMARY_PASSWORD
            _PASSWORD_CACHE[env_var] = cached_password

        if cached_password is None:
            bt.logging.error(
                f"{ERR}❌ Wallet password could not be determined for environment variable {env_var}.{Style.RESET_ALL}"
            )
            raise SystemExit(1)

        try:
            keyfile.save_password_to_env(cached_password)
        except AttributeError:
            os.environ[env_var] = cached_password
        except Exception as exc:  # pylint: disable=broad-except
            bt.logging.error(
                f"{ERR}❌ Failed to store wallet password for {env_var}:{Style.RESET_ALL} {exc}"
            )
            raise SystemExit(1) from exc

        _PASSWORD_ENV_VARS.add(env_var)
        bt.logging.debug(f"Stored wallet password in environment variable {env_var} for auto-unlock.")


def register_signal_handlers() -> None:
    def _handle(signum: int, _frame: Optional[object]) -> None:
        bt.logging.info(f"{WARN}🚨 Received signal {signum}; shutting down.{Style.RESET_ALL}")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle)


def unlock_wallet(wallet: "bt.wallet") -> None:
    bt.logging.debug(f"Attempting to unlock coldkey for wallet {wallet}.")
    try:
        wallet.unlock_coldkey()
    except PasswordError as err:
        bt.logging.error(f"{ERR}❌ Invalid coldkey password:{Style.RESET_ALL} {err}")
        sys.exit(1)
    except KeyFileError as err:
        bt.logging.error(f"{ERR}❌ Coldkey file error:{Style.RESET_ALL} {err}")
        sys.exit(1)

    try:
        bt.logging.debug(f"Attempting to unlock hotkey for wallet {wallet}.")
        wallet.unlock_hotkey()
    except PasswordError:
        bt.logging.warning(
            f"{WARN}⚠️ Hotkey password is invalid; continuing since coldkey suffices for staking extrinsics.{Style.RESET_ALL}"
        )
    except KeyFileError:
        bt.logging.warning(
            f"{WARN}⚠️ Hotkey file missing or unreadable; continuing since coldkey suffices for staking extrinsics.{Style.RESET_ALL}"
        )


@dataclass
class StakeSnapshot:
    hotkey: str
    stake: Balance


class StakeManager:
    def __init__(
        self,
        subtensor: "bt.subtensor",
        wallet: "bt.wallet",
        primary_hotkey: str,
        netuid: int,
        aggregator_hotkey: str,
        destination_coldkey: str,
        wait_for_finalization: bool,
        slack_notifier: Optional["SlackNotifier"] = None,
    ) -> None:
        self.subtensor = subtensor
        self.wallet = wallet
        self.primary_hotkey = primary_hotkey
        self.netuid = netuid
        self.aggregator_hotkey = aggregator_hotkey
        self.destination_coldkey = destination_coldkey
        self.wait_for_finalization = wait_for_finalization
        self.slack_notifier = slack_notifier
        self.coldkey_ss58 = wallet.coldkeypub.ss58_address
        bt.logging.debug(
            f"StakeManager initialized with netuid={netuid}, primary_hotkey={primary_hotkey}, "
            f"aggregator_hotkey={aggregator_hotkey}, destination_coldkey={destination_coldkey}, "
            f"wait_for_finalization={wait_for_finalization}."
        )

    def owned_hotkeys(self) -> list[str]:
        bt.logging.debug(f"Fetching owned hotkeys for coldkey {self.coldkey_ss58}.")
        try:
            return self.subtensor.get_owned_hotkeys(self.coldkey_ss58) or []
        except Exception as exc:  # pylint: disable=broad-except
            bt.logging.error(f"{ERR}❌ Failed to retrieve owned hotkeys:{Style.RESET_ALL} {exc}")
            return []

    def fetch_stake(self, hotkey: str) -> Optional[Balance]:
        bt.logging.debug(
            f"Fetching stake for coldkey {self.coldkey_ss58} -> hotkey {hotkey} on netuid {self.netuid}."
        )
        try:
            return self.subtensor.get_stake(
                coldkey_ss58=self.coldkey_ss58,
                hotkey_ss58=hotkey,
                netuid=self.netuid,
            )
        except Exception as exc:  # pylint: disable=broad-except
            bt.logging.error(
                f"{ERR}❌ Failed to query stake for hotkey {hotkey} on netuid {self.netuid}:{Style.RESET_ALL} {exc}"
            )
            return None

    def snapshot_stakes(self, hotkeys: Iterable[str]) -> list[StakeSnapshot]:
        hotkeys_list = list(hotkeys)
        bt.logging.debug(f"Creating stake snapshot for hotkeys: {hotkeys_list}")
        snapshots: list[StakeSnapshot] = []
        for hotkey in hotkeys_list:
            stake = self.fetch_stake(hotkey)
            if stake is None:
                continue
            snapshots.append(StakeSnapshot(hotkey=hotkey, stake=stake))
        return snapshots

    def relevant_hotkeys(self) -> list[str]:
        """
        Return the set of hotkeys we actively manage (primary and aggregator) without duplicates.
        """
        ordered: list[str] = []
        for candidate in (self.primary_hotkey, self.aggregator_hotkey):
            if candidate and candidate not in ordered:
                ordered.append(candidate)
        return ordered

    def sweep_to_aggregator(self) -> bool:
        bt.logging.debug(f"Beginning sweep of stakes into aggregator hotkey {self.aggregator_hotkey}.")
        if not self.primary_hotkey:
            bt.logging.debug("No primary hotkey configured; skipping sweep.")
            return False
        if self.primary_hotkey == self.aggregator_hotkey:
            bt.logging.debug("Primary hotkey matches aggregator; skipping sweep to avoid self-transfer.")
            return False

        stake = self.fetch_stake(self.primary_hotkey)
        if stake is None or stake.rao <= 0:
            bt.logging.debug(
                f"Skipping primary hotkey {self.primary_hotkey} due to no stake or retrieval failure."
            )
            return False

        bt.logging.info(
            f"{OK}➡️ Moving {stake} ({stake.tao:.9f} α) from hotkey {self.primary_hotkey} "
            f"to {self.aggregator_hotkey} on netuid {self.netuid}.{Style.RESET_ALL}"
        )
        try:
            bt.logging.debug(
                f"Submitting move_stake extrinsic from {self.primary_hotkey} to {self.aggregator_hotkey} "
                f"(move_all_stake=True)."
            )
            success = self.subtensor.move_stake(
                wallet=self.wallet,
                origin_hotkey=self.primary_hotkey,
                origin_netuid=self.netuid,
                destination_hotkey=self.aggregator_hotkey,
                destination_netuid=self.netuid,
                move_all_stake=True,
            )
        except Exception as exc:  # pylint: disable=broad-except
            bt.logging.error(
                f"{ERR}❌ Failed to move stake from {self.primary_hotkey} to {self.aggregator_hotkey}:{Style.RESET_ALL} {exc}"
            )
            success = False

        bt.logging.debug(
            f"Move stake result for {self.primary_hotkey} -> {self.aggregator_hotkey}: {success}"
        )

        # Record metrics and send slack notification on failures
        if self.slack_notifier:
            if success:
                self.slack_notifier.record_stake_sweep_success(stake.tao)
            else:
                self.slack_notifier.record_stake_sweep_failure()
                self.slack_notifier.send_message(
                    f"❌ Stake sweep failed\n"
                    f"From: ...{self.primary_hotkey[-8:]}\n"
                    f"To: ...{self.aggregator_hotkey[-8:]}\n"
                    f"Netuid: {self.netuid}",
                    level="error"
                )

        return success

    def transfer_aggregated_stake(self) -> bool:
        bt.logging.debug(f"Preparing to transfer aggregated stake from hotkey {self.aggregator_hotkey}.")
        stake = self.fetch_stake(self.aggregator_hotkey)
        if stake is None:
            bt.logging.debug(
                f"Stake fetch for aggregator hotkey {self.aggregator_hotkey} returned None; aborting transfer."
            )
            return False
        if stake.rao <= 0:
            bt.logging.info(
                f"{WARN}⚠️ Aggregator hotkey {self.aggregator_hotkey} holds no stake on netuid {self.netuid}; skipping transfer.{Style.RESET_ALL}"
            )
            return False

        bt.logging.info(
            f"{OK}🚚 Transferring {stake} ({stake.tao:.9f} α) from hotkey {self.aggregator_hotkey} "
            f"to coldkey {self.destination_coldkey} on netuid {self.netuid}.{Style.RESET_ALL}"
        )
        try:
            success = self.subtensor.transfer_stake(
                wallet=self.wallet,
                destination_coldkey_ss58=self.destination_coldkey,
                hotkey_ss58=self.aggregator_hotkey,
                origin_netuid=self.netuid,
                destination_netuid=self.netuid,
                amount=stake,
                wait_for_inclusion=True,
                wait_for_finalization=self.wait_for_finalization,
            )
        except Exception as exc:  # pylint: disable=broad-except
            bt.logging.error(
                f"{ERR}❌ Stake transfer from {self.aggregator_hotkey} to {self.destination_coldkey} failed:{Style.RESET_ALL} {exc}"
            )
            return False

        if success:
            bt.logging.success(f"{OK}✅ Stake transfer extrinsic succeeded.{Style.RESET_ALL}")
            # Record successful transfer
            if self.slack_notifier:
                self.slack_notifier.record_stake_transfer_success(stake.tao)
        else:
            bt.logging.error(f"{ERR}❌ Stake transfer extrinsic failed on chain.{Style.RESET_ALL}")
            # Record failure and send slack notification
            if self.slack_notifier:
                self.slack_notifier.record_stake_transfer_failure()
                self.slack_notifier.send_message(
                    f"❌ Stake transfer failed\n"
                    f"Amount: {stake.tao:.9f} α\n"
                    f"From: ...{self.aggregator_hotkey[-8:]}\n"
                    f"To: ...{self.destination_coldkey[-8:]}\n"
                    f"Netuid: {self.netuid}",
                    level="error"
                )
        bt.logging.debug(
            f"Transfer stake result from {self.aggregator_hotkey} to {self.destination_coldkey}: {success}"
        )
        return success

    def process_epoch(self) -> None:
        bt.logging.info(f"{OK}🔄 Epoch boundary reached; sweeping and transferring stake.{Style.RESET_ALL}")
        bt.logging.debug("Invoking sweep_to_aggregator followed by transfer_aggregated_stake.")
        self.sweep_to_aggregator()
        self.transfer_aggregated_stake()


def monitor_epochs(manager: StakeManager, poll_interval: float) -> None:
    bt.logging.debug(
        f"Starting epoch monitor for netuid {manager.netuid} with poll interval {poll_interval:.1f}s."
    )
    next_epoch_block = manager.subtensor.get_next_epoch_start_block(manager.netuid)
    if next_epoch_block is not None:
        bt.logging.info(
            f"{INFO}📆 Next epoch boundary for netuid {manager.netuid} at block {next_epoch_block}.{Style.RESET_ALL}"
        )
    else:
        bt.logging.warning(f"{WARN}⚠️ Unable to determine next epoch start; falling back to tempo-based polling.{Style.RESET_ALL}")

    tempo: Optional[int] = None
    last_epoch_index: Optional[int] = None

    while not stop_event.is_set():
        try:
            current_block = manager.subtensor.get_current_block()
            bt.logging.debug(f"Current block height: {current_block}")
        except Exception as exc:  # pylint: disable=broad-except
            bt.logging.error(f"{ERR}❌ Failed to fetch current block:{Style.RESET_ALL} {exc}")
            stop_event.wait(poll_interval)
            continue

        triggered = False

        if next_epoch_block is not None and current_block >= next_epoch_block:
            triggered = True
        elif next_epoch_block is None:
            if tempo is None:
                try:
                    tempo = manager.subtensor.tempo(manager.netuid)
                except Exception as exc:  # pylint: disable=broad-except
                    bt.logging.error(f"{ERR}❌ Failed to fetch tempo for netuid {manager.netuid}:{Style.RESET_ALL} {exc}")
                    tempo = None

                if tempo:
                    bt.logging.info(
                        f"{INFO}📈 Subnet {manager.netuid} tempo:{Style.RESET_ALL} {tempo} blocks per epoch."
                    )

            if tempo:
                epoch_index = current_block // tempo
                bt.logging.debug(f"Computed epoch index {epoch_index} using tempo {tempo}.")
                if last_epoch_index is None:
                    last_epoch_index = epoch_index
                elif epoch_index > last_epoch_index:
                    triggered = True
                    last_epoch_index = epoch_index

        if triggered:
            manager.process_epoch()
            try:
                next_epoch_block = manager.subtensor.get_next_epoch_start_block(
                    manager.netuid, block=current_block
                )
            except Exception as exc:  # pylint: disable=broad-except
                bt.logging.error(f"{ERR}❌ Failed to compute next epoch start:{Style.RESET_ALL} {exc}")
                next_epoch_block = None

        stop_event.wait(poll_interval)


def main() -> None:
    config = parse_miner_config()
    bt.logging(config=config)
    bt.logging.info(f"{INFO}🛰️ Miner logging configured.{Style.RESET_ALL}")
    bt.logging.debug(f"Parsed miner config: {config}")
    register_signal_handlers()

    # Initialize Slack notifier
    slack_notifier: Optional[SlackNotifier] = None

    wallet = bt.wallet(config=config)
    try:
        primary_hotkey_ss58 = wallet.hotkey.ss58_address
    except (KeyFileError, AttributeError):
        primary_hotkey_ss58 = ""
    env_file = getattr(config, "env_file", None)
    password_env_var = getattr(config, "wallet_password_env", "MINER_WALLET_PASSWORD")
    password_file = getattr(config, "wallet_password_file", None)
    password_value = load_wallet_password_from_env(password_env_var, env_file)
    ensure_wallet_password_cached(
        wallet,
        password_source=password_file,
        password_value=password_value,
    )
    subtensor = bt.subtensor(config=config)
    bt.logging.debug(f"Subtensor connection established to network {subtensor.network}.")

    hotkey_ss58 = primary_hotkey_ss58 or "<unavailable>"

    bt.logging.info(
        f"{INFO}🔑 Loaded wallet:{Style.RESET_ALL} coldkey {wallet.coldkeypub.ss58_address}, "
        f"hotkey {hotkey_ss58} on network {subtensor.network}."
    )

    # Initialize SlackNotifier if webhook URL is provided
    if getattr(config, "slack_webhook_url", None):
        slack_notifier = SlackNotifier(
            hotkey=primary_hotkey_ss58 or wallet.coldkeypub.ss58_address,
            webhook_url=config.slack_webhook_url,
            is_miner=True
        )
        bt.logging.info(f"{OK}📱 Slack notifications enabled{Style.RESET_ALL}")

        # Send startup notification
        slack_notifier.send_message(
            f"🚀 Miner started on subnet {config.netuid}\n"
            f"Coldkey: ...{wallet.coldkeypub.ss58_address[-8:]}\n"
            f"Hotkey: ...{hotkey_ss58[-8:]}\n"
            f"Network: {subtensor.network}\n"
            f"Aggregator: ...{config.aggregator_hotkey[-8:]}\n"
            f"Destination: ...{config.destination_coldkey[-8:]}",
            level="info"
        )
    else:
        bt.logging.info(f"{WARN}📱 Slack notifications disabled{Style.RESET_ALL}")

    unlock_wallet(wallet)
    bt.logging.debug("Wallet unlocked successfully.")

    manager = StakeManager(
        subtensor=subtensor,
        wallet=wallet,
        primary_hotkey=primary_hotkey_ss58,
        netuid=config.netuid,
        aggregator_hotkey=config.aggregator_hotkey,
        destination_coldkey=config.destination_coldkey,
        wait_for_finalization=config.wait_finalization,
        slack_notifier=slack_notifier,
    )

    owned = manager.owned_hotkeys()
    bt.logging.info(
        f"{INFO}🧾 Owned hotkeys for coldkey {manager.coldkey_ss58}:{Style.RESET_ALL} {', '.join(owned) or 'none'}"
    )
    snapshots = manager.snapshot_stakes(manager.relevant_hotkeys())
    for snapshot in snapshots:
        bt.logging.info(
            f"{INFO}💹 Stake snapshot:{Style.RESET_ALL} {snapshot.hotkey} (netuid {config.netuid}) "
            f"= {snapshot.stake} ({snapshot.stake.tao:.9f} α)"
        )

    manager.sweep_to_aggregator()
    if not config.no_initial_transfer:
        manager.transfer_aggregated_stake()

    bt.logging.info(
        f"{OK}🛰️ Entering epoch monitoring loop (poll interval {config.poll_interval:.1f}s). Press Ctrl+C to exit.{Style.RESET_ALL}"
    )
    try:
        monitor_epochs(manager, config.poll_interval)
    finally:
        # Send shutdown notification
        if slack_notifier:
            slack_notifier.send_message(
                f"🛑 Miner stopped\n"
                f"Coldkey: ...{wallet.coldkeypub.ss58_address[-8:]}\n"
                f"Hotkey: ...{hotkey_ss58[-8:]}\n"
                f"Netuid: {config.netuid}",
                level="info"
            )
            slack_notifier.shutdown()

    bt.logging.info(f"{INFO}🛑 Shutdown complete.{Style.RESET_ALL}")
    bt.logging.debug("Miner script exited cleanly.")


if __name__ == "__main__":
    main()
