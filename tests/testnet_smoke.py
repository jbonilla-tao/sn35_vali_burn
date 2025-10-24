"""
Optional smoke tests that touch the public Bittensor testnet.

These are skipped by default; set RUN_TESTNET=1 in your environment to enable them.
"""

from __future__ import annotations

import os
import unittest

import bittensor as bt


RUN_TESTNET = os.getenv("RUN_TESTNET") == "1"


class TestnetSmokeTest(unittest.TestCase):
    """Minimal connectivity checks against the Bittensor testnet."""

    @unittest.skipUnless(RUN_TESTNET, "Set RUN_TESTNET=1 to enable testnet smoke checks.")
    def test_can_query_testnet_block(self):
        subtensor = bt.subtensor(network="test")
        try:
            current_block = subtensor.get_current_block()
        finally:
            subtensor.close()

        self.assertGreater(
            current_block,
            0,
            "Expected a positive block height from the testnet.",
        )


if __name__ == "__main__":
    unittest.main()
