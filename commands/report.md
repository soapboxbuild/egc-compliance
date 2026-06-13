---
description: Generate an ASHRAE 90.1-2022 energy code compliance report for a building
argument-hint: [Audette building UID, or "list buildings first"]
---

Generate an ASHRAE 90.1-2022 Energy Code Compliance report:

1. Pull building envelope and systems data from Audette for the building UID
2. Use the EnergyPlus MCP to build and validate IDF files for 4 scenarios:
   - As-built (current building from Audette data)
   - DOE Reference (ASHRAE 90.1-2004 baseline)
   - Code Minimum (ASHRAE 90.1-2022 prescriptive minimum)
   - Retrofit (optimized upgrades)
3. Run all 4 simulations, calibrate to utility bills if available
4. Check prescriptive compliance: wall/roof/slab insulation, window U/SHGC, HVAC efficiency, LPD
5. Generate an HTML artifact showing the compliance table and energy comparison charts

$ARGUMENTS
