"""EMA-gap trend bot for the make.fun BTC race (agent-fun/bars-replay/v1).

The bot trades Coinbase Wrapped BTC (cbBTC) against USDC on 1-minute bars.
It stays flat in cash until an uptrend is confirmed by two exponential
moving averages of the closes, then buys nearly all of its cash in one
long-only position, and sells everything when the trend stalls or the
position is stopped out.

Entry needs three pieces of evidence at once:
  * the fast EMA (15 bars) leads the slow EMA (90 bars) by at least 30 bps,
  * the fast EMA is still rising over the last 15 bars,
  * the latest close is above the slow EMA.
That much proof means the move is already several times the ~26 bps a
full-size round trip costs in fees, slippage and price impact, so the bot
does not pay that toll on chop.

Exits: sell all when the fast EMA's lead fades to +5 bps, when the
position's value drops 120 bps below its peak, or when it drops 150 bps
below cost. After any exit the bot waits 45 bars before it may buy again.

The bot is fully deterministic: it reads only the hello message, each
batch of bars and its own account state. No clock, no network, no files,
no randomness, so two runs on the same window produce byte-identical
replies. It speaks the bars-replay/v1 protocol through the vendored SDK
(vendor/makefun_bars.py, a byte-for-byte copy of the kit's shim).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
BPS = 10_000

# --- trend parameters -------------------------------------------------------
FAST_SPAN = 15        # fast EMA, in 1-minute bars
SLOW_SPAN = 75        # slow EMA, in 1-minute bars
SLOPE_BARS = 15       # fast EMA must be higher than it was this many bars ago
ENTRY_GAP_BPS = 35    # minimum fast-over-slow lead, in bps, to buy
EXIT_GAP_BPS = 5      # sell when the fast lead shrinks to this, in bps

# --- risk exits -------------------------------------------------------------
TRAIL_BPS = 120       # sell when the position falls this far below its peak value
STOP_BPS = 150        # sell when the position falls this far below its cost
COOLDOWN_BARS = 45    # bars to sit out after any exit before buying again

# --- order shaping ----------------------------------------------------------
IMPACT_CAP_BPS = 15   # size down rather than pay more one-way impact than this
BUY_SLIPPAGE_BPS = 200   # a buy may fill up to this far above the close it saw
SELL_SLIPPAGE_BPS = 300  # exits get extra room: a refused exit is the costly one


class EmaTracker:
    """A single EMA, seeded with the mean of its first span of closes."""

    def __init__(self, span):
        self.span = span
        self.alpha = 2.0 / (span + 1)
        self.value = None
        self.seed_bars = []

    def update(self, close):
        if self.value is None:
            self.seed_bars.append(close)
            if len(self.seed_bars) == self.span:
                self.value = sum(self.seed_bars) / self.span
                self.seed_bars = []
        else:
            self.value += self.alpha * (close - self.value)


class TrendBot:
    """Long-only EMA-gap trend bot: flat in cash unless the trend proves itself."""

    def __init__(self):
        self.fast = EmaTracker(FAST_SPAN)
        self.slow = EmaTracker(SLOW_SPAN)
        self.fast_history = []   # recent fast EMA values, for the rising check
        self.bars_seen = 0       # CBBTC bars processed so far
        self.in_position = False
        self.peak_value = 0      # best close-value reached by the open position
        self.no_buy_before = 0   # bar count before which no new entry is allowed

    def _observe(self, close):
        """Fold one new bar's close into both EMAs."""
        self.fast.update(close)
        self.slow.update(close)
        self.bars_seen += 1
        if self.fast.value is not None:
            self.fast_history.append(self.fast.value)
            if len(self.fast_history) > SLOPE_BARS + 1:
                del self.fast_history[0]

    def _lead_bps(self):
        """Fast EMA's lead over the slow EMA, in basis points."""
        return (self.fast.value - self.slow.value) * BPS / self.slow.value

    def _fast_rising(self):
        return (
            len(self.fast_history) > SLOPE_BARS
            and self.fast_history[-1] > self.fast_history[0]
        )

    def _sync_position(self, state):
        """Track the position the account actually holds.

        Fills land one bar after the decision, so the account -- not the
        last order sent -- is the source of truth. An exit starts the
        cooldown clock.
        """
        held = state.positions.get(SYMBOL)
        if held is not None and not self.in_position:
            self.in_position = True
            self.peak_value = state.value(SYMBOL, held.qty)
        elif held is None and self.in_position:
            self.in_position = False
            self.peak_value = 0
            self.no_buy_before = self.bars_seen + COOLDOWN_BARS

    def _build_buy(self, state):
        """A near-full-size buy, sized down if the pool is too thin."""
        size = min(state.cash, state.weight_room(SYMBOL))
        impact = state.impact_bps(SYMBOL, size)
        if impact is not None and impact > IMPACT_CAP_BPS:
            size = size * IMPACT_CAP_BPS // impact
        min_notional = int(state.hello["fees"]["min_notional_quote_units"])
        if size < min_notional:
            return None
        return {
            "side": "buy",
            "symbol": SYMBOL,
            "notional_quote_units": size,
            "max_slippage_bps": BUY_SLIPPAGE_BPS,
        }

    def _build_sell(self, held):
        return {
            "side": "sell",
            "symbol": SYMBOL,
            "qty": held.qty,
            "max_slippage_bps": SELL_SLIPPAGE_BPS,
        }

    def on_batch(self, state, batch):
        """One reply per batch: the orders decided on this bar, or none."""
        bar = None
        for candidate in batch["bars"]:
            if candidate["symbol"] == SYMBOL:
                bar = candidate
        held = state.positions.get(SYMBOL)

        if bar is not None:
            self._observe(int(bar["c"]))

        self._sync_position(state)

        # Warm-up batches cannot trade, and without a fresh bar there is
        # nothing new to decide on.
        if state.warming_up or bar is None:
            return []

        if held is not None:
            value = state.value(SYMBOL, held.qty)
            if value > self.peak_value:
                self.peak_value = value
            # Three ways out, checked every bar: the trend has stalled
            # (lead faded), the position gave back too much from its peak,
            # or it fell too far below cost. Any one of them sells the
            # whole position -- partial exits would only pay the round-trip
            # toll twice for the same trade.
            faded = (
                self.fast.value is not None
                and self.slow.value is not None
                and self._lead_bps() <= EXIT_GAP_BPS
            )
            trailed = (
                self.peak_value > 0
                and value * BPS <= self.peak_value * (BPS - TRAIL_BPS)
            )
            stopped = value * BPS <= held.cost * (BPS - STOP_BPS)
            if faded or trailed or stopped:
                return [self._build_sell(held)]
            return []

        # Flat: need the cooldown over, both EMAs live, and all three
        # entry proofs before spending a single unit of cash.
        if self.bars_seen < self.no_buy_before:
            return []
        if self.fast.value is None or self.slow.value is None:
            return []
        if self._lead_bps() < ENTRY_GAP_BPS:
            return []
        if not self._fast_rising():
            return []
        if int(bar["c"]) <= self.slow.value:
            return []
        order = self._build_buy(state)
        return [order] if order is not None else []


def main():
    makefun_bars.run(TrendBot().on_batch)


if __name__ == "__main__":
    main()
