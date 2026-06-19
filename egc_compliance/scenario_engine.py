"""
Scenario Engine — Orchestrates 4-scenario energy code compliance analysis.

Generates and executes 4 scenarios in parallel:
1. Baseline - Actual building as-is
2. Reference - ASHRAE 90.1 Appendix G baseline
3. 90.1-2022 - Code-compliant prescriptive design
4. Retrofit - Baseline + ECMs from Audette MCP
"""

import copy
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Dict, Any, List, Optional

from egc_compliance.idf.generator import IDFGenerator
from egc_compliance.model_adapter import schema_to_idf_payload
from egc_compliance.simulation.runner import SimulationRunner

logger = logging.getLogger(__name__)


class ScenarioEngine:
    """Orchestrates 4-scenario energy code compliance analysis."""

    def __init__(self, building_model: Dict[str, Any], climate_zone: str):
        """
        Initialize scenario engine.

        Args:
            building_model: Validated building model from BuildingCollector
            climate_zone: ASHRAE climate zone (e.g., "5A")
        """
        self.building_model = building_model
        self.climate_zone = climate_zone

    def _generate_baseline_idf(self) -> str:
        """
        Generate baseline scenario IDF (actual building as-is).

        Returns:
            IDF content string
        """
        gen = IDFGenerator(schema_to_idf_payload(self.building_model))
        return gen.generate()

    def _apply_appendix_g_rules(self, building_model: Dict) -> Dict:
        """
        Apply ASHRAE 90.1 Appendix G transformations to building model.

        Args:
            building_model: Building model dict (will be modified in-place)

        Returns:
            Modified building_model
        """
        climate_zone = self.climate_zone
        # Bug 4 fix: BuildingCollector uses flat schema with no 'project' key
        _proj = building_model.get('project', {})
        building_type = _proj.get('building_type') or building_model.get('building_type', 'Office')
        _geom = building_model.get('geometry', {})
        area_ft2 = (
            _proj.get('conditioned_area_ft2') or
            (float(_geom.get('footprint_sqft', 0)) * int(_geom.get('stories', 1))) or
            50000
        )

        # Appendix G envelope values by climate zone (Table G3.1-5)
        appendix_g_envelope = {
            '5A': {
                'wall_u_factor': 0.124,  # Btu/h·ft²·°F
                'roof_u_factor': 0.063,
                'window_u_factor': 0.57,
                'window_shgc': 0.40
            },
            '4A': {
                'wall_u_factor': 0.124,
                'roof_u_factor': 0.063,
                'window_u_factor': 0.57,
                'window_shgc': 0.40
            }
        }

        # Get envelope values for this climate zone (default to 5A)
        envelope = appendix_g_envelope.get(climate_zone, appendix_g_envelope['5A'])

        # Apply envelope transformations
        for construction in building_model.get('constructions', []):
            construction['wall_r_value'] = 1.0 / envelope['wall_u_factor']
            construction['roof_r_value'] = 1.0 / envelope['roof_u_factor']

        for window in building_model.get('window_defs', []):
            window['u_factor'] = envelope['window_u_factor']
            window['shgc'] = envelope['window_shgc']

        # Determine HVAC system type based on building type and area (Table G3.1.1)
        if building_type == 'Residential':
            system_type = 'PTAC'
            heating_cop = 3.3
            cooling_eer = 10.0
        elif area_ft2 < 25000:
            system_type = 'PSZ-AC'
            heating_cop = 3.3
            cooling_eer = 11.0
        elif area_ft2 < 150000:
            system_type = 'Packaged VAV'
            heating_cop = 3.3
            cooling_eer = 11.5
        else:
            system_type = 'VAV with reheat'
            heating_cop = 3.3
            cooling_eer = 12.0

        # Apply HVAC transformations (using IdealLoadsAirSystem with Appendix G efficiencies)
        for equipment in building_model.get('hvac_equipment', []):
            equipment['equipment_type'] = system_type
            equipment['heating_cop'] = heating_cop
            equipment['cooling_eer'] = cooling_eer

        # Bug 3 fix: write back to flat schema keys so model_adapter picks up modifications
        building_model.setdefault('envelope', {})['wall_u_value'] = envelope['wall_u_factor']
        building_model.setdefault('envelope', {})['roof_u_value'] = envelope['roof_u_factor']
        building_model['envelope'].pop('wall_r_value', None)
        building_model['envelope'].pop('roof_r_value', None)
        building_model.setdefault('systems', {})['heating_efficiency'] = heating_cop
        building_model['systems']['cooling_eer'] = cooling_eer

        return building_model

    def _generate_reference_idf(self) -> str:
        """
        Generate Reference scenario IDF (ASHRAE 90.1 Appendix G baseline).

        Returns:
            IDF content string
        """
        # Deep copy to avoid modifying original
        ref_model = copy.deepcopy(self.building_model)

        # Apply Appendix G transformations
        ref_model = self._apply_appendix_g_rules(ref_model)

        # Generate IDF
        gen = IDFGenerator(schema_to_idf_payload(ref_model))
        return gen.generate()

    def _generate_code_2022_idf(self) -> str:
        """
        Generate 90.1-2022 scenario IDF (code-compliant prescriptive minimums).

        Returns:
            IDF content string
        """
        # Load prescriptive minimums from data file
        code_data_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'data',
            'ashrae_901_2022.json'
        )

        with open(code_data_path) as f:
            code_data = json.load(f)

        zone_data = code_data['climate_zones'].get(self.climate_zone)
        if not zone_data:
            raise ValueError(f"Climate zone {self.climate_zone} not found in ASHRAE 90.1-2022 data")

        # Deep copy building model
        code_model = copy.deepcopy(self.building_model)

        # Apply envelope minimums
        for construction in code_model.get('constructions', []):
            construction['wall_r_value'] = zone_data['envelope']['wall_r_value']
            construction['roof_r_value'] = zone_data['envelope']['roof_r_value']
            construction['slab_r_value'] = zone_data['envelope']['slab_r_value']

        for window in code_model.get('window_defs', []):
            window['u_factor'] = zone_data['envelope']['window_u_factor']
            window['shgc'] = zone_data['envelope']['window_shgc']

        # Apply HVAC minimums
        for equipment in code_model.get('hvac_equipment', []):
            equipment['heating_cop'] = zone_data['systems']['heating_cop_min']
            equipment['cooling_eer'] = zone_data['systems']['cooling_eer_min']

        # Apply lighting minimums
        # Bug 5 fix: BuildingCollector flat schema has no 'project' key
        _proj = code_model.get('project', {})
        building_type = (_proj.get('building_type') or code_model.get('building_type', 'Office')).lower()
        lpd_key = f"{building_type}_lpd"
        if lpd_key in zone_data['lighting']:
            code_model.setdefault('project', {})['lighting_lpd'] = zone_data['lighting'][lpd_key]

        # Bug 3 fix: write envelope/systems back to flat keys for model_adapter
        _env = code_model.setdefault('envelope', {})
        _env['wall_r_value'] = zone_data['envelope']['wall_r_value']
        _env.pop('wall_u_value', None)
        _env['roof_r_value'] = zone_data['envelope']['roof_r_value']
        _env.pop('roof_u_value', None)
        code_model.setdefault('systems', {}).update({
            'heating_cop': zone_data['systems']['heating_cop_min'],
            'cooling_eer': zone_data['systems']['cooling_eer_min'],
        })

        # Generate IDF
        gen = IDFGenerator(schema_to_idf_payload(code_model))
        return gen.generate()

    def _apply_retrofit_measures(
        self,
        building_model: Dict,
        measures: List[Dict]
    ) -> Dict:
        """
        Apply retrofit measures (ECMs) to building model.

        Args:
            building_model: Building model dict (will be modified in-place)
            measures: List of retrofit measure dicts with measure_type and parameters

        Returns:
            Modified building_model
        """
        for measure in measures:
            measure_type = measure.get('measure_type')
            params = measure.get('parameters', {})

            if measure_type == 'envelope_upgrade':
                # Apply to all constructions
                for construction in building_model.get('constructions', []):
                    if 'wall_r_value' in params:
                        construction['wall_r_value'] = params['wall_r_value']
                    if 'roof_r_value' in params:
                        construction['roof_r_value'] = params['roof_r_value']
                    if 'slab_r_value' in params:
                        construction['slab_r_value'] = params['slab_r_value']

                # Apply to windows
                for window in building_model.get('window_defs', []):
                    if 'window_u_factor' in params:
                        window['u_factor'] = params['window_u_factor']
                    if 'window_shgc' in params:
                        window['shgc'] = params['window_shgc']

                # Bug 3 fix: write back to flat schema keys for model_adapter
                _env = building_model.setdefault('envelope', {})
                if 'wall_r_value' in params:
                    _env['wall_r_value'] = params['wall_r_value']
                    _env.pop('wall_u_value', None)
                if 'roof_r_value' in params:
                    _env['roof_r_value'] = params['roof_r_value']
                    _env.pop('roof_u_value', None)
                if 'slab_r_value' in params:
                    _env['slab_r_value'] = params['slab_r_value']

            elif measure_type == 'hvac_replacement':
                # Apply to all equipment
                for equipment in building_model.get('hvac_equipment', []):
                    if 'heating_cop' in params:
                        equipment['heating_cop'] = params['heating_cop']
                    if 'cooling_eer' in params:
                        equipment['cooling_eer'] = params['cooling_eer']

                # Bug 3 fix: write back to flat schema keys for model_adapter
                _sys = building_model.setdefault('systems', {})
                if 'heating_cop' in params:
                    _sys['heating_cop'] = params['heating_cop']
                if 'cooling_eer' in params:
                    _sys['cooling_eer'] = params['cooling_eer']

            elif measure_type == 'lighting_upgrade':
                # Apply to project-level LPD
                if 'lighting_lpd' in params:
                    building_model['project']['lighting_lpd'] = params['lighting_lpd']

        return building_model

    def _generate_retrofit_idf(self, measures: Optional[List[Dict]] = None) -> str:
        """
        Generate Retrofit scenario IDF (baseline + ECMs).

        Args:
            measures: Optional list of retrofit measures from Audette MCP

        Returns:
            IDF content string
        """
        if not measures:
            # No retrofit measures, return baseline
            return self._generate_baseline_idf()

        # Deep copy building model
        retrofit_model = copy.deepcopy(self.building_model)

        # Apply measures
        retrofit_model = self._apply_retrofit_measures(retrofit_model, measures)

        # Generate IDF
        gen = IDFGenerator(schema_to_idf_payload(retrofit_model))
        return gen.generate()

    def _run_parallel_simulations(
        self,
        idf_dict: Dict[str, str],
        max_retries: int = 1
    ) -> Dict[str, Dict]:
        """
        Execute all scenarios in parallel using ThreadPoolExecutor.

        Args:
            idf_dict: Dict of scenario name -> IDF content string
            max_retries: Number of retries with parameter correction (default: 1)

        Returns:
            Dict of scenario name -> simulation results
        """
        runner = SimulationRunner()
        results = {}

        max_workers = max(1, min(os.cpu_count() or 4, len(idf_dict)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all scenarios
            futures = {
                name: executor.submit(
                    runner.run_with_retry,
                    idf_content,
                    self.climate_zone,
                    max_retries=3  # SimulationRunner's internal retry
                )
                for name, idf_content in idf_dict.items()
            }

            # Wait for all to complete with progress logging
            import threading

            def log_progress():
                """Log progress every 2 seconds until all done."""
                while not all(f.done() for f in futures.values()):
                    time.sleep(2)
                    if not all(f.done() for f in futures.values()):
                        done_count = sum(1 for f in futures.values() if f.done())
                        logger.info(f"Scenarios complete: {done_count}/{len(futures)}")

            # Start progress logging in background
            progress_thread = threading.Thread(target=log_progress, daemon=True)
            progress_thread.start()

            # Wait for all futures to complete
            wait(futures.values())

            # Collect results
            for name, future in futures.items():
                try:
                    result = future.result()

                    # If failed and retries allowed, attempt parameter correction
                    if not result['success'] and max_retries > 0:
                        logger.warning(f"{name} scenario failed, attempting parameter correction...")
                        corrected_idf = self._parse_error_and_correct(
                            result.get('error', ''),
                            result.get('fatal_errors', []),
                            idf_dict[name]
                        )

                        if corrected_idf:
                            # Retry with corrected IDF
                            logger.info(f"Retrying {name} with corrected IDF...")
                            result = runner.run_with_retry(
                                corrected_idf,
                                self.climate_zone,
                                max_retries=1
                            )
                            result['retry_attempted'] = True

                    results[name] = result

                    # Bug 1 fix: parse_sql_results was dead code — call it here
                    if result.get('success') and result.get('sim_dir'):
                        sql_path = os.path.join(result['sim_dir'], 'eplusout.sql')
                        if os.path.exists(sql_path):
                            parsed = runner.parse_sql_results(sql_path, result['sim_dir'])
                            results[name].update(parsed)

                except Exception as e:
                    logger.exception(f"Scenario {name} raised exception")
                    results[name] = {
                        'success': False,
                        'error': str(e),
                        'fatal_errors': [str(e)]
                    }

        return results

    def _parse_error_and_correct(
        self,
        error_msg: str,
        fatal_errors: List[str],
        idf_content: str
    ) -> Optional[str]:
        """
        Parse error message and attempt to correct IDF parameters.

        Args:
            error_msg: Error message string
            fatal_errors: List of fatal error strings
            idf_content: Original IDF content

        Returns:
            Corrected IDF string, or None if cannot correct
        """
        for error in fatal_errors:
            error_lower = error.lower()

            if "design heating load is zero" in error_lower:
                # Increase infiltration rate
                idf_content = re.sub(
                    r'(ZoneInfiltration:DesignFlowRate,.*?\n.*?Flow/ExteriorArea,\s*)(\d+\.?\d*)',
                    r'\g<1>0.0003',
                    idf_content,
                    flags=re.DOTALL
                )
                logger.info("Corrected: Increased infiltration to 0.3 ACH")
                return idf_content

            elif "window u-factor" in error_lower and "below" in error_lower:
                # Set to minimum valid value (0.5)
                idf_content = re.sub(
                    r'(WindowMaterial:SimpleGlazingSystem,.*?\n.*?U-Factor,\s*)(\d+\.?\d*)',
                    r'\g<1>0.5',
                    idf_content,
                    flags=re.DOTALL
                )
                logger.info("Corrected: Adjusted window U-factor to 0.5")
                return idf_content

            elif "window u-factor" in error_lower and "above" in error_lower:
                # Set to maximum valid value (1.2)
                idf_content = re.sub(
                    r'(WindowMaterial:SimpleGlazingSystem,.*?\n.*?U-Factor,\s*)(\d+\.?\d*)',
                    r'\g<1>1.2',
                    idf_content,
                    flags=re.DOTALL
                )
                logger.info("Corrected: Adjusted window U-factor to 1.2")
                return idf_content

        # No correction available
        return None

    def _calculate_deltas(
        self,
        baseline_results: Dict,
        scenario_results: Dict,
        scenario_name: str = ''
    ) -> Dict:
        """
        Calculate energy savings and compliance metrics vs baseline.

        Args:
            baseline_results: Baseline scenario results
            scenario_results: Scenario results to compare
            scenario_name: Scenario name (for compliance margin calculation)

        Returns:
            Dict with delta calculations
        """
        baseline_eui = baseline_results.get('eui_kwh_per_sf', 0)
        scenario_eui = scenario_results.get('eui_kwh_per_sf', 0)

        baseline_energy = baseline_results.get('total_site_energy_kwh', 0)
        scenario_energy = scenario_results.get('total_site_energy_kwh', 0)

        # Delta calculations (negative = savings)
        eui_delta = scenario_eui - baseline_eui
        eui_delta_pct = (eui_delta / baseline_eui) * 100 if baseline_eui > 0 else 0

        energy_savings_kwh = baseline_energy - scenario_energy
        energy_savings_pct = (energy_savings_kwh / baseline_energy) * 100 if baseline_energy > 0 else 0

        # End-use deltas
        end_use_deltas_kwh = {}
        baseline_end_uses = baseline_results.get('end_uses_kwh', {})
        scenario_end_uses = scenario_results.get('end_uses_kwh', {})

        for end_use in baseline_end_uses:
            baseline_val = baseline_end_uses[end_use]
            scenario_val = scenario_end_uses.get(end_use, 0)
            end_use_deltas_kwh[end_use] = scenario_val - baseline_val

        from egc_compliance.config.energy_rates import DEFAULT_ELEC_RATE_USD_PER_KWH
        state = self.building_model.get('state', '').upper()
        elec_rate = DEFAULT_ELEC_RATE_USD_PER_KWH.get(state, 0.12)
        cost_savings_annual = energy_savings_kwh * elec_rate

        deltas = {
            'eui_delta_kwh_per_sf': round(eui_delta, 2),
            'eui_delta_pct': round(eui_delta_pct, 1),
            'energy_savings_kwh': round(energy_savings_kwh, 0),
            'energy_savings_pct': round(energy_savings_pct, 1),
            'end_use_deltas_kwh': {k: round(v, 0) for k, v in end_use_deltas_kwh.items()},
            'cost_savings_annual': round(cost_savings_annual, 2)
        }

        # Special calculation for code_2022: compliance margin
        if scenario_name == 'code_2022':
            # Compliance margin: invert sign (negative delta = positive compliance)
            compliance_margin_pct = -eui_delta_pct
            deltas['compliance_margin_pct'] = round(compliance_margin_pct, 1)

        return deltas

    def generate_all_scenarios(
        self,
        retrofit_measures: Optional[List[Dict]] = None
    ) -> Dict[str, str]:
        """
        Generate IDF content for all 4 scenarios.

        Args:
            retrofit_measures: Optional list of ECMs from Audette MCP

        Returns:
            Dict mapping scenario name to IDF content string
        """
        logger.info("Generating all 4 scenarios...")

        idf_dict = {
            'baseline': self._generate_baseline_idf(),
            'reference': self._generate_reference_idf(),
            'code_2022': self._generate_code_2022_idf(),
            'retrofit': self._generate_retrofit_idf(retrofit_measures)
        }

        logger.info("All scenarios generated")
        return idf_dict

    def run_all_scenarios(
        self,
        idf_dict: Dict[str, str],
        max_retries: int = 1
    ) -> Dict[str, Any]:
        """
        Execute all scenarios in parallel and aggregate results.

        Args:
            idf_dict: Dict of scenario name -> IDF content
            max_retries: Number of retries with parameter correction (default: 1)

        Returns:
            Aggregated results dict with baseline-anchored deltas
        """
        import datetime

        logger.info("Running all scenarios in parallel...")

        # Execute simulations
        sim_results = self._run_parallel_simulations(idf_dict, max_retries)

        # Aggregate results
        aggregated = {}
        baseline_results = sim_results.get('baseline', {})
        baseline_success = baseline_results.get('success', False)

        # Process each scenario
        for scenario_name in ['baseline', 'reference', 'code_2022', 'retrofit']:
            scenario_result = sim_results.get(scenario_name, {})

            if scenario_name == 'baseline':
                # Baseline: raw metrics only
                aggregated[scenario_name] = {
                    'success': scenario_result.get('success', False),
                    'raw': scenario_result if scenario_result.get('success') else {}
                }
                if not scenario_result.get('success'):
                    aggregated[scenario_name]['error'] = scenario_result.get('error')
                    aggregated[scenario_name]['fatal_errors'] = scenario_result.get('fatal_errors', [])
            else:
                # Other scenarios: raw + deltas (if baseline succeeded)
                aggregated[scenario_name] = {
                    'success': scenario_result.get('success', False),
                    'raw': scenario_result if scenario_result.get('success') else {}
                }

                if not scenario_result.get('success'):
                    aggregated[scenario_name]['error'] = scenario_result.get('error')
                    aggregated[scenario_name]['fatal_errors'] = scenario_result.get('fatal_errors', [])
                    if 'retry_attempted' in scenario_result:
                        aggregated[scenario_name]['retry_attempted'] = True
                elif baseline_success:
                    # Calculate deltas vs baseline
                    aggregated[scenario_name]['vs_baseline'] = self._calculate_deltas(
                        baseline_results,
                        scenario_result,
                        scenario_name
                    )

        # Add metadata
        aggregated['metadata'] = {
            'building_uid': self.building_model.get('building_uid', 'unknown'),
            'building_name': self.building_model.get('project', {}).get('name', 'Unknown'),
            'climate_zone': self.climate_zone,
            'simulation_date': datetime.datetime.now().isoformat(),
            'appendix_g_simplifications': [
                'Single orientation (baseline orientation used, not 4-orientation average)'
            ]
        }

        # Add warnings if baseline failed
        if not baseline_success:
            aggregated['metadata']['warnings'] = [
                'Baseline scenario failed - delta calculations unavailable'
            ]

        logger.info("Results aggregation complete")
        return aggregated
