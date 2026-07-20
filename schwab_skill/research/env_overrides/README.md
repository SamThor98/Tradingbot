# Peer-generator env overrides (PF 1.50 Track B)

JSON profiles for `scripts/run_multi_era_backtest_schwab_only.py --env-overrides`.

| File | Entry family | Notes |
|---|---|---|
| `pullback_only_aug.json` | `pullback` | Bare peer book; Stage 2 not required |
| `pead_primary_aug.json` | `pead_primary` | Earnings-beat primary; needs PEAD provider |

```bash
python scripts/run_multi_era_backtest_schwab_only.py \
  --env-overrides research/env_overrides/pullback_only_aug.json \
  --run-tag pullback_only_aug --no-resume
```

After chunks land:

```bash
python scripts/analyze_peer_generator_stack_transfer.py --run-id pullback_only_aug
```
