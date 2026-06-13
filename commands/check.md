---
description: Check ASHRAE 90.1-2022 prescriptive compliance for a building
argument-hint: [Audette building UID]
---

Run ASHRAE 90.1-2022 prescriptive compliance checks using the EnergyPlus MCP:

1. Load the building IDF via EnergyPlus MCP — use load_idf and get_model_basics
2. Check each of the 9 prescriptive items against ASHRAE 90.1-2022 minimums for the climate zone:
   - Wall U-value, Roof U-value, Slab F-factor
   - Window U-value, Window SHGC
   - Heating efficiency, Cooling efficiency, DHW efficiency
   - Lighting Power Density (LPD)
3. Show: current value, code minimum, pass/fail, and retrofit recommendation for each failure

$ARGUMENTS
