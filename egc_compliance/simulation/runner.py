"""
Simulation Runner — Executes EnergyPlus and parses results.

Ported from egc-platform/simulation.py for Claude Desktop skill.
Runs EnergyPlus simulations with IDF files and weather data.
"""

import os
import subprocess
import tempfile
import shutil
import logging
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class SimulationRunner:
    """Runs EnergyPlus simulations and parses results."""

    def __init__(self, ep_dir: Optional[str] = None):
        """
        Initialize simulation runner.

        Args:
            ep_dir: EnergyPlus installation directory. If None, uses environment
                   variable or default path.
        """
        # EnergyPlus paths
        if ep_dir is None:
            ep_dir = os.environ.get("ENERGYPLUS_DIR", "/usr/local/EnergyPlus-24-1-0")

        self.ep_dir = ep_dir
        self.ep_exe = os.path.join(ep_dir, "energyplus")
        self.ep_idd = os.path.join(ep_dir, "Energy+.idd")

        # Verify EnergyPlus is installed
        if not os.path.exists(self.ep_exe):
            logger.warning(f"EnergyPlus not found at {self.ep_exe}")

    def run_simulation(
        self,
        idf_content: str,
        climate_zone: str,
        custom_epw_path: Optional[str] = None,
        timeout: int = 600
    ) -> Dict[str, Any]:
        """
        Run an EnergyPlus simulation.

        Args:
            idf_content: Complete IDF file content as string
            climate_zone: ASHRAE climate zone (e.g., "5A")
            custom_epw_path: Optional path to custom EPW file
            timeout: Simulation timeout in seconds (default: 600 = 10 minutes)

        Returns:
            Dict with simulation results including success, errors, and energy data
        """
        # Check if EnergyPlus is installed
        if not os.path.exists(self.ep_exe):
            return {
                "success": False,
                "error": f"EnergyPlus not found at {self.ep_exe}. Install with: sudo apt install energyplus (or download from energyplus.net)",
                "fatal_errors": ["EnergyPlus not installed"],
                "severe_errors": []
            }

        # Get weather file
        try:
            from egc_compliance.simulation.weather_manager import WeatherManager
            weather_mgr = WeatherManager()
            epw_path = weather_mgr.get_weather_file(climate_zone, custom_epw_path)
        except Exception as e:
            return {
                "success": False,
                "error": f"Weather file error: {str(e)}",
                "fatal_errors": [str(e)],
                "severe_errors": []
            }

        # Create temp directory for simulation
        sim_dir = tempfile.mkdtemp(prefix="audette_sim_")
        _cleanup_needed = True
        idf_path = os.path.join(sim_dir, "model.idf")

        try:
            # Write IDF
            with open(idf_path, 'w') as f:
                f.write(idf_content)

            # Run EnergyPlus
            cmd = [
                self.ep_exe,
                "-i", self.ep_idd,
                "-w", epw_path,
                "-d", sim_dir,
                "-r",  # Readvars - converts ESO to CSV
                idf_path
            ]

            logger.info(f"Running EnergyPlus: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=sim_dir
            )

            # Parse output
            stdout = result.stdout
            stderr = result.stderr

            # Parse error log file
            err_file = os.path.join(sim_dir, "eplusout.err")
            if not os.path.exists(err_file):
                err_file = os.path.join(sim_dir, "model.err")  # fallback

            fatal_errors = []
            severe_errors = []
            warnings = []

            if os.path.exists(err_file):
                with open(err_file) as f:
                    err_content = f.read()

                for line in err_content.split('\n'):
                    if '** Fatal **' in line:
                        fatal_errors.append(line.strip())
                    elif '** Severe **' in line:
                        severe_errors.append(line.strip())
                    elif '** Warning **' in line:
                        warnings.append(line.strip())

            # Check for fatal errors
            if fatal_errors or result.returncode != 0:
                return {
                    "success": False,
                    "error": "Simulation failed with fatal errors",
                    "fatal_errors": fatal_errors[:10],
                    "severe_errors": severe_errors[:10],
                    "warnings_count": len(warnings),
                    "stderr": stderr[:1000] if stderr else "",
                    "idf_lines": idf_content.count('\n'),
                    "return_code": result.returncode
                }

            # Success - will parse results in subsequent tasks
            weather_name = Path(epw_path).stem

            _cleanup_needed = False
            return {
                "success": True,
                "weather_file": weather_name,
                "idf_lines": idf_content.count('\n'),
                "warnings_count": len(warnings),
                "severe_errors": severe_errors[:5],
                "fatal_errors": [],
                "sim_dir": sim_dir  # Keep for result parsing
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Simulation timed out (>{timeout} seconds)",
                "fatal_errors": [f"Simulation exceeded {timeout}-second timeout"],
                "severe_errors": []
            }
        except Exception as e:
            logger.exception("Simulation failed")
            return {
                "success": False,
                "error": str(e),
                "fatal_errors": [str(e)],
                "severe_errors": []
            }
        finally:
            if _cleanup_needed:
                shutil.rmtree(sim_dir, ignore_errors=True)

    def parse_sql_results(self, sql_path: str, sim_dir: str) -> Dict[str, Any]:
        """
        Parse EnergyPlus SQL output database.

        Args:
            sql_path: Path to eplusout.sql file
            sim_dir: Simulation output directory

        Returns:
            Dict with energy metrics:
                - eui_kwh_per_sf: Energy Use Intensity (kWh/ft²)
                - eui_kbtu_per_sf: Energy Use Intensity (kBtu/ft²)
                - total_site_energy_kwh: Total site energy (kWh)
                - total_site_energy_kbtu: Total site energy (kBtu)
                - conditioned_area_m2: Conditioned floor area (m²)
                - conditioned_area_ft2: Conditioned floor area (ft²)
                - end_uses: Dict of end-use energy by category (GJ)
                - end_uses_kwh: Dict of end-use energy by category (kWh)
                - unmet_heating_hours: Hours setpoint not met during heating
                - unmet_cooling_hours: Hours setpoint not met during cooling
        """
        import sqlite3

        # Default result structure
        result = {
            'eui_kwh_per_sf': None,
            'eui_kbtu_per_sf': None,
            'total_site_energy_kwh': None,
            'total_site_energy_kbtu': None,
            'conditioned_area_m2': None,
            'conditioned_area_ft2': None,
            'end_uses': {},
            'end_uses_kwh': {},
            'unmet_heating_hours': None,
            'unmet_cooling_hours': None
        }

        # Check if SQL file exists
        if not os.path.exists(sql_path):
            logger.warning(f"SQL output file not found: {sql_path}")
            return result

        try:
            conn = sqlite3.connect(sql_path)
            cursor = conn.cursor()

            # Bug 7 fix: filter to annual report only.
            # EnergyPlus writes 'End Uses' twice: once for AnnualBuildingUtilityPerformanceSummary
            # (annual energy) and once for DemandEndUseComponentsSummary (peak demand / sizing run).
            # The sizing run has enormous District Heating values (100,000+ GJ) that corrupt totals.
            ANNUAL_REPORT = 'AnnualBuildingUtilityPerformanceSummary'
            try:
                cursor.execute("""
                    SELECT TableName, ColumnName, RowName, Units, Value
                    FROM TabularDataWithStrings
                    WHERE ReportName = ?
                """, (ANNUAL_REPORT,))
            except Exception:
                # Fallback for older EnergyPlus SQL schemas without ReportName column
                cursor.execute("""
                    SELECT TableName, ColumnName, RowName, Units, Value
                    FROM TabularDataWithStrings
                """)
            rows = cursor.fetchall()

            # EnergyPlus end-use name mapping to our keys
            end_use_map = {
                'Heating': 'heating',
                'Cooling': 'cooling',
                'Interior Lighting': 'interior_lighting',
                'Interior Equipment': 'interior_equipment',
                'Fans': 'fans',
                'Pumps': 'pumps',
                'Water Systems': 'dhw'
            }

            # Parse rows
            for table_name, column_name, row_name, units, value in rows:
                try:
                    # Total Site Energy
                    if table_name == 'Site and Source Energy' and row_name == 'Total Site Energy':
                        total_gj = float(value)
                        result['total_site_energy_kwh'] = round(total_gj * 277.778, 0)
                        result['total_site_energy_kbtu'] = round(total_gj * 947.817, 0)

                    # Building Area
                    elif table_name == 'Building Area' and row_name == 'Total Building Area':
                        area_m2 = float(value)
                        result['conditioned_area_m2'] = area_m2
                        result['conditioned_area_ft2'] = round(area_m2 * 10.7639, 2)

                    # End Uses — Bug 6 fix: accumulate across all fuel types (Electricity,
                    # Natural Gas, etc.) instead of overwriting. The last fuel type in the
                    # table is typically Gasoline/Coal (all zeros), so plain assignment
                    # always produced zeros for all-electric or all-gas buildings.
                    elif table_name == 'End Uses' and row_name in end_use_map:
                        energy_gj = float(value)
                        key = end_use_map[row_name]
                        result['end_uses'][key] = result['end_uses'].get(key, 0.0) + energy_gj
                        result['end_uses_kwh'][key] = round(result['end_uses'][key] * 277.778, 0)

                    # Unmet Hours
                    elif table_name == 'Comfort and Setpoint Not Met Summary':
                        if 'Heating' in row_name and 'Not Met' in row_name:
                            result['unmet_heating_hours'] = float(value)
                        elif 'Cooling' in row_name and 'Not Met' in row_name:
                            result['unmet_cooling_hours'] = float(value)

                except (ValueError, TypeError) as e:
                    logger.debug(f"Could not parse value '{value}' for {table_name}/{row_name}: {e}")
                    continue

            conn.close()

            # Calculate EUI if we have energy and area
            if result['total_site_energy_kwh'] and result['conditioned_area_ft2']:
                result['eui_kwh_per_sf'] = round(
                    result['total_site_energy_kwh'] / result['conditioned_area_ft2'],
                    2
                )
                result['eui_kbtu_per_sf'] = round(
                    result['total_site_energy_kbtu'] / result['conditioned_area_ft2'],
                    2
                )

            return result

        except Exception as e:
            logger.exception("Error parsing SQL results")
            return result

    def run_with_retry(
        self,
        idf_content: str,
        climate_zone: str,
        custom_epw_path: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 600,
        initial_delay: float = 1.0
    ) -> Dict[str, Any]:
        """
        Run simulation with retry logic and exponential backoff.

        Args:
            idf_content: Complete IDF file content
            climate_zone: ASHRAE climate zone
            custom_epw_path: Optional custom weather file
            max_retries: Maximum number of retry attempts
            timeout: Simulation timeout in seconds
            initial_delay: Initial delay between retries in seconds

        Returns:
            Dict with simulation results including retry_count
        """
        import time

        retry_count = 0
        last_result = None

        for attempt in range(max_retries + 1):
            # Run simulation
            result = self.run_simulation(
                idf_content,
                climate_zone,
                custom_epw_path,
                timeout
            )

            last_result = result

            # Success - return immediately
            if result.get('success'):
                result['retry_count'] = retry_count
                return result

            # Check if error is retryable
            error = result.get('error', '')

            # Non-retryable errors (fail fast)
            if 'EnergyPlus not found' in error:
                result['retry_count'] = retry_count
                return result

            if 'Weather file error' in error:
                result['retry_count'] = retry_count
                return result

            # If we've exhausted retries, return failure
            if attempt >= max_retries:
                result['retry_count'] = retry_count
                return result

            # Retryable error - wait and retry
            retry_count += 1
            delay = initial_delay * (2 ** (retry_count - 1))
            logger.info(f"Simulation failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s...")
            time.sleep(delay)

        # Should never reach here, but just in case
        if last_result:
            last_result['retry_count'] = retry_count
            return last_result

        return {
            'success': False,
            'error': 'All retries exhausted',
            'retry_count': retry_count,
            'fatal_errors': [],
            'severe_errors': []
        }
