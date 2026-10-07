"""Plain, auditable finance formulas (no hidden assumptions; every input is an argument)."""
from .battery import Battery


def npv(annual_saving: float, years: int, rate: float, fade: float = 0.0, capex: float = 0.0,
        annual_cost: float = 0.0) -> float:
    """NPV of a saving stream that fades by `fade`/yr (capacity loss), minus upfront capex and running cost."""
    return -capex + sum((annual_saving * (1 - fade) ** (y - 1) - annual_cost) / (1 + rate) ** y
                        for y in range(1, years + 1))


def break_even_capex(annual_saving: float, years: int, rate: float, fade: float = 0.0, annual_cost: float = 0.0) -> float:
    """Maximum upfront spend at which NPV = 0."""
    return npv(annual_saving, years, rate, fade, 0.0, annual_cost)


def simple_payback_years(capex: float, annual_saving: float, annual_cost: float = 0.0) -> float:
    net = annual_saving - annual_cost
    return float("inf") if net <= 0 else capex / net


def arbitrage_threshold_price(offpeak_price: float, b: Battery) -> float:
    """Minimum peak price at which buying 1 kWh off-peak to use at peak is profitable:
    p_peak * eta_d > p_off / eta_c + deg  (per DC kWh stored)."""
    return (offpeak_price / b.eta_charge + b.deg_cost_per_kwh) / b.eta_discharge


def solar_shift_threshold_price(export_price: float, b: Battery) -> float:
    """Minimum later import price at which storing 1 kWh of solar beats exporting it."""
    return arbitrage_threshold_price(export_price, b)


def daily_arbitrage_upper_bound(offpeak_price: float, peak_price: float, b: Battery) -> float:
    """Upper bound on $/day from one full usable cycle of grid arbitrage."""
    usable = b.capacity_kwh - b.soc_min
    per_kwh = peak_price * b.eta_discharge - offpeak_price / b.eta_charge - b.deg_cost_per_kwh
    return max(per_kwh, 0.0) * usable
