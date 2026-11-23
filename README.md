# btc-race-bot-20261123

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/heruruben-hub/btc-race-bot-20261123/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/heruruben-hub/btc-race-bot-20261123/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)

A long-only EMA-gap trend bot that trades Coinbase Wrapped BTC (cbBTC) against USDC on 1-minute bars for make.fun's "Race a BTC trading bot live for 24 hours" bounty.

## Strategy

The bot sits in cash until an uptrend is confirmed, then buys nearly all of its cash in a single long position:

- **Entry:** the fast EMA (15 bars) leads the slow EMA (90 bars) by at least 30 bps, the fast EMA is still rising over the last 15 bars, and the latest close is above the slow EMA.
- **Exit:** sell everything when the fast EMA's lead fades to +5 bps, when the position falls 120 bps below its peak value (trailing stop), or 150 bps below cost (hard stop).
- **Cooldown:** 45 bars in cash after any exit before the next entry.
- **Sizing:** orders are sized down if the pool is thin enough that the fill's price impact would exceed 15 bps; buys tolerate 200 bps of slippage, sells 300 bps.

It speaks the agent-fun/bars-replay/v1 protocol through the vendored SDK in `vendor/makefun_bars.py` (a byte-for-byte copy of the kit's shim). Deterministic: no clock, no network, no files, no randomness — two runs on the same window produce byte-identical replies.

## Run it

```sh
python3 -P -s main.py
```

The bot reads newline-delimited JSON on stdin (a `hello`, then one `batch` per bar, then `done`) and writes one reply per batch on stdout.

<sub>Entered on [make.fun](https://make.fun/?ref=kh6903y0&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511) · AI agents build software for cash rewards</sub>
