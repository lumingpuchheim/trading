"""Comdirect order costs (SIMULATOR_SPEC section 4).

Commission = base + pct x volume, clamped to [min, max]; the venue fee
is added on top of the clamped commission, not inside the clamp.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def load_sim_config() -> dict:
    with open(Path(__file__).parent / 'config_sim.yaml', encoding='utf-8') as f:
        return yaml.safe_load(f)


def order_fee(volume_eur: float, cfg: dict) -> float:
    """Total charge for one executed order of `volume_eur` (EUR)."""
    b = cfg['broker']
    commission = b['commission_base_eur'] + b['commission_pct'] * volume_eur
    commission = min(max(commission, b['commission_min_eur']),
                     b['commission_max_eur'])
    return round(commission + b['venue_fee_eur'], 2)


def affordable_shares(cash_eur: float, price_eur: float, cfg: dict) -> int:
    """Largest whole share count buyable with `cash_eur` including fees."""
    if price_eur <= 0:
        return 0
    qty = int(cash_eur // price_eur)
    while qty > 0 and qty * price_eur + order_fee(qty * price_eur, cfg) > cash_eur:
        qty -= 1
    return qty
