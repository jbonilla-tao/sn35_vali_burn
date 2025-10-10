# SN35 - Cartha Subnet

This repository is for validator and supporting tooling for Cartha. Cartha aligns cross-venue trading intelligence, liquidity signals, and incentive flows so the network can compound market depth for forex and commodities.

## What is Cartha?
- **0xMarkets engine**: Powers a multi-asset, permissionless perpetuals DEX offering up to 500x leverage across currencies, commodities, crypto, and other RWAs with USDC-only collateral.
- **Subnet-backed liquidity**: Miners act as LPs by staking USDC into market-specific vaults on Cartha, earning ALPHA emissions and 60% of trading fees for maintaining target collateral.
- **Risk & execution layer**: Validators aggregate external oracle feeds, cross-check prices, and manage liquidation workflows, keeping vaults solvent and incentives aligned on netuid `35`.

## Current alpha accumulation phase
- While the broader Cartha codebase is in active development, we operate the subnet ourselves to capture alpha that will seed future community programmes.
- Accumulated intelligence informs the design of the Cartha incentive mechanism and will underpin upcoming airdrop allocations once the subnet opens more broadly.
- Traders who engage now help validate the scoring pipeline and shape the criteria for long-term participation.

## Validator behaviour
- Validators monitor a designated miner and emit weights solely for the configured `SELECTED_MINER_HOTKEY`.

## Minimum compute requirements
| Component   | Minimum           | Recommended              |
| ----------- | ----------------- | ------------------------ |
| **CPU**     | 2 cores @ 2.2 GHz | 4 cores @ 3.0 GHz        |
| **RAM**     | 4 GB              | 8 GB                     |
| **Storage** | 2 GB free (SSD)   | 20 GB SSD                |
| **Network** | 100 / 20 Mbps     | 750 / 600 Mbps           |
| **OS**      | Debian 12         | Debian 12 / Ubuntu 22.04 |
| **GPU**     | Not required      | Optional for inference   |

## How to run the validator
1. Install Python 3.10+ and create a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.sample` to `.env` and set `SELECTED_MINER_HOTKEY` to the miner that should receive weights.
    > or run this command to set it in one go:
    ```bash
    echo "SELECTED_MINER_HOTKEY=miner_hotkey_here" >> .env
    ```
4. Create a virtual environment
   ```bash
    python -m venv venv
   ```
5. Install prerequisites
    ```bash
    pip install -r requirements.txt
    ``` 
6. Run using this command:
```bash
python neurons/validator.py \
--netuid 35 \
--wallet.name "coldkey_name" \
--wallet.hotkey "validator_hotkey" \
--logging.debug
```
[See test evidence for example](tests/evidence/TEST_EVIDENCE.md)