from dataclasses import dataclass


@dataclass(frozen=True)
class Battery:
    """Home battery parameters. Defaults approximate a common 13.5 kWh / 5 kW AC unit.

    ASSUMPTIONS (labeled): round-trip efficiency ~90% (manufacturer spec sheets for
    this class of product typically quote ~89-90% AC round trip); degradation cost
    $0.03 per kWh discharged (see REPORT.md: ~$9k replacement / (13.5 kWh * ~4000
    equivalent full cycles * 0.8 avg usable) ~= $0.02-0.05/kWh).
    """
    capacity_kwh: float = 13.5
    reserve_frac: float = 0.20      # SoC kept for backup (outages) - never dispatched
    power_kw: float = 5.0           # AC charge/discharge limit
    eta_charge: float = 0.95        # AC->DC
    eta_discharge: float = 0.95     # DC->AC   (round trip = 0.9025)
    deg_cost_per_kwh: float = 0.03  # $ per kWh DC discharged
    allow_grid_charge: bool = True
    allow_battery_export: bool = False  # conservative: battery only serves home load

    @property
    def soc_min(self) -> float:
        return self.capacity_kwh * self.reserve_frac

    @property
    def soc_max(self) -> float:
        return self.capacity_kwh

    @property
    def round_trip(self) -> float:
        return self.eta_charge * self.eta_discharge

    def replace(self, **kw) -> "Battery":
        d = self.__dict__.copy()
        d.update(kw)
        return Battery(**d)
