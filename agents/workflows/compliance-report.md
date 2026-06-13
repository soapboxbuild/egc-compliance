---
name: compliance-report
description: >
  Full ASHRAE 90.1-2022 compliance report: pull Audette data, run EnergyPlus scenarios, check prescriptive items, generate HTML report artifact.
---

# Compliance Report Workflow

## Steps

1. **Setup** — verify EnergyPlus MCP connectivity, confirm building UID in Audette
2. **Data pull** — fetch building envelope and systems data from Audette
3. **Simulation** — build and run 4 EnergyPlus scenarios via EnergyPlus MCP
4. **Calibration** — if utility bills available (Arcadia/Nectar), calibrate simulation outputs
5. **Prescriptive checks** — evaluate 9 ASHRAE 90.1-2022 prescriptive items
6. **Report** — generate interactive HTML artifact with compliance table and energy charts

Pause after setup confirmation and after simulation results before proceeding to report.
