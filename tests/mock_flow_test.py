"""
Mock-based tests that exercise the validator and miner flows without hitting the real
Bittensor network or wallet files.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bittensor.utils.balance import Balance

import neuron.miner as miner
import neuron.validator as validator
import utils.config as config


class FlowHalt(RuntimeError):
    """Raised to halt an infinite loop once the scenario has been exercised."""


class FakeWallet:
    """Minimal wallet stub compatible with the scripts."""

    def __init__(self, name: str | None = None, hotkey: str | None = None, path=None, config: object | None = None):
        if config is not None and hasattr(config, "wallet"):
            wallet_cfg = getattr(config, "wallet")
            name = getattr(wallet_cfg, "name", name)
            hotkey = getattr(wallet_cfg, "hotkey", hotkey)

        self.name = name or "mock_wallet"
        self.hotkey_name = hotkey or "mock_hotkey"
        self.coldkeypub = SimpleNamespace(ss58_address=f"cold-{self.name}")
        self.hotkey = SimpleNamespace(ss58_address=f"hot-{self.hotkey_name}")

    def unlock_coldkey(self):
        return None

    def unlock_hotkey(self):
        return None

    @staticmethod
    def add_args(parser):
        parser.add_argument("--wallet.name", default="mock_wallet")
        parser.add_argument("--wallet.hotkey", default="mock_hotkey")
        parser.add_argument("--wallet.path", default=None)


def fake_bt_wallet(*, config=None, **kwargs) -> FakeWallet:
    return FakeWallet(config=config, **kwargs)


class FakeSubtensor:
    """Simulates the chain interactions the scripts rely on."""

    def __init__(self, *, network: str | None = None, config: object | None = None, **kwargs):
        wallet_cfg = getattr(config, "wallet", SimpleNamespace(name="mock_wallet", hotkey="mock_hotkey"))
        self.coldkey = f"cold-{getattr(wallet_cfg, 'name', 'mock_wallet')}"
        self.primary_hotkey = getattr(wallet_cfg, "hotkey", "mock_hotkey")
        self.aggregator_hotkey = getattr(config, "aggregator_hotkey", "mock_aggregator")
        self.destination_coldkey = getattr(config, "destination_coldkey", "mock_destination")
        self.network = network or getattr(getattr(config, "subtensor", SimpleNamespace()), "network", "mocknet")

        owned_alt_hotkey = f"{self.primary_hotkey}-alt"
        self.owned_hotkeys = [self.aggregator_hotkey, owned_alt_hotkey]
        self._stakes = {
            (self.coldkey, self.primary_hotkey): Balance.from_tao(5),
            (self.coldkey, owned_alt_hotkey): Balance.from_tao(5),
            (self.coldkey, self.aggregator_hotkey): Balance.from_tao(0),
        }

        self.set_weights_calls = 0
        self.move_history: list[tuple[str, str, float]] = []
        self.transfer_history: list[tuple[str, str, float]] = []

        self._current_block = 107
        self._next_epoch_block = 109
        self._tempo = 5

    # ----- Helpers used by validator -------------------------------------------------
    def is_hotkey_registered_on_subnet(self, hotkey_ss58: str, netuid: int) -> bool:
        return True

    def query_subtensor(self, name: str, params=None, block=None):
        if name == "SubnetOwnerHotkey":
            return "mock_owner_hotkey"
        if name == "ValidatorPermit":
            return SimpleNamespace(value=[True])
        if name == "Tempo":
            return SimpleNamespace(value=self._tempo)
        if name == "BlocksSinceLastStep":
            return SimpleNamespace(value=1)
        if name == "WeightsVersionKey":
            return SimpleNamespace(value=123456)
        if name == "SubnetworkN":
            return SimpleNamespace(value=35)
        raise KeyError(f"Unsupported query {name}")

    def get_uid_for_hotkey_on_subnet(self, hotkey_ss58: str, netuid: int) -> int:
        return 0

    def get_current_block(self) -> int:
        self._current_block += 1
        return self._current_block

    def set_weights(self, wallet, netuid, uids, weights, version_key, wait_for_inclusion, wait_for_finalization):
        self.set_weights_calls += 1
        return True, "ok"

    # ----- Helpers used by miner -----------------------------------------------------
    def get_owned_hotkeys(self, coldkey_ss58: str, block=None, reuse_block=False):
        return list(self.owned_hotkeys)

    def get_stake(self, coldkey_ss58: str, hotkey_ss58: str, netuid: int, block=None):
        return self._stakes.get((coldkey_ss58, hotkey_ss58), Balance.from_tao(0))

    def move_stake(
        self,
        wallet,
        origin_hotkey: str,
        origin_netuid: int,
        destination_hotkey: str,
        destination_netuid: int,
        amount=None,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        period=None,
        move_all_stake=False,
    ):
        origin_key = (self.coldkey, origin_hotkey)
        destination_key = (self.coldkey, destination_hotkey)
        origin_balance = self._stakes.get(origin_key, Balance.from_tao(0))
        amount_balance = origin_balance if move_all_stake or amount is None else amount
        self._stakes[origin_key] = Balance.from_tao(origin_balance.tao - amount_balance.tao)
        dest_balance = self._stakes.get(destination_key, Balance.from_tao(0))
        self._stakes[destination_key] = Balance.from_tao(dest_balance.tao + amount_balance.tao)
        self.move_history.append((origin_hotkey, destination_hotkey, amount_balance.tao))
        return True

    def transfer_stake(
        self,
        wallet,
        destination_coldkey_ss58: str,
        hotkey_ss58: str,
        origin_netuid: int,
        destination_netuid: int,
        amount: Balance,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        period=None,
    ):
        self.transfer_history.append((hotkey_ss58, destination_coldkey_ss58, amount.tao))
        self._stakes[(self.coldkey, hotkey_ss58)] = Balance.from_tao(0)
        return True

    def get_next_epoch_start_block(self, netuid: int, block=None):
        return self._next_epoch_block

    def tempo(self, netuid: int, block=None) -> int:
        return self._tempo


class FakeSubtensorFactory:
    """Callable with an add_args hook to satisfy the config parser."""

    def __init__(self):
        self.instances: list[FakeSubtensor] = []

    def __call__(self, *args, **kwargs):
        instance = FakeSubtensor(*args, **kwargs)
        self.instances.append(instance)
        return instance

    @staticmethod
    def add_args(parser):
        parser.add_argument("--subtensor.network", default="mocknet")


class FakeEvent:
    """Stops the miner epoch monitor after a single iteration."""

    def __init__(self):
        self._set = False
        self.wait_calls = 0

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, interval: float) -> bool:
        self.wait_calls += 1
        if self.wait_calls >= 1:
            self._set = True
        return True


class MockFlowTest(unittest.TestCase):
    """End-to-end sanity checks for validator and miner using mock infrastructure."""

    def test_validator_single_iteration(self):
        factory = FakeSubtensorFactory()
        validator_config = SimpleNamespace(
            netuid=35,
            target_uid=None,
            set_weights_interval=720,
            wallet=SimpleNamespace(name="mock_wallet", hotkey="mock_hotkey", path=None),
            subtensor=SimpleNamespace(network="mocknet"),
            logging=SimpleNamespace(
                debug=True, trace=False, info=False, record_log=False, logging_dir="~/.bittensor/miners"
            ),
        )

        with patch.object(validator, "Wallet", FakeWallet), patch.object(validator.bt, "subtensor", factory):
            with patch.object(validator, "parse_validator_config", return_value=validator_config):
                with patch("neuron.validator.time.sleep", side_effect=FlowHalt):
                    validator_instance = validator.TempValidator()
                    with self.assertRaises(FlowHalt):
                        validator_instance.run()

        sub = factory.instances[-1]
        self.assertEqual(sub.set_weights_calls, 1, "Validator should set weights exactly once in the mock test.")

    def test_miner_flow(self):
        factory = FakeSubtensorFactory()
        fake_event = FakeEvent()
        miner_config = SimpleNamespace(
            netuid=35,
            aggregator_hotkey="agg_hotkey",
            destination_coldkey="dest_coldkey",
            poll_interval=0.1,
            wait_finalization=False,
            no_initial_transfer=False,
            wallet=SimpleNamespace(name="mock_wallet", hotkey="mock_hotkey", path=None),
            subtensor=SimpleNamespace(network="mocknet"),
            logging=SimpleNamespace(
                debug=True, trace=False, info=False, record_log=False, logging_dir="~/.bittensor/miners"
            ),
        )

        with patch.object(miner.bt, "subtensor", factory), patch.object(miner.bt, "wallet", fake_bt_wallet):
            with patch.object(miner, "stop_event", fake_event), patch("neuron.miner.signal.signal"):
                with patch.object(miner, "parse_miner_config", return_value=miner_config):
                    miner.main()

        sub = factory.instances[-1]
        self.assertGreaterEqual(
            len(sub.move_history),
            1,
            "Miner sweep should move stake at least once in the mock scenario.",
        )
        self.assertGreaterEqual(
            len(sub.transfer_history),
            1,
            "Miner flow should transfer stake to the destination coldkey during the mock test.",
        )
        aggregator_balance = sub.get_stake(sub.coldkey, sub.aggregator_hotkey, netuid=35)
        self.assertEqual(
            aggregator_balance.rao,
            0,
            "Aggregator stake should be emptied after transfer in the mock flow.",
        )


class ConfigParsingTests(unittest.TestCase):
    """Ensure CLI parser handles network overrides."""

    def test_miner_subtensor_network(self):
        cfg = config.parse_miner_config(["--subtensor.network", "test"])
        self.assertEqual(
            getattr(cfg.subtensor, "network", None),
            "test",
            "--subtensor.network should populate the miner config network.",
        )


if __name__ == "__main__":
    unittest.main()
