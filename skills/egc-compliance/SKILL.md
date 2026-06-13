---
name: egc-compliance
description: Generate ASHRAE 90.1-2022 Energy Code Compliance reports for buildings. Uses the EnergyPlus MCP to run four simulation scenarios (as-built, DOE reference, code minimum, retrofit), calibrates to actual utility data, checks prescriptive compliance, and renders an interactive HTML report artifact. Use when asked about energy code compliance, ASHRAE 90.1, EnergyPlus simulation, prescriptive compliance checks, or comparing a building to code minimum. Requires the Audette plugin and EnergyPlus MCP token.
---

# EGC Compliance — ASHRAE 90.1-2022

## What It Does
Generates a full energy code compliance report by:
1. Pulling building envelope and systems data from Audette
2. Building EnergyPlus IDF files for 4 scenarios via the EnergyPlus MCP
3. Running simulations and calibrating to actual utility bills
4. Checking 9 prescriptive compliance items against ASHRAE 90.1-2022
5. Producing an interactive HTML report artifact

## EnergyPlus MCP Tools Used

### IDF Management
- `load_idf(idf_path)` — Load an EnergyPlus input file
- `validate_idf(idf_path)` — Validate IDF syntax and content
- `get_model_basics(idf_path)` — Summary of zones, surfaces, systems

### Building Geometry
- `list_zones(idf_path)` — All thermal zones
- `get_surfaces(idf_path)` — Walls, roofs, floors, windows
- `get_materials(idf_path)` — Material and construction properties

### Building Systems
- `inspect_lights(idf_path)` — Lighting power density
- `modify_lights(idf_path, modifications)` — Update LPD for scenarios
- `inspect_people(idf_path)` — Occupancy schedules
- `inspect_electric_equipment(idf_path)` — Plug loads
- `discover_hvac_loops(idf_path)` — HVAC topology
- `get_loop_topology(idf_path, loop_name)` — Detailed HVAC layout

### Simulation
- `modify_simulation_settings(idf_path, object_type, field_updates)` — Configure run settings
- `add_output_variables(idf_path, variables)` — Add simulation outputs
- `add_output_meters(idf_path, meters)` — Add energy meters
- `copy_file(source, target)` — Clone IDF for each scenario

## The 4 Scenarios
| Scenario | Description | Purpose |
|----------|-------------|---------|
| As-Built | Current building (from Audette data) | Baseline |
| DOE Reference | ASHRAE 90.1-2004 baseline | Permit comparison |
| Code Minimum | ASHRAE 90.1-2022 prescriptive min | Compliance target |
| Retrofit | Optimized upgrades | Improvement roadmap |

## Prescriptive Checks (9 items)
Wall U-value · Roof U-value · Slab F-factor · Window U-value · Window SHGC · Heating efficiency · Cooling efficiency · DHW efficiency · Lighting Power Density (LPD)

## Workflow
```
1. Get building_uid from Audette → fetch envelope + systems data
2. Build as-built IDF using geometry and equipment from Audette
3. Clone IDF 3× → modify each for DOE reference / code minimum / retrofit
4. Run all 4 simulations via EnergyPlus MCP
5. Fetch utility bills from Arcadia/Nectar → calibrate simulation outputs
6. Run prescriptive checks → pass/fail per ASHRAE 90.1-2022
7. Generate HTML artifact with charts and compliance table
```

## Authentication
- **Audette plugin**: required — building data source
- **EnergyPlus MCP token**: Bearer token from Soapbox (set ENERGYPLUS_MCP_TOKEN)
- **Arcadia or Nectar** (optional): for calibration against actual utility bills
