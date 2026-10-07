"""tou_battery: provable time-of-use dispatch optimizer for home batteries.

Modules:
  tariff     - TOU import/export price schedules (illustrative; user must enter their own)
  profiles   - seeded synthetic load/PV profiles (stand-in for the user's meter data)
  battery    - battery parameters
  dispatch   - exact dynamic-programming optimizer + baseline controllers + simulator
  mpc        - rolling-horizon controller using only past data (realistic forecasts)
  mv         - measurement & verification: savings from metered data vs counterfactual
  economics  - NPV, payback, break-even and analytic arbitrage thresholds
  analysis   - end-to-end runner that produces results/results.json
"""
