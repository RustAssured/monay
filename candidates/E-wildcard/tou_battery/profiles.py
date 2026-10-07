"""Seeded synthetic hourly household load and rooftop PV profiles.

These are stand-ins for a real household's smart-meter (Green Button) export.
ASSUMPTIONS (labeled): ~7,000 kWh/yr household load with an evening peak and summer
cooling; 6 kW PV producing ~1,500 kWh/kW-yr (typical sunny-climate yield) with random
cloudiness. Deterministic given the seed.
"""
import numpy as np
from .tariff import hour_index, HOURS_PER_YEAR


def synthetic_load(seed: int = 0, n_hours: int = HOURS_PER_YEAR, annual_kwh: float = 7000.0):
    rng = np.random.default_rng(seed)
    month, hod, weekday = hour_index(n_hours)
    shape = (0.35
             + 0.35 * np.exp(-0.5 * ((hod - 7.5) / 1.5) ** 2)
             + 0.90 * np.exp(-0.5 * ((hod - 19.0) / 2.2) ** 2))
    shape = shape * np.where(weekday >= 5, 1.1, 1.0)
    cooling = np.where(np.isin(month, (6, 7, 8, 9)), 1.0, 0.0) * 0.8 * np.exp(-0.5 * ((hod - 17) / 3.0) ** 2)
    heating = np.where(np.isin(month, (12, 1, 2)), 0.25, 0.0)
    daily_noise = np.repeat(rng.lognormal(0, 0.15, n_hours // 24 + 1), 24)[:n_hours]
    hourly_noise = rng.lognormal(0, 0.25, n_hours)
    load = (shape + cooling + heating) * daily_noise * hourly_noise
    return load * (annual_kwh / load.sum())


def synthetic_pv(seed: int = 0, n_hours: int = HOURS_PER_YEAR, kw_dc: float = 6.0, yield_kwh_per_kw: float = 1500.0):
    if kw_dc <= 0:
        return np.zeros(n_hours)
    rng = np.random.default_rng(seed + 10_000)
    month, hod, _ = hour_index(n_hours)
    doy = np.repeat(np.arange(365), 24)[:n_hours]
    daylen = 12 + 2.5 * np.sin(2 * np.pi * (doy - 80) / 365)       # hours
    sunrise = 12 - daylen / 2
    x = (hod + 0.5 - sunrise) / daylen
    shape = np.where((x > 0) & (x < 1), np.sin(np.pi * np.clip(x, 0, 1)) ** 1.5, 0.0)
    season = 0.75 + 0.25 * np.sin(2 * np.pi * (doy - 80) / 365)
    clouds = np.repeat(np.clip(rng.beta(5, 1.5, n_hours // 24 + 1), 0.1, 1.0), 24)[:n_hours]
    pv = shape * season * clouds
    return pv * (kw_dc * yield_kwh_per_kw / pv.sum())
