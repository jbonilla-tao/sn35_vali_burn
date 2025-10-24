import time

import bittensor as bt
from bittensor_wallet import Wallet

from utils.config import parse_validator_config
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

INFO = Fore.CYAN
WARN = Fore.YELLOW
ERR = Fore.RED
OK = Fore.GREEN
ACCENT = Fore.MAGENTA
BLOCK_TIME = 12


class TempValidator:
    def __init__(self):
        self.config = self.get_config()
        bt.logging(self.config)
        bt.logging.info("Validator configuration loaded.")
        bt.logging.debug(
            f"Config details: netuid={getattr(self.config, 'netuid', None)}, "
            f"target_uid={getattr(self.config, 'target_uid', None)}, "
            f"set_weights_interval={getattr(self.config, 'set_weights_interval', None)}"
        )

        # Initialize wallet.
        self.wallet = Wallet(config=self.config)
        bt.logging.info(f"{INFO}🔑 Wallet:{Style.RESET_ALL} {self.wallet}")

        # Initialize subtensor.
        self.subtensor = bt.subtensor(config=self.config)
        bt.logging.info(f"{INFO}🌐 Subtensor:{Style.RESET_ALL} {self.subtensor}")

    def get_config(self):
        return parse_validator_config()

    def get_burn_uid(self):
        # Get the subtensor owner hotkey
        bt.logging.debug(f"Querying subnet owner hotkey for netuid {self.config.netuid}.")
        sn_owner_hotkey = self.subtensor.query_subtensor(
            "SubnetOwnerHotkey",
            params=[self.config.netuid],
        )
        bt.logging.info(f"{ACCENT}🏛️ Subnet owner hotkey:{Style.RESET_ALL} {sn_owner_hotkey}")

        # Get the UID of this hotkey
        bt.logging.debug(
            f"Fetching UID for subnet owner hotkey {sn_owner_hotkey} on netuid {self.config.netuid}."
        )
        sn_owner_uid = self.subtensor.get_uid_for_hotkey_on_subnet(
            hotkey_ss58=sn_owner_hotkey,
            netuid=self.config.netuid,
        )
        bt.logging.info(f"{ACCENT}🆔 Subnet owner UID:{Style.RESET_ALL} {sn_owner_uid}")

        return sn_owner_uid

    def run(self):
        bt.logging.info(f"{OK}🚀 Running validator loop on subnet {self.config.netuid}.{Style.RESET_ALL}")

        while True:
            bt.logging.debug("Running validator loop...")

            # Check if registered.
            bt.logging.debug(
                f"Checking registration status for hotkey {self.wallet.hotkey.ss58_address} "
                f"on netuid {self.config.netuid}."
            )
            registered = self.subtensor.is_hotkey_registered_on_subnet(
                hotkey_ss58=self.wallet.hotkey.ss58_address,
                netuid=self.config.netuid,
            )
            bt.logging.info(f"{INFO}🧾 Registration status:{Style.RESET_ALL} {registered}")

            if not registered:
                bt.logging.warning(f"{WARN}⚠️ Hotkey not registered; sleeping before retry.{Style.RESET_ALL}")
                time.sleep(10)
                continue

            # Check Validator Permit
            bt.logging.debug(f"Querying validator permits for netuid {self.config.netuid}.")
            validator_permits = self.subtensor.query_subtensor(
                "ValidatorPermit",
                params=[self.config.netuid],
            ).value
            this_uid = self.subtensor.get_uid_for_hotkey_on_subnet(
                hotkey_ss58=self.wallet.hotkey.ss58_address,
                netuid=self.config.netuid,
            )
            bt.logging.info(f"{INFO}🧠 Validator UID:{Style.RESET_ALL} {this_uid}")
            bt.logging.info(f"{INFO}🎫 Validator permit:{Style.RESET_ALL} {validator_permits[this_uid]}")
            if not validator_permits[this_uid]:
                bt.logging.warning(f"{WARN}⏳ Validator permit missing; waiting for next epoch.{Style.RESET_ALL}")
                bt.logging.debug("Fetching tempo and blocks since last step for sleep calculation.")
                curr_block = self.subtensor.get_current_block()
                tempo = self.subtensor.query_subtensor(
                    "Tempo",
                    params=[self.config.netuid],
                ).value
                bt.logging.info(f"{INFO}⏱️ Subnet tempo:{Style.RESET_ALL} {tempo} blocks")
                blocks_since_last_step = self.subtensor.query_subtensor(
                    "BlocksSinceLastStep",
                    block=curr_block,
                    params=[self.config.netuid],
                ).value
                bt.logging.info(f"{INFO}📊 Blocks since last step:{Style.RESET_ALL} {blocks_since_last_step}")
                time_to_wait = (tempo - blocks_since_last_step) * BLOCK_TIME + 0.1
                bt.logging.info(f"{WARN}😴 Sleeping until next epoch (~{time_to_wait:.1f}s).{Style.RESET_ALL}")
                time.sleep(time_to_wait)
                continue

            # Get the weights version key.
            bt.logging.debug(f"Retrieving weights version key for netuid {self.config.netuid}.")
            version_key = self.subtensor.query_subtensor(
                "WeightsVersionKey",
                params=[self.config.netuid],
            ).value
            bt.logging.info(f"{INFO}🔐 Weights version key:{Style.RESET_ALL} {version_key}")

            # Check if manual UID is provided
            if self.config.target_uid is not None:
                burn_uid = self.config.target_uid
                bt.logging.info(f"{OK}🎯 Using manually specified burn UID:{Style.RESET_ALL} {burn_uid}")
            else:
                # Get the burn UID automatically
                bt.logging.debug("Auto-detecting burn UID from subnet owner hotkey.")
                burn_uid = self.get_burn_uid()
                bt.logging.info(f"{OK}🎯 Auto-detected burn UID:{Style.RESET_ALL} {burn_uid}")

            bt.logging.debug(f"Querying subnet size for netuid {self.config.netuid}.")
            subnet_n = self.subtensor.query_subtensor(
                "SubnetworkN",
                params=[self.config.netuid],
            ).value
            bt.logging.info(f"{INFO}🛰️ Subnet size:{Style.RESET_ALL} {subnet_n}")

            # Set weights to burn UID.
            uids = [burn_uid]
            weights = [1.0]
            bt.logging.debug(
                f"Prepared weight payload uids={uids} weights={weights} for netuid {self.config.netuid}."
            )

            # Set weights.
            success, message = self.subtensor.set_weights(
                self.wallet,
                self.config.netuid,
                uids,
                weights,
                version_key=version_key,
                wait_for_inclusion=True,
                wait_for_finalization=True,
            )
            if not success:
                bt.logging.error(f"{ERR}❌ Error setting weights:{Style.RESET_ALL} {message}")
                bt.logging.debug("Sleeping 10 seconds before retry due to weight error.")
                time.sleep(10)
                continue

            bt.logging.success(f"{OK}✅ Weights set successfully.{Style.RESET_ALL}")
            bt.logging.debug(f"Weights successfully set for uids={uids} on netuid {self.config.netuid}.")

            # Wait for next time to set weights.
            bt.logging.info(
                f"{INFO}⏳ Waiting {self.config.set_weights_interval} blocks before the next weight update.{Style.RESET_ALL}"
            )
            time.sleep(self.config.set_weights_interval * BLOCK_TIME)


if __name__ == "__main__":
    validator = TempValidator()
    validator.run()
