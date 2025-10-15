"""
Subtensor Manager - Handles round-robin network switching for subtensor connections

This class manages subtensor connections across multiple networks (local, finney, subvortex)
and automatically switches networks when operations fail.
"""

import bittensor as bt
from typing import Optional
from utils.weight_failure_classifier import WeightFailureClassifier


class SubtensorManager:
    """Manages subtensor connections with round-robin failover"""

    def __init__(self, config, slack_notifier=None, starting_network: str = 'local'):
        """
        Initialize the SubtensorManager

        Args:
            config: Bittensor config object
            slack_notifier: Optional SlackNotifier for alerts
            starting_network: Network to start with ('local', 'finney', or 'subvortex')
        """
        self.config = config
        self.slack_notifier = slack_notifier

        # Round-robin network configuration: local -> finney -> subvortex -> local
        self.round_robin_networks = ['local', 'finney', 'subvortex']

        # Set starting network
        if starting_network in self.round_robin_networks:
            self.current_round_robin_index = self.round_robin_networks.index(starting_network)
        else:
            raise Exception(f"Unknown starting network '{starting_network}'")

        # Track consecutive failures
        self.consecutive_operation_failures = 0

        # Initialize subtensor
        self.subtensor = None
        self._init_subtensor()

        print(f"SubtensorManager initialized on {self.get_current_network()}")

    def get_current_network(self) -> str:
        """Get the name of the current network"""
        return self.round_robin_networks[self.current_round_robin_index]

    def get_subtensor(self) -> bt.subtensor:
        """Get the current subtensor instance"""
        return self.subtensor

    def _init_subtensor(self):
        """Initialize subtensor connection based on current round-robin index"""
        current_network = self.get_current_network()

        if current_network == 'local':
            # Use local subtensor (default bittensor behavior with no chain_endpoint)
            self.config.subtensor.network = 'local'
            # Remove chain_endpoint if it exists to use local
            if hasattr(self.config.subtensor, 'chain_endpoint'):
                delattr(self.config.subtensor, 'chain_endpoint')
        else:
            # Use finney or subvortex
            self.config.subtensor.network = current_network
            self.config.subtensor.chain_endpoint = f"wss://entrypoint-{current_network}.opentensor.ai:443"

        self.subtensor = bt.subtensor(config=self.config)
        print(f"Subtensor initialized: {self.subtensor} (network: {current_network})")

    def _cleanup_subtensor_connection(self):
        """Safely close substrate connection to prevent file descriptor leaks"""
        if self.subtensor:
            try:
                if hasattr(self.subtensor, 'substrate') and self.subtensor.substrate:
                    print("Cleaning up substrate connection")
                    self.subtensor.substrate.close()
            except Exception as e:
                print(f"Warning: Error during substrate cleanup: {e}")

    def switch_to_next_network(self):
        """
        Switch to the next network in round-robin: local -> finney -> subvortex -> local

        This should be called when an operation fails with a non-benign error.
        """
        # Clean up existing connection
        self._cleanup_subtensor_connection()

        # Switch to next network
        old_network = self.get_current_network()
        self.current_round_robin_index = (self.current_round_robin_index + 1) % len(self.round_robin_networks)
        new_network = self.get_current_network()

        print(f"Switching subtensor from {old_network} to {new_network}")
        if self.slack_notifier:
            self.slack_notifier.send_message(
                f"Switching subtensor network from {old_network} to {new_network} due to operation failures",
                level="warning"
            )

        # Reinitialize subtensor with new network
        self._init_subtensor()

        return new_network

    def handle_operation_failure(self, error_message: str, operation_name: str = "operation") -> bool:
        """
        Handle an operation failure and determine if network should be switched

        Args:
            error_message: The error message from the failed operation
            operation_name: Name of the operation that failed (for logging)

        Returns:
            bool: True if this is a benign error (no network switch needed)
        """
        # Use centralized classifier to check if this is a benign error
        is_benign = WeightFailureClassifier.is_benign(error_message)

        if not is_benign:
            # Non-benign error: switch network
            self.consecutive_operation_failures += 1
            old_network = self.get_current_network()
            new_network = self.switch_to_next_network()
            print(f"Non-benign {operation_name} error on {old_network}. Switched to {new_network}")
            return False
        else:
            # Benign error: no network switch
            print(f"Benign error in {operation_name} - continuing without network switch")
            return True

    def handle_operation_success(self, operation_name: str = "operation"):
        """
        Handle a successful operation

        Args:
            operation_name: Name of the operation that succeeded (for logging)
        """
        # Reset failure counter on success - stay on this network!
        if self.consecutive_operation_failures > 0:
            current_network = self.get_current_network()
            print(f"{operation_name} succeeded on {current_network} after {self.consecutive_operation_failures} failures. Staying on {current_network}.")
            if self.slack_notifier:
                self.slack_notifier.send_message(
                    f"✅ {operation_name} recovered on {current_network} after {self.consecutive_operation_failures} failures",
                    level="info"
                )
            self.consecutive_operation_failures = 0

    def cleanup(self):
        """Cleanup resources on shutdown"""
        self._cleanup_subtensor_connection()
