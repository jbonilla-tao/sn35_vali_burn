## How to run validator
Add `SELECTED_MINER_HOTKEY` to the `.env` file, then launch:

```
python neurons/validator.py --netuid 35 --wallet.name <coldkey_name> --wallet.hotkey <validator_hotkey> --logging.debug
```
