"""Time-of-use tariffs.

ALL RATES BELOW ARE ILLUSTRATIVE ASSUMPTIONS, chosen to be the same order of magnitude
as published US residential TOU tariffs in 2024-2025 (e.g. California electrification
TOU rates with a 4-9pm peak, and "free/cheap nights" retail plans in deregulated
markets). They are NOT a quote of any utility's current tariff. A real operator must
enter the exact tariff sheet of their own utility (see REPORT.md launch checklist).
"""
from dataclasses import dataclass, field
import numpy as np

HOURS_PER_YEAR = 8760


def hour_index(year_hours: int = HOURS_PER_YEAR):
    """Return (month[1..12], hour_of_day[0..23], weekday[0..6]) for a non-leap year
    starting on a Wednesday (2025-01-01 was a Wednesday)."""
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month = np.repeat(np.repeat(np.arange(1, 13), days_in_month), 24)[:year_hours]
    hod = np.tile(np.arange(24), 365)[:year_hours]
    weekday = np.repeat((np.arange(365) + 2) % 7, 24)[:year_hours]
    return month, hod, weekday


@dataclass(frozen=True)
class Tariff:
    name: str
    # per season: list of (start_hour, end_hour_exclusive, import $/kWh); uncovered hours use offpeak
    summer_months: tuple = (6, 7, 8, 9)
    summer: tuple = ()
    winter: tuple = ()
    summer_offpeak: float = 0.30
    winter_offpeak: float = 0.30
    export_flat: float = 0.05
    export_peak: float = 0.05          # export credit during export_peak_hours
    export_peak_hours: tuple = (16, 21)
    note: str = ""

    def prices(self, n_hours: int = HOURS_PER_YEAR):
        month, hod, _ = hour_index(n_hours)
        summer = np.isin(month, self.summer_months)
        imp = np.where(summer, self.summer_offpeak, self.winter_offpeak).astype(float)
        for (a, b, p) in self.summer:
            imp[summer & (hod >= a) & (hod < b)] = p
        for (a, b, p) in self.winter:
            imp[~summer & (hod >= a) & (hod < b)] = p
        exp = np.full(n_hours, self.export_flat, dtype=float)
        a, b = self.export_peak_hours
        exp[(hod >= a) & (hod < b)] = self.export_peak
        return imp, exp


# Illustrative California-style electrification TOU (4-9pm peak, part-peak shoulders),
# with a NEM-3.0-style low export credit.
CA_TOU_ILLUSTRATIVE = Tariff(
    name="CA_TOU_illustrative",
    summer=((15, 16, 0.44), (16, 21, 0.60), (21, 24, 0.44)),
    winter=((15, 16, 0.35), (16, 21, 0.37), (21, 24, 0.35)),
    summer_offpeak=0.38,
    winter_offpeak=0.33,
    export_flat=0.05,
    export_peak=0.10,
    note="Order-of-magnitude of 2024 CA electrification TOU; export ~ NEM3 avoided cost (assumed).",
)

# Illustrative "cheap nights" retail plan (deregulated market style).
CHEAP_NIGHTS_ILLUSTRATIVE = Tariff(
    name="cheap_nights_illustrative",
    summer=((0, 6, 0.08), (14, 20, 0.24)),
    winter=((0, 6, 0.08), (14, 20, 0.20)),
    summer_offpeak=0.16,
    winter_offpeak=0.15,
    export_flat=0.03,
    export_peak=0.03,
    note="Overnight discount plan; illustrative.",
)

# Flat tariff: arbitrage should be ~zero. The tool must say "do not bother".
FLAT_ILLUSTRATIVE = Tariff(
    name="flat_illustrative",
    summer_offpeak=0.30,
    winter_offpeak=0.30,
    export_flat=0.30,
    export_peak=0.30,
    note="Flat retail rate with full net metering: no TOU value exists.",
)

TARIFFS = {t.name: t for t in (CA_TOU_ILLUSTRATIVE, CHEAP_NIGHTS_ILLUSTRATIVE, FLAT_ILLUSTRATIVE)}
