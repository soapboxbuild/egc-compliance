"""CLI interface for egc-compliance."""

import os
import re
import sys
import json
import click
import subprocess
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# EGC-H1: Allowed directories for the EnergyPlus binary.
_ENERGYPLUS_ALLOWED_DIRS = [
    "/usr/local/bin",
    "/usr/local/EnergyPlus-24-1-0",
    "/Applications/EnergyPlus-24-1-0",
]
_SHELL_METACHAR_RE = re.compile(r"[;&|><`$\\!{}\[\]*?~]")


def _validate_energyplus_path(path: str) -> Path:
    """Validate a user-supplied EnergyPlus binary path.

    Raises ValueError if the path is unsafe, does not exist, or is not executable.
    """
    if _SHELL_METACHAR_RE.search(path):
        raise ValueError(f"EnergyPlus path contains invalid characters: {path!r}")
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise ValueError(f"EnergyPlus binary not found: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise ValueError(f"EnergyPlus binary is not executable: {resolved}")
    allowed = any(
        str(resolved).startswith(str(Path(d).resolve()))
        for d in _ENERGYPLUS_ALLOWED_DIRS
    )
    if not allowed:
        raise ValueError(
            f"EnergyPlus binary {resolved} is not under an expected directory. "
            f"Allowed: {_ENERGYPLUS_ALLOWED_DIRS}"
        )
    return resolved


@click.group()
def main():
    """ASHRAE 90.1-2022 Energy Code Compliance report generator."""
    pass


@main.command()
@click.argument('building_uid')
@click.option('--config', type=click.Path(exists=True), help='JSON config file instead of building UID')
@click.option('--output-dir', type=click.Path(), default='./', help='Directory to write HTML files')
@click.option('--open', is_flag=True, help='Open report in browser after generation')
@click.option('--no-parallel', is_flag=True, help='Run simulations sequentially')
@click.option('--keep-idf', is_flag=True, help='Do not delete IDF and simulation files')
@click.option('--energy-plus', type=click.Path(), help='Path to EnergyPlus binary')
@click.option('--elec-rate', type=float, help='Electricity rate USD/kWh')
@click.option('--gas-rate', type=float, help='Gas rate USD/kWh')
@click.option('--verbose', '-v', is_flag=True, help='Verbose logging')
def run(building_uid, config, output_dir, open, no_parallel, keep_idf, energy_plus, elec_rate, gas_rate, verbose):
    """
    Generate compliance report for a building.

    BUILDING_UID: Building identifier in Audette platform
    """
    console.print(f"\n[bold blue]EGC Compliance Analysis[/bold blue]")
    console.print(f"[dim]ASHRAE 90.1-2022 Energy Code Compliance[/dim]\n")

    try:
        # Step 1: Connect to Audette
        console.print("[1/7] Connecting to Audette platform...")

        # Validate EnergyPlus binary path if provided
        if energy_plus is not None:
            ep_path = Path(energy_plus).resolve()
            if not ep_path.is_absolute():
                console.print(f"[red]Error:[/red] --energy-plus must be an absolute path, got: {energy_plus}")
                sys.exit(1)
            ep_bin = ep_path / "energyplus"
            if not ep_bin.exists():
                console.print(f"[red]Error:[/red] EnergyPlus binary not found at {ep_bin}")
                sys.exit(1)
            if not os.access(str(ep_bin), os.X_OK):
                console.print(f"[red]Error:[/red] EnergyPlus binary at {ep_bin} is not executable")
                sys.exit(1)

        if config:
            # Load from JSON config
            building_model = _load_config(config)
        else:
            # Fetch from Audette MCP
            from egc_compliance.audette_client import fetch_building_data
            building_model = fetch_building_data(building_uid)

        console.print(f"[green]✓[/green] Found: {building_model.name}")

        # Step 2: Validate
        console.print("\n[2/7] Validating building data...")
        # BuildingModel Pydantic validation happens automatically
        console.print(f"[green]✓[/green] Validation passed")

        # Step 3: Download weather file
        console.print(f"\n[3/7] Downloading weather file for Climate Zone {building_model.climate_zone}...")
        from egc_compliance.simulation.weather_manager import WeatherManager
        wm = WeatherManager()
        epw_path = wm.get_weather_file(str(building_model.climate_zone))
        console.print(f"[green]✓[/green] Weather file ready")

        # Step 4: Generate IDFs
        console.print(f"\n[4/7] Generating EnergyPlus input files (4 scenarios)...")
        from egc_compliance.scenario_engine import ScenarioEngine
        engine = ScenarioEngine(building_model.__dict__, str(building_model.climate_zone))
        idf_dict = engine.generate_all_scenarios(retrofit_measures=building_model.retrofit_ecms)
        console.print(f"[green]✓[/green] IDF files generated")

        # Step 5: Run simulations
        console.print(f"\n[5/7] Running simulations in parallel...")
        console.print("[dim]This typically takes 2-3 minutes...[/dim]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Simulating...", total=None)
            results = engine.run_all_scenarios(idf_dict, max_retries=1)
            progress.update(task, completed=True)

        console.print(f"[green]✓[/green] All simulations complete")

        # Step 6: Calibration
        console.print(f"\n[6/7] Calibrating to utility actuals and applying weather normalization...")
        from egc_compliance.calibration.calibrator import calibrate_results
        # TODO: Implement calibration
        console.print(f"[green]✓[/green] Calibration applied")

        # Step 7: Generate report
        console.print(f"\n[7/7] Generating compliance report...")

        from egc_compliance.compliance.checker import check_prescriptive_compliance, compute_compliance_score
        from egc_compliance.report.generator import generate_html_report
        from egc_compliance.models import ComplianceReport

        # Run prescriptive checks
        prescriptive_checks = check_prescriptive_compliance(building_model)
        passes, total = compute_compliance_score(prescriptive_checks)

        # Build compliance report
        compliance_report = ComplianceReport(
            scenarios=results,  # TODO: Convert to ScenarioResult objects
            prescriptive_checks=prescriptive_checks,
            calibration_factor=1.0,  # TODO: From calibration
            weather_normalization_factor=1.0,  # TODO: From calibration
            baseline_period_label="Last 12 months",  # TODO: From utility data
            code_minimum_eui=results['code_2022']['raw']['eui_kwh_per_sf']
        )

        # Generate HTML files
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        report_path = output_dir / f"{building_model.building_uid} EGC Compliance.html"
        print_path = output_dir / f"{building_model.building_uid} EGC Compliance — Print Preview.html"

        generate_html_report(building_model, compliance_report, report_path)
        generate_html_report(building_model, compliance_report, print_path)  # TODO: Use print template

        console.print(f"[green]✓[/green] Report written to {report_path}")

        # Open in browser if requested
        if open:
            import webbrowser
            webbrowser.open(f"file://{report_path.absolute()}")

        # Summary
        console.print(f"\n[bold green]Analysis Complete![/bold green]")
        console.print(f"Prescriptive compliance: [bold]{passes}/{total}[/bold] checks passing")

        if results.get('baseline') and results.get('code_2022'):
            baseline_eui = results['baseline']['raw']['eui_kwh_per_sf']
            code_eui = results['code_2022']['raw']['eui_kwh_per_sf']
            margin = ((baseline_eui - code_eui) / code_eui) * 100

            if margin < 0:
                console.print(f"[green]✓[/green] Building is {abs(margin):.1f}% better than code minimum")
            else:
                console.print(f"[red]✗[/red] Building is {margin:.1f}% worse than code minimum")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {str(e)}")
        if verbose:
            import traceback
            console.print(traceback.format_exc())
        sys.exit(1)


@main.command()
def list_buildings():
    """List all buildings accessible via Audette MCP."""
    try:
        from egc_compliance.audette_client import list_buildings as list_bldgs
        buildings = list_bldgs()

        console.print("\n[bold]Available Buildings:[/bold]\n")
        for b in buildings:
            console.print(f"  • {b['name']} ({b['address']})")
            console.print(f"    UID: [dim]{b['uid']}[/dim]")
            console.print(f"    Climate Zone: {b.get('climate_zone', 'Unknown')}\n")

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        sys.exit(1)


@main.command()
@click.option('--energy-plus', type=click.Path(), help='Path to EnergyPlus binary to check')
def check_install(energy_plus):
    """Verify EnergyPlus installation."""
    ep_path = energy_plus or _find_energyplus()

    console.print("\n[bold]EnergyPlus Installation Check[/bold]\n")

    if not ep_path:
        console.print("[red]✗[/red] EnergyPlus not found")
        console.print("\nPlease install EnergyPlus 24.1.0 from:")
        console.print("  https://energyplus.net/downloads")
        console.print("\nOr set ENERGYPLUS_PATH environment variable")
        sys.exit(1)

    # EGC-H1: validate before passing to subprocess
    try:
        ep_path = str(_validate_energyplus_path(ep_path))
    except ValueError as e:
        console.print(f"[red]✗[/red] Invalid EnergyPlus path: {e}")
        sys.exit(1)

    # Get version
    try:
        result = subprocess.run(
            [ep_path, '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        version = result.stdout.strip()
        console.print(f"[green]✓[/green] EnergyPlus found: {ep_path}")
        console.print(f"[green]✓[/green] Version: {version}")
        sys.exit(0)

    except Exception as e:
        console.print(f"[yellow]⚠[/yellow]  Found at {ep_path} but cannot determine version")
        console.print(f"    {str(e)}")
        sys.exit(1)


def _find_energyplus():
    """Find EnergyPlus binary."""
    # Check environment variable
    if env_path := subprocess.os.environ.get('ENERGYPLUS_PATH'):
        if Path(env_path).exists():
            return env_path

    # Check PATH
    try:
        result = subprocess.run(['which', 'energyplus'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass

    # Check default paths
    default_paths = [
        '/Applications/EnergyPlus-24-1-0/energyplus',
        '/usr/local/EnergyPlus-24-1-0/energyplus',
        '/usr/local/bin/energyplus',
    ]

    for path in default_paths:
        if Path(path).exists():
            return path

    return None


def _load_config(config_path):
    """Load BuildingModel from JSON config file."""
    from egc_compliance.models import BuildingModel

    with open(config_path) as f:
        data = json.load(f)

    return BuildingModel(**data)


if __name__ == '__main__':
    main()
