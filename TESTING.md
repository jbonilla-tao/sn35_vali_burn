# Testing Guide

The repository ships with two layers of tests:

1. **Mock flow tests** that exercise the validator and miner logic without requiring access to a live Bittensor chain.
2. **Optional testnet smoke checks** that query the public Bittensor test network.

## Mock Flow Suite (offline)

From the repository root:

```bash
python -m unittest tests.mock_flow_test
```

What it does:

- Replaces the wallet and subtensor objects with lightweight mocks.
- Verifies the validator sets weights once per loop.
- Verifies the miner moves alpha into the RT21 hotkey and transfers it to the destination coldkey.

Because everything is mocked, this suite runs entirely offline.

## Testnet Smoke Check (optional, online)

To confirm that the environment can reach the public **test** network:

```bash
RUN_TESTNET=1 python -m unittest tests.testnet_smoke
```

Notes:

- The test is skipped unless `RUN_TESTNET=1` is present in the environment.
- It instantiates `bt.subtensor(network="test")` and asserts that the current block height is positive.
- No stake-moving extrinsics are submitted; it is safe to run without funding a wallet.

Run these smoke checks only when you have network access and expect a live chain response.
