## How to run validator
Add SELECTED_MINER_HOTKEY in .env file

    python neurons/validator.py --netuid 78 --subtensor.network test --wallet.name test_coldkey  --wallet.hotkey test_hotkey_validator_1 --logging.debug