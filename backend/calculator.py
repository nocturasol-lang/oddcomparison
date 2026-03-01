"""
Betting calculators: Kelly Criterion, arbitrage, guaranteed profit, lay liability, ROI.
All functions use Decimal for precise results.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, getcontext

# Default precision for division (e.g. 28 decimal places)
getcontext().prec = 28

D = Decimal


def _to_decimal(value: int | float | str | Decimal) -> Decimal:
    """Coerce to Decimal; preserve precision from float/str."""
    if isinstance(value, Decimal):
        return value
    return D(str(value))


# ---------------------------------------------------------------------------
# Kelly Criterion
# ---------------------------------------------------------------------------


def kelly_stake(
    decimal_odds: int | float | Decimal,
    estimated_probability: int | float | Decimal,
    bankroll: int | float | Decimal,
    fraction: int | float | Decimal = 1,
) -> Decimal:
    """
    Optimal stake using the Kelly Criterion.

    f* = (bp - q) / b  where b = decimal_odds - 1, p = prob, q = 1 - p.
    Returns stake as a positive Decimal (0 if edge <= 0). Fraction is the
    Kelly fraction (e.g. 0.5 for half Kelly).

    decimal_odds: back odds (e.g. 2.5).
    estimated_probability: your estimate of true probability (0–1).
    bankroll: total bankroll to stake from.
    fraction: 1 = full Kelly; 0.5 = half Kelly.
    """
    odds = _to_decimal(decimal_odds)
    p = _to_decimal(estimated_probability)
    b = _to_decimal(bankroll)
    frac = _to_decimal(fraction)
    if odds <= 1 or p <= 0 or p >= 1 or b <= 0 or frac <= 0:
        return D("0")
    # b in formula = decimal_odds - 1 (net odds)
    net_odds = odds - 1
    q = 1 - p
    # f* = (p * (odds - 1) - q) / (odds - 1) = (p*net_odds - q) / net_odds
    edge = p * net_odds - q
    if edge <= 0:
        return D("0")
    f_star = edge / net_odds
    f_star = max(D("0"), min(f_star, D("1")))  # clamp to [0, 1]
    stake = b * f_star * frac
    return stake.quantize(D("0.000001"), rounding=ROUND_HALF_UP)


def kelly_fraction(
    decimal_odds: int | float | Decimal,
    estimated_probability: int | float | Decimal,
) -> Decimal:
    """
    Raw Kelly fraction of bankroll (0–1). Use with any bankroll: stake = bankroll * fraction.
    Returns 0 if no edge.
    """
    odds = _to_decimal(decimal_odds)
    p = _to_decimal(estimated_probability)
    if odds <= 1 or p <= 0 or p >= 1:
        return D("0")
    net_odds = odds - 1
    q = 1 - p
    edge = p * net_odds - q
    if edge <= 0:
        return D("0")
    f = edge / net_odds
    return max(D("0"), min(f, D("1"))).quantize(D("0.000001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Arbitrage
# ---------------------------------------------------------------------------


def arbitrage_percentage(*decimal_odds: int | float | Decimal) -> Decimal:
    """
    Total implied probability (overround) across outcomes.
    Sum of 1/odds for each outcome. Below 1 (100%) indicates possible arbitrage.

    Returns value in decimal form (e.g. 0.98 = 98%).
    """
    if not decimal_odds:
        return D("0")
    total = sum(1 / _to_decimal(o) for o in decimal_odds if _to_decimal(o) > 0)
    return total.quantize(D("0.000001"), rounding=ROUND_HALF_UP)


def arbitrage_profit_percent(*decimal_odds: int | float | Decimal) -> Decimal | None:
    """
    Guaranteed profit as a percentage of total stake when arbitrage exists.
    profit% = (1 / arbitrage_percentage) - 1  when arbitrage_percentage < 1.
    Returns None if no arbitrage (total implied >= 1).
    """
    total = arbitrage_percentage(*decimal_odds)
    if total >= 1 or total <= 0:
        return None
    # Profit on 1 unit total stake: (1/total) - 1
    profit_pct = (D("1") / total) - 1
    return profit_pct.quantize(D("0.000001"), rounding=ROUND_HALF_UP)


def arbitrage_stakes(
    total_stake: int | float | Decimal,
    *decimal_odds: int | float | Decimal,
) -> list[Decimal] | None:
    """
    Stake per outcome to lock in equal profit regardless of result.
    total_stake: sum of stakes across all outcomes.
    decimal_odds: one decimal odds per outcome (same order).

    Returns list of stakes, or None if no arbitrage (implied total >= 1).
    """
    if not decimal_odds or total_stake <= 0:
        return None
    odds_dec = [_to_decimal(o) for o in decimal_odds]
    if any(o <= 0 for o in odds_dec):
        return None
    inv = [1 / o for o in odds_dec]
    total_inv = sum(inv)
    if total_inv >= 1:
        return None
    stake_total = _to_decimal(total_stake)
    stakes = [stake_total * (x / total_inv) for x in inv]
    return [s.quantize(D("0.000001"), rounding=ROUND_HALF_UP) for s in stakes]


def minimum_guaranteed_profit(
    total_stake: int | float | Decimal,
    *decimal_odds: int | float | Decimal,
) -> Decimal | None:
    """
    Minimum profit guaranteed when staking for arbitrage (same amount returned
    regardless of outcome). Returns None if no arbitrage.
    """
    stakes = arbitrage_stakes(total_stake, *decimal_odds)
    if not stakes or not decimal_odds:
        return None
    # Return from first outcome: stakes[0] * odds[0]; profit = return - total_stake
    return_first = stakes[0] * _to_decimal(decimal_odds[0])
    total = _to_decimal(total_stake)
    profit = return_first - total
    return profit.quantize(D("0.000001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Lay liability
# ---------------------------------------------------------------------------


def lay_liability(
    stake: int | float | Decimal,
    lay_odds: int | float | Decimal,
) -> Decimal:
    """
    Liability on a lay bet (amount you lose if the selection wins).
    Liability = stake * (lay_odds - 1). Total payout if selection wins = stake * lay_odds.
    """
    s = _to_decimal(stake)
    o = _to_decimal(lay_odds)
    if s < 0 or o < 1:
        return D("0")
    return (s * (o - 1)).quantize(D("0.000001"), rounding=ROUND_HALF_UP)


def lay_stake_from_liability(
    liability: int | float | Decimal,
    lay_odds: int | float | Decimal,
) -> Decimal:
    """Stake required to have a given liability: stake = liability / (lay_odds - 1)."""
    liab = _to_decimal(liability)
    o = _to_decimal(lay_odds)
    if liab <= 0 or o <= 1:
        return D("0")
    return (liab / (o - 1)).quantize(D("0.000001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------


def roi_decimal(
    profit: int | float | Decimal,
    total_stake: int | float | Decimal,
) -> Decimal:
    """
    Return on investment as a decimal: profit / total_stake.
    E.g. 0.05 = 5% ROI. Returns 0 if total_stake is 0.
    """
    p = _to_decimal(profit)
    s = _to_decimal(total_stake)
    if s == 0:
        return D("0")
    return (p / s).quantize(D("0.000001"), rounding=ROUND_HALF_UP)


def roi_percent(
    profit: int | float | Decimal,
    total_stake: int | float | Decimal,
) -> Decimal:
    """ROI as a percentage (0–100 scale). E.g. 5.0 = 5%."""
    return (roi_decimal(profit, total_stake) * 100).quantize(
        D("0.000001"), rounding=ROUND_HALF_UP
    )


def roi_single_bet(
    stake: int | float | Decimal,
    decimal_odds: int | float | Decimal,
    won: bool,
) -> Decimal:
    """
    ROI for a single bet: (returns - stake) / stake.
    won: True if the bet won. Returns -1 (i.e. -100%) if lost.
    """
    s = _to_decimal(stake)
    o = _to_decimal(decimal_odds)
    if s <= 0:
        return D("0")
    if won:
        returns = s * o
        return ((returns - s) / s).quantize(D("0.000001"), rounding=ROUND_HALF_UP)
    return D("-1")


def expected_roi_decimal(
    decimal_odds: int | float | Decimal,
    estimated_probability: int | float | Decimal,
) -> Decimal:
    """
    Expected ROI (decimal) of a single bet given true probability.
    E[ROI] = p * (odds - 1) - (1 - p) = p*odds - 1.
    """
    odds = _to_decimal(decimal_odds)
    p = _to_decimal(estimated_probability)
    if odds <= 0:
        return D("0")
    return (p * odds - 1).quantize(D("0.000001"), rounding=ROUND_HALF_UP)
