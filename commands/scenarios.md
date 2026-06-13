---
description: Run EnergyPlus simulation scenarios to compare as-built vs code minimum performance
argument-hint: [IDF file path or Audette building UID]
---

Run the four EnergyPlus compliance scenarios using the EnergyPlus MCP:

1. Load or build the as-built IDF from Audette data
2. Clone IDF three times using copy_file
3. Modify each clone for DOE Reference / Code Minimum / Retrofit using:
   - modify_lights for LPD changes
   - modify_simulation_settings for HVAC efficiency updates
   - add_output_meters for energy tracking
4. Run all four simulations
5. Compare EUI and energy cost across scenarios — show % improvement to reach code compliance

$ARGUMENTS
