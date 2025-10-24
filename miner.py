#!/usr/bin/env python3
"""
Automated stake sweeper for Bittensor validators.

This script moves all stake from the wallet-owned hotkeys to a designated aggregator
hotkey and, on each epoch, transfers the aggregated stake to a destination coldkey.
"""

from __future__ import annotations

import signal
import sys
from dataclasses import dataclass
from threading import Event
from typing import Iterable, Optional

import bittensor as bt
from bittensor.utils.balance import Balance
from bittensor_wallet.errors import KeyFileError, PasswordError

from config import parse_miner_config
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

INFO = Fore.CYAN
WARN = Fore.YELLOW
ERR = Fore.RED
OK = Fore.GREEN
ACCENT = Fore.MAGENTA

stop_event = Event()


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
        netuid: int,
        aggregator_hotkey: str,
        destination_coldkey: str,
        wait_for_finalization: bool,
    ) -> None:
        self.subtensor = subtensor
        self.wallet = wallet
        self.netuid = netuid
        self.aggregator_hotkey = aggregator_hotkey
        self.destination_coldkey = destination_coldkey
        self.wait_for_finalization = wait_for_finalization
        self.coldkey_ss58 = wallet.coldkeypub.ss58_address
        bt.logging.debug(
            f"StakeManager initialized with netuid={netuid}, aggregator_hotkey={aggregator_hotkey}, "
            f"destination_coldkey={destination_coldkey}, wait_for_finalization={wait_for_finalization}."
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

    def ensure_aggregator_owned(self) -> bool:
        bt.logging.debug(
            f"Ensuring aggregator hotkey {self.aggregator_hotkey} is owned by coldkey {self.coldkey_ss58}."
        )
        hotkeys = self.owned_hotkeys()
        if self.aggregator_hotkey not in hotkeys:
            bt.logging.error(
                f"Aggregator hotkey {self.aggregator_hotkey} is not owned by coldkey {self.coldkey_ss58}."
            )
            return False
        return True

    def sweep_to_aggregator(self) -> bool:
        bt.logging.debug(f"Beginning sweep of stakes into aggregator hotkey {self.aggregator_hotkey}.")
        hotkeys = self.owned_hotkeys()
        if self.aggregator_hotkey not in hotkeys:
            bt.logging.error(
                f"{ERR}❌ Cannot sweep stake because aggregator hotkey {self.aggregator_hotkey} is not owned by this wallet.{Style.RESET_ALL}"
            )
            return False

        moved_any = False
        for hotkey in hotkeys:
            if hotkey == self.aggregator_hotkey:
                continue
            stake = self.fetch_stake(hotkey)
            if stake is None or stake.rao <= 0:
                bt.logging.debug(
                    f"Skipping hotkey {hotkey} due to no stake or retrieval failure."
                )
                continue

            bt.logging.info(
                f"{OK}➡️ Moving {stake} ({stake.tao:.9f} α) from hotkey {hotkey} to {self.aggregator_hotkey} on netuid {self.netuid}.{Style.RESET_ALL}"
            )
            try:
                bt.logging.debug(
                    f"Submitting move_stake extrinsic from {hotkey} to {self.aggregator_hotkey} (move_all_stake=True)."
                )
                success = self.subtensor.move_stake(
                    wallet=self.wallet,
                    origin_hotkey=hotkey,
                    origin_netuid=self.netuid,
                    destination_hotkey=self.aggregator_hotkey,
                    destination_netuid=self.netuid,
                    move_all_stake=True,
                )
            except Exception as exc:  # pylint: disable=broad-except
                bt.logging.error(
                    f"{ERR}❌ Failed to move stake from {hotkey} to {self.aggregator_hotkey}:{Style.RESET_ALL} {exc}"
                )
                success = False

            moved_any = moved_any or success
            bt.logging.debug(
                f"Move stake result for {hotkey} -> {self.aggregator_hotkey}: {success}"
            )
        return moved_any

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
        else:
            bt.logging.error(f"{ERR}❌ Stake transfer extrinsic failed on chain.{Style.RESET_ALL}")
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

    wallet = bt.wallet(config=config)
    subtensor = bt.subtensor(config=config)
    bt.logging.debug(f"Subtensor connection established to network {subtensor.network}.")

    try:
        hotkey_ss58 = wallet.hotkey.ss58_address
    except (KeyFileError, AttributeError):
        hotkey_ss58 = "<unavailable>"

    bt.logging.info(
        f"{INFO}🔑 Loaded wallet:{Style.RESET_ALL} coldkey {wallet.coldkeypub.ss58_address}, "
        f"hotkey {hotkey_ss58} on network {subtensor.network}."
    )

    unlock_wallet(wallet)
    bt.logging.debug("Wallet unlocked successfully.")

    manager = StakeManager(
        subtensor=subtensor,
        wallet=wallet,
        netuid=config.netuid,
        aggregator_hotkey=config.aggregator_hotkey,
        destination_coldkey=config.destination_coldkey,
        wait_for_finalization=config.wait_finalization,
    )

    if not manager.ensure_aggregator_owned():
        sys.exit(1)

    owned = manager.owned_hotkeys()
    bt.logging.info(
        f"{INFO}🧾 Owned hotkeys for coldkey {manager.coldkey_ss58}:{Style.RESET_ALL} {', '.join(owned) or 'none'}"
    )
    snapshots = manager.snapshot_stakes(owned)
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
    monitor_epochs(manager, config.poll_interval)
    bt.logging.info(f"{INFO}🛑 Shutdown complete.{Style.RESET_ALL}")
    bt.logging.debug("Miner script exited cleanly.")


if __name__ == "__main__":
    main()
