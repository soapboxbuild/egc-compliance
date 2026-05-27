# EGC Compliance - Deployment Guide

## GitHub Repository
**URL:** https://github.com/Christopher-audette-1/egc-compliance

## Installation

### Option 1: From GitHub (Recommended)
```bash
pip install git+https://github.com/Christopher-audette-1/egc-compliance.git
```

### Option 2: Clone and Install
```bash
git clone https://github.com/Christopher-audette-1/egc-compliance.git
cd egc-compliance
pip install -e .
```

### Option 3: As Plugin Dependency (Claude Desktop)

Add to your plugin's `requirements.txt`:
```
egc-compliance @ git+https://github.com/Christopher-audette-1/egc-compliance.git
```

Or in `pyproject.toml`:
```toml
[project.dependencies]
egc-compliance = {git = "https://github.com/Christopher-audette-1/egc-compliance.git"}
```

## Usage

### Command Line

```bash
# Check installation
egc-compliance check-install

# List buildings from Audette MCP
egc-compliance list-buildings

# Generate compliance report
egc-compliance run <building_uid>

# With options
egc-compliance run <building_uid> \
  --output-dir ./reports \
  --open \
  --verbose

# From JSON config
egc-compliance run --config building.json
```

### As Python Library

```python
from egc_compliance.models import BuildingModel, ClimateZone
from egc_compliance.compliance.checker import check_prescriptive_compliance
from egc_compliance.report.generator import generate_html_report

# Load building data
building = BuildingModel(...)

# Run compliance checks
checks = check_prescriptive_compliance(building)

# Generate report
# (after running simulations)
generate_html_report(building, compliance_report, "report.html")
```

## Requirements

- **Python:** 3.11+
- **EnergyPlus:** 24.1.0 (download from https://energyplus.net/downloads)
- **Dependencies:** See `pyproject.toml`
  - pydantic>=2.0
  - jinja2>=3.1
  - click>=8.1
  - eppy>=0.5.63
  - requests>=2.31
  - rich>=13.0

## EnergyPlus Installation

### macOS
```bash
# Download from energyplus.net
# Install to /Applications/EnergyPlus-24-1-0/
export ENERGYPLUS_PATH=/Applications/EnergyPlus-24-1-0/energyplus
```

### Linux
```bash
# Download from energyplus.net
sudo tar -xzf EnergyPlus-24-1-0-*.tar.gz -C /usr/local/
export ENERGYPLUS_PATH=/usr/local/EnergyPlus-24-1-0/energyplus
```

## Integration with Claude Desktop

### As a Plugin Skill

Create a skill in your plugin that calls the CLI:

```python
import subprocess

def run_egc_compliance(building_uid: str) -> str:
    """Run EGC compliance analysis."""
    result = subprocess.run(
        ["egc-compliance", "run", building_uid, "--verbose"],
        capture_output=True,
        text=True
    )
    return result.stdout
```

### As a Direct Import

```python
from egc_compliance.scenario_engine import ScenarioEngine
from egc_compliance.compliance.checker import check_prescriptive_compliance

# Use the library directly in your skill
engine = ScenarioEngine(building_data, climate_zone)
results = engine.run_all_scenarios(...)
```

## Output

The tool generates two HTML files:
1. `{building_name} EGC Compliance.html` - Interactive report
2. `{building_name} EGC Compliance — Print Preview.html` - Print-optimized

Both are self-contained (all CSS/JS inline) and work offline.

## Testing

```bash
# Run test suite
pytest

# Run specific test
pytest tests/test_compliance.py -v

# With coverage
pytest --cov=egc_compliance
```

## Troubleshooting

### EnergyPlus not found
```bash
egc-compliance check-install
# Set ENERGYPLUS_PATH environment variable
```

### Import errors
```bash
pip install -e ".[dev]"  # Install with dev dependencies
```

### Simulation failures
Use `--verbose` flag to see detailed error messages:
```bash
egc-compliance run <building_uid> --verbose
```

## Support

- **GitHub Issues:** https://github.com/Christopher-audette-1/egc-compliance/issues
- **Documentation:** See README.md and IMPLEMENTATION_STATUS.md
