import time
import argparse
import sys

import bittensor as bt
from bittensor_wallet import Wallet
from utils.slack_notifier import SlackNotifier
from utils.subtensor_manager import SubtensorManager
BLOCK_TIME = 12


class TempValidator:
    def __init__(self):
        self.config = self.get_config()
        # Initialize wallet first to get hotkey
        self.wallet = Wallet(config=self.config)
        print(f"Wallet: {self.wallet}")

        # Initialize SlackNotifier with hotkey and is_miner=False
        if self.config.slack_webhook_url is not None:
            self.slack_notifier = SlackNotifier(
                hotkey=self.wallet.hotkey.ss58_address,
                webhook_url=self.config.slack_webhook_url,
                is_miner=False
            )
        else:
            self.slack_notifier = None

        # Initialize SubtensorManager with round-robin failover
        # Determine starting network (default to local)
        initial_network = self.config.subtensor.get('network', 'local')
        self.subtensor_manager = SubtensorManager(
            config=self.config,
            slack_notifier=self.slack_notifier,
            starting_network=initial_network
        )
        self.subtensor = self.subtensor_manager.get_subtensor()

        self.this_uid = self.subtensor.get_uid_for_hotkey_on_subnet(
            hotkey_ss58=self.wallet.hotkey.ss58_address,
            netuid=self.config.netuid,
        )
        print(f"Validator UID: {self.this_uid}")

        # Initialize burn_uid
        self.burn_uid = self.get_burn_uid()

    def get_config(self):
        # Set up the configuration parser.
        parser = argparse.ArgumentParser(
            description="Subnet Validator",
            usage="python3 pyro.py <command> [options]",
            add_help=True,
        )
        command_parser = parser.add_subparsers(dest="command")
        run_command_parser = command_parser.add_parser(
            "run", help="""Run the validator"""
        )

        # Adds required argument for netuid with no default
        run_command_parser.add_argument(
            "--netuid", type=int, required=True, help="The chain subnet uid."
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
            default=None,  # 2 epochs
            help="Send Slack alerts here",
        )

        # Adds subtensor specific arguments.
        bt.subtensor.add_args(run_command_parser)
        # Adds wallet specific arguments.
        Wallet.add_args(run_command_parser)

        # Parse the config.
        try:
            config = bt.config(parser)
        except ValueError as e:
            print(f"Error parsing config: {e}")
            exit(1)

        # Double-check that netuid is specified
        if hasattr(config, 'command') and config.command == 'run' and not hasattr(config, 'netuid'):
            print("Error: --netuid is required but not specified")
            sys.exit(1)

        return config


    def get_burn_uid(self):
        # Get the subtensor owner hotkey
        sn_owner_hotkey = self.subtensor.query_subtensor(
            "SubnetOwnerHotkey",
            params=[self.config.netuid],
        )
        print(f"SN Owner Hotkey: {sn_owner_hotkey}")

        # Get the UID of this hotkey
        sn_owner_uid = self.subtensor.get_uid_for_hotkey_on_subnet(
            hotkey_ss58=sn_owner_hotkey,
            netuid=self.config.netuid,
        )
        print(f"SN Owner UID: {sn_owner_uid}")

        return sn_owner_uid

    def handle_no_permit(self):
        print("No Validator Permit, wait until next epoch...")
        curr_block = self.subtensor.get_current_block()
        tempo = self.subtensor.query_subtensor(
            "Tempo",
            params=[self.config.netuid],
        ).value
        print(f"Tempo: {tempo}")
        blocks_since_last_step = self.subtensor.query_subtensor(
            "BlocksSinceLastStep",
            block=curr_block,
            params=[self.config.netuid],
        ).value
        print(f"Blocks Since Last Step: {blocks_since_last_step}")
        time_to_wait = (tempo - blocks_since_last_step) * BLOCK_TIME + 0.1
        print(f"Sleeping until next epoch, {time_to_wait} seconds...")
        if self.slack_notifier:
            self.slack_notifier.record_no_permit_event()
            msg = f"Validator {self.wallet.hotkey.ss58_address} (uid {self.this_uid}) has no permit on subnet {self.config.netuid}. Sleeping until next epoch."
            self.slack_notifier.send_message(msg, level="warning")
        time.sleep(time_to_wait)

    def run(self):
        print(f"Running validator for subnet {self.config.netuid}...")

        # Track last burn UID check time
        last_burn_uid_check = time.time()
        burn_uid_check_interval = 6 * 3600  # 6 hours in seconds

        # Track last validator permit check time
        last_permit_check = time.time()
        permit_check_interval = 6 * 3600  # 6 hours in seconds
        has_validator_permit = None  # Cache permit status

        # Track last version key check time
        last_version_key_check = time.time()
        version_key_check_interval = 6 * 3600  # 6 hours in seconds
        cached_version_key = None  # Cache version key

        while True:
            print("Running validator loop...")

            # Check if 6 hours have passed since last burn UID check
            if time.time() - last_burn_uid_check >= burn_uid_check_interval:
                print("Checking for burn UID changes...")
                new_burn_uid = self.get_burn_uid()
                if new_burn_uid != self.burn_uid:
                    print(f"Burn UID changed from {self.burn_uid} to {new_burn_uid}")
                    if self.slack_notifier:
                        self.slack_notifier.record_burn_uid_change(self.burn_uid, new_burn_uid)
                    self.burn_uid = new_burn_uid
                last_burn_uid_check = time.time()

            # Check if registered.
            registered = self.subtensor.is_hotkey_registered_on_subnet(
                hotkey_ss58=self.wallet.hotkey.ss58_address,
                netuid=self.config.netuid,
            )
            print(f"Registered: {registered}")

            if not registered:
                print("Not registered, skipping...")
                if self.slack_notifier:
                    self.slack_notifier.record_registration_failure()
                time.sleep(10)
                continue

            # Check Validator Permit every 6 hours
            if has_validator_permit is None or time.time() - last_permit_check >= permit_check_interval:
                print("Checking validator permit status...")
                validator_permits = self.subtensor.query_subtensor(
                    "ValidatorPermit",
                    params=[self.config.netuid],
                ).value
                has_validator_permit = validator_permits[self.this_uid]
                last_permit_check = time.time()
                print(f"Validator Permit: {has_validator_permit}")

            if not has_validator_permit:
                self.handle_no_permit()
                # Force recheck of permit after handling no permit
                has_validator_permit = None
                continue

            # Get the weights version key every 6 hours
            if cached_version_key is None or time.time() - last_version_key_check >= version_key_check_interval:
                print("Fetching weights version key...")
                cached_version_key = self.subtensor.query_subtensor(
                    "WeightsVersionKey",
                    params=[self.config.netuid],
                ).value
                last_version_key_check = time.time()
                print(f"Weights Version Key: {cached_version_key}")

            version_key = cached_version_key

            # Set weights to burn UID.
            uids = [self.burn_uid]
            weights = [1.0]

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
                print(f"Error setting weights: {message}")

                # Use SubtensorManager to handle the failure
                is_benign = self.subtensor_manager.handle_operation_failure(message, "weight setting")

                # Track failure and send alerts
                if self.slack_notifier:
                    should_alert, failure_type = self.slack_notifier.record_weight_set_failure(message)
                    if should_alert:
                        current_network = self.subtensor_manager.get_current_network()
                        self.slack_notifier.send_weight_failure_alert(
                            error_msg=f"{message} (network: {current_network})",
                            failure_type=failure_type,
                            netuid=self.config.netuid
                        )

                # Sleep only for non-benign errors
                if not is_benign:
                    time.sleep(10)

                # Update local subtensor reference after potential network switch
                self.subtensor = self.subtensor_manager.get_subtensor()

                continue

            print("Weights set successfully.")
            # Handle success through SubtensorManager
            self.subtensor_manager.handle_operation_success("Weight setting")

            # Track success and send recovery alert if needed
            if self.slack_notifier:
                should_send_recovery = self.slack_notifier.record_weight_set_success()
                if should_send_recovery:
                    self.slack_notifier.send_weight_recovery_alert(netuid=self.config.netuid)

            # Wait for next time to set weights.
            print(
                f"Waiting {self.config.set_weights_interval} blocks before next weight set..."
            )
            time.sleep(self.config.set_weights_interval * BLOCK_TIME)


if __name__ == "__main__":
    validator = TempValidator()
    validator.run()
