"""Building data collection with cascade from MCP → docs → Q&A."""

import copy
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# EGC-H2: Base directory that PDF files must reside under.
# Override via AUDETTE_PDF_BASE_DIR env var if needed.
_PDF_BASE_DIR = os.environ.get("AUDETTE_PDF_BASE_DIR", os.path.expanduser("~"))


def _validate_pdf_path(pdf_path: str) -> str:
    """Ensure pdf_path resolves within _PDF_BASE_DIR (prevents path traversal)."""
    resolved = os.path.realpath(pdf_path)
    base = os.path.realpath(_PDF_BASE_DIR)
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ValueError(
            f"PDF path {resolved!r} is outside the allowed base directory {base!r}. "
            "Set AUDETTE_PDF_BASE_DIR to override."
        )
    return resolved

try:
    import jsonschema
    from jsonschema import validate, ValidationError, SchemaError
except ImportError:
    # Fallback for environments without jsonschema
    jsonschema = None
    ValidationError = None
    SchemaError = None

# Import Audette MCP tools (these will be available in Claude Desktop context)
try:
    from mcp__claude_ai_Audette_AI__get_building_model_details import get_building_model_details
except ImportError:
    # Fallback for testing without MCP
    def get_building_model_details(building_uid: str) -> Dict[str, Any]:
        raise NotImplementedError("Audette MCP not available")

# Import Read tool for PDF extraction (available in Claude Desktop context)
try:
    from tools import Read
except ImportError:
    # Fallback for testing - will be mocked
    Read = None


class BuildingCollector:
    """Collects building data from multiple sources with cascading priority.

    Priority order:
    1. Audette MCP (get_building_model_details)
    2. PDF documents (extraction via pypdf + Gemini)
    3. Interactive Q&A with user
    4. 3D geometry modeler (for missing geometry)

    Tracks data sources for transparency.
    """

    def __init__(self):
        """Initialize building collector."""
        self.data_sources = {}  # Track which source provided each field

    def query_mcp(self, building_uid: str) -> Dict[str, Any]:
        """Query Audette MCP for building data.

        Args:
            building_uid: Unique identifier for the building

        Returns:
            Dictionary with building data from MCP

        Raises:
            ValueError: If building_uid is invalid or MCP query fails
        """
        if not building_uid:
            raise ValueError("building_uid is required")

        try:
            data = get_building_model_details(building_uid)

            # Track that all fields came from MCP
            for key in data.keys():
                self.data_sources[key] = 'audette_mcp'

            return data
        except Exception as e:
            raise ValueError(f"Failed to query Audette MCP: {e}")

    def identify_gaps(self, building_data: Dict[str, Any]) -> List[str]:
        """Identify missing fields by comparing against schema.

        Args:
            building_data: Partial building data (e.g., from MCP)

        Returns:
            List of missing field paths (e.g., ['envelope.wall_r_value', 'systems.heating_type'])
        """
        # Load schema
        schema_path = Path(__file__).parent / 'schemas' / 'building_model.json'
        with open(schema_path) as f:
            schema = json.load(f)

        gaps = []

        # Check required top-level properties
        required_props = schema.get('required', [])
        for prop in required_props:
            if prop not in building_data:
                gaps.append(prop)

        # Check nested required properties
        properties = schema.get('properties', {})
        for field_name, field_schema in properties.items():
            if field_name in building_data:
                # Field exists, check nested requirements
                if field_schema.get('type') == 'object':
                    nested_gaps = self._check_nested_gaps(
                        field_name,
                        building_data[field_name],
                        field_schema
                    )
                    gaps.extend(nested_gaps)
            elif field_name in required_props:
                # Already added above
                pass

        return gaps

    def _check_nested_gaps(self, parent_path: str, data: Dict[str, Any],
                          schema: Dict[str, Any]) -> List[str]:
        """Check for missing fields in nested objects."""
        gaps = []
        required = schema.get('required', [])
        properties = schema.get('properties', {})

        for prop in required:
            if prop not in data:
                gaps.append(f"{parent_path}.{prop}")

        return gaps

    def extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """Extract building data from PDF using Claude's Read tool.

        This uses Claude Desktop's native PDF reading capability.
        The Read tool returns extracted text which we parse for building parameters.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Dictionary with extracted building data
        """
        # Path traversal protection
        try:
            resolved = Path(pdf_path).resolve()
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid PDF path: {e}")
        # Reject paths that escape via traversal
        parts = Path(pdf_path).parts
        if ".." in parts:
            raise ValueError(f"Path traversal detected in pdf_path: {pdf_path!r}")
        # Use resolved path
        pdf_path = str(resolved)

        # In Claude Desktop, the Read tool is available in the execution context
        # For testing, we mock it
        if Read is None:
            raise NotImplementedError("Read tool not available")

        # EGC-H2: reject paths that escape the expected base directory
        pdf_path = _validate_pdf_path(pdf_path)
        pdf_text = Read(file_path=pdf_path)

        extracted = {}

        # Extract envelope data
        envelope = {}

        # Wall R-value pattern: "R-13", "R-value: 13", "Wall R-value: R-13"
        wall_match = re.search(r'wall\s+r[-\s]?value[:\s]+r?[-\s]?(\d+(?:\.\d+)?)', pdf_text, re.IGNORECASE)
        if wall_match:
            envelope['wall_r_value'] = float(wall_match.group(1))

        # Roof R-value
        roof_match = re.search(r'roof\s+r[-\s]?value[:\s]+r?[-\s]?(\d+(?:\.\d+)?)', pdf_text, re.IGNORECASE)
        if roof_match:
            envelope['roof_r_value'] = float(roof_match.group(1))

        # Slab R-value
        slab_match = re.search(r'slab\s+r[-\s]?value[:\s]+r?[-\s]?(\d+(?:\.\d+)?)', pdf_text, re.IGNORECASE)
        if slab_match:
            envelope['slab_r_value'] = float(slab_match.group(1))

        # Window U-factor
        u_match = re.search(r'window\s+u[-\s]?factor[:\s]+(\d+\.\d+)', pdf_text, re.IGNORECASE)
        if u_match:
            envelope['window_u_factor'] = float(u_match.group(1))

        # Window SHGC
        shgc_match = re.search(r'(?:window\s+)?shgc[:\s]+(\d+\.\d+)', pdf_text, re.IGNORECASE)
        if shgc_match:
            envelope['window_shgc'] = float(shgc_match.group(1))

        if envelope:
            extracted['envelope'] = envelope
            self.data_sources['envelope'] = 'pdf'

        # Extract systems data
        systems = {}

        # Heating type and efficiency
        # Pattern allows for bullets/dashes before "Heating"
        heating_match = re.search(r'[-*]?\s*heating[:\s]+([^,\n]+)(?:,\s*(\d+)%?\s*efficiency)?', pdf_text, re.IGNORECASE)
        if heating_match:
            heating_desc = heating_match.group(1).strip().lower()
            # Map common descriptions to schema enum values
            if 'gas' in heating_desc and 'boiler' in heating_desc:
                systems['heating_type'] = 'boiler_gas'
            elif 'oil' in heating_desc and 'boiler' in heating_desc:
                systems['heating_type'] = 'boiler_oil'
            elif 'gas' in heating_desc and 'furnace' in heating_desc:
                systems['heating_type'] = 'gas_furnace'
            elif 'electric' in heating_desc and ('resistance' in heating_desc or 'baseboard' in heating_desc):
                systems['heating_type'] = 'electric_resistance'
            elif 'heat pump' in heating_desc:
                systems['heating_type'] = 'heat_pump'
            elif 'district' in heating_desc and 'steam' in heating_desc:
                systems['heating_type'] = 'district_steam'

            if heating_match.group(2):
                systems['heating_efficiency'] = float(heating_match.group(2)) / 100.0

        # Cooling type and efficiency (EER)
        # Pattern allows for bullets/dashes before "Cooling"
        cooling_match = re.search(r'[-*]?\s*cooling[:\s]+([^,\n]+)(?:,\s*(\d+(?:\.\d+)?)\s*eer)?', pdf_text, re.IGNORECASE)
        if cooling_match:
            cooling_desc = cooling_match.group(1).strip().lower()
            if 'chiller' in cooling_desc:
                systems['cooling_type'] = 'chiller'
            elif 'air conditioner' in cooling_desc or 'ac' in cooling_desc or 'dx' in cooling_desc or 'split' in cooling_desc:
                systems['cooling_type'] = 'air_conditioner'
            elif 'heat pump' in cooling_desc:
                systems['cooling_type'] = 'heat_pump'

            if cooling_match.group(2):
                systems['cooling_eer'] = float(cooling_match.group(2))

        # Ventilation
        vent_match = re.search(r'ventilation[:\s]+(\d+\.\d+)\s*cfm/sqft', pdf_text, re.IGNORECASE)
        if vent_match:
            systems['ventilation_cfm_per_sqft'] = float(vent_match.group(1))

        # Infiltration
        inf_match = re.search(r'infiltration[:\s]+(\d+\.\d+)\s*ach', pdf_text, re.IGNORECASE)
        if inf_match:
            systems['infiltration_ach'] = float(inf_match.group(1))

        if systems:
            extracted['systems'] = systems
            self.data_sources['systems'] = 'pdf'

        return extracted

    def fill_gaps_via_qa(self, building_data: Dict[str, Any],
                         gaps: List[str],
                         qa_responses: Dict[str, Any]) -> Dict[str, Any]:
        """Fill missing fields using Q&A responses.

        In production, this would prompt user interactively in conversation.
        For testing, responses are provided as a dict.

        Args:
            building_data: Partial building data
            gaps: List of missing field paths (e.g., ['envelope.wall_r_value'])
            qa_responses: Dict mapping gap paths to user-provided values

        Returns:
            Updated building data with gaps filled
        """
        # Deep copy to avoid mutating input
        result = copy.deepcopy(building_data)

        # Process all QA responses, not just those in gaps list
        # This handles cases where gaps shows 'lighting' but QA has 'lighting.interior_lpd_w_per_sqft'
        for qa_path, value in qa_responses.items():
            # Parse nested path (e.g., "envelope.wall_r_value")
            parts = qa_path.split('.')

            if len(parts) == 1:
                # Top-level field
                result[parts[0]] = value
                self.data_sources[parts[0]] = 'user_qa'

            elif len(parts) == 2:
                # Nested field
                parent, child = parts
                if parent not in result:
                    result[parent] = {}
                result[parent][child] = value
                self.data_sources[qa_path] = 'user_qa'

            else:
                # Deeply nested (rare, but handle it)
                current = result
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value
                self.data_sources[qa_path] = 'user_qa'

        return result

    def generate_qa_prompts(self, gaps: List[str]) -> Dict[str, str]:
        """Generate user-friendly prompts for missing fields.

        Args:
            gaps: List of missing field paths

        Returns:
            Dict mapping gap paths to user-friendly question strings
        """
        prompts = {}

        # Field-specific prompts with typical ranges
        field_prompts = {
            'geometry.floor_to_floor_ft': "What is the floor-to-floor height in feet? (typical: 12-14 ft for offices)",
            'geometry.floor_to_ceiling_ft': "What is the floor-to-ceiling height in feet? (typical: 9-10 ft)",
            'geometry.wwr': "What is the window-to-wall ratio? (typical: 0.2-0.4, enter as decimal)",

            'envelope.wall_r_value': "What is the wall R-value? (code minimum varies by climate zone)",
            'envelope.roof_r_value': "What is the roof R-value? (typical: R-20 to R-30)",
            'envelope.slab_r_value': "What is the slab/foundation R-value? (typical: R-5 to R-10)",
            'envelope.window_u_factor': "What is the window U-factor? (typical: 0.3-0.5 for double-pane)",
            'envelope.window_shgc': "What is the window SHGC (solar heat gain coefficient)? (typical: 0.25-0.40)",

            'systems.heating_efficiency': "What is the heating system efficiency? (enter as decimal, e.g., 0.85 for 85%)",
            'systems.cooling_eer': "What is the cooling system EER? (typical: 10-14 for chillers)",
            'systems.ventilation_cfm_per_sqft': "What is the ventilation rate in CFM per square foot? (typical: 0.06)",
            'systems.infiltration_ach': "What is the infiltration rate in ACH (air changes per hour)? (typical: 0.2-0.5)",

            'lighting.interior_lpd_w_per_sqft': "What is the interior lighting power density in W/sqft? (typical: 0.6-1.0 for offices)",
            'lighting.exterior_lpd_w_per_sqft': "What is the exterior lighting power density in W/sqft? (typical: 0.1-0.2)",

            'schedules.operating_hours_per_week': "How many hours per week is the building occupied? (typical: 40-50 for offices)"
        }

        for gap in gaps:
            if gap in field_prompts:
                prompts[gap] = field_prompts[gap]
            else:
                # Generic prompt for unmapped fields
                field_name = gap.split('.')[-1].replace('_', ' ').title()
                prompts[gap] = f"What is the {field_name}?"

        return prompts

    def integrate_modeler_geometry(self, building_data: Dict[str, Any],
                                   modeler_geometry: Dict[str, Any]) -> Dict[str, Any]:
        """Integrate geometry data from 3D modeler playground.

        The 3D modeler is a browser-based Three.js playground that allows
        users to visually define building geometry. It returns a JSON object
        with geometry parameters.

        Args:
            building_data: Partial building data
            modeler_geometry: Geometry dict from 3D modeler containing:
                - footprint_sqft: Building footprint area
                - stories: Number of stories
                - floor_to_floor_ft: Floor-to-floor height
                - floor_to_ceiling_ft: Floor-to-ceiling height
                - wwr: Window-to-wall ratio

        Returns:
            Updated building data with geometry section
        """
        # Deep copy to avoid mutating input
        result = copy.deepcopy(building_data)

        # Add geometry section (replaces any existing partial geometry)
        result['geometry'] = modeler_geometry

        # Track that geometry came from 3D modeler
        self.data_sources['geometry'] = '3d_modeler'

        # Track individual geometry fields for granular transparency
        for field in modeler_geometry.keys():
            self.data_sources[f'geometry.{field}'] = '3d_modeler'

        return result

    def needs_geometry_modeler(self, gaps: List[str]) -> bool:
        """Check if 3D geometry modeler is needed based on gaps.

        The modeler should be launched when critical geometry fields are missing.
        Critical fields: footprint_sqft, stories, floor heights, WWR.

        Args:
            gaps: List of missing field paths

        Returns:
            True if geometry modeler should be launched
        """
        critical_geometry_fields = [
            'geometry.footprint_sqft',
            'geometry.stories',
            'geometry.floor_to_floor_ft',
            'geometry.floor_to_ceiling_ft',
            'geometry.wwr'
        ]

        # If any critical geometry field is missing, recommend modeler
        for gap in gaps:
            if gap in critical_geometry_fields:
                return True

        # If entire geometry section is missing
        if 'geometry' in gaps:
            return True

        return False

    def validate_complete_model(self, building_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate building data against JSON schema.

        Args:
            building_data: Complete building model to validate

        Returns:
            Tuple of (is_valid: bool, errors: List[str])
            - is_valid: True if data passes validation
            - errors: List of user-friendly error messages (empty if valid)
        """
        if jsonschema is None:
            raise ImportError("jsonschema library is required for validation. Install with: pip install jsonschema")

        # Load schema
        schema_path = Path(__file__).parent / 'schemas' / 'building_model.json'
        with open(schema_path) as f:
            schema = json.load(f)

        errors = []

        try:
            # Create validator to get all errors
            validator = jsonschema.Draft7Validator(schema)
            validation_errors = list(validator.iter_errors(building_data))

            if not validation_errors:
                return (True, [])

            # Convert all validation errors to user-friendly messages
            for e in validation_errors:
                error_path = '.'.join(str(p) for p in e.path) if e.path else 'root'
                error_msg = f"Validation error at '{error_path}': {e.message}"
                errors.append(error_msg)

            return (False, errors)

        except SchemaError as e:
            # Schema itself is invalid (should not happen in production)
            errors.append(f"Schema error: {e.message}")
            return (False, errors)

        except Exception as e:
            # Catch-all for unexpected errors
            errors.append(f"Unexpected validation error: {str(e)}")
            return (False, errors)

    def get_data_source_summary(self) -> Dict[str, str]:
        """Get data source transparency report.

        Returns:
            Copy of data_sources dict showing which source provided each field
        """
        return copy.deepcopy(self.data_sources)

    @classmethod
    def estimate_dhw_thermal_kwh(cls, building_model: Dict[str, Any]) -> float:
        """Estimate annual domestic hot water (DHW) thermal energy demand.

        Uses building-type-specific estimation methods based on ASHRAE standards.

        Args:
            building_model: Complete building model dict

        Returns:
            Annual DHW thermal energy in kWh
        """
        building_type = building_model.get("building_type", "office")
        gfa_ft2 = building_model.get("project", {}).get("conditioned_area_ft2", 10000)

        if building_type in ("multi_unit_residential", "multifamily"):
            # Residential: based on occupancy and gallons per day per person
            units = building_model.get("residential_units", 1)
            occ = 2  # Assume 2 occupants per unit
            gpd = 18  # Gallons per day per person (ASHRAE)
            temp_rise_f = 60  # Temperature rise (F)
            # Formula: units * occ * gpd * 365 days * 8.33 lb/gal * temp_rise / 3412.14 Btu/kWh
            return units * occ * gpd * 365 * 8.33 * temp_rise_f / 3412.14

        elif building_type == "hotel":
            # Hotel: 0.68 kWh/ft²/yr (converted from thermal Btu)
            return gfa_ft2 * 0.0032 * 3412.14 / 3412.14  # Simplified to 0.68 kWh/ft²/yr

        elif building_type == "office":
            # Office: 0.10 kWh/ft²/yr
            return gfa_ft2 * 0.10

        elif building_type == "school":
            # School: 0.08 kWh/ft²/yr
            return gfa_ft2 * 0.08

        else:
            # Default: 0.05 kWh/ft²/yr
            return gfa_ft2 * 0.05

    @classmethod
    def get_dhw_fuel_kwh(cls, dhw_thermal_kwh: float, system_type: str, efficiency: float) -> float:
        """Convert DHW thermal demand to fuel consumption based on system type.

        Args:
            dhw_thermal_kwh: Thermal energy demand in kWh
            system_type: DHW system type ("gas", "heat_pump", "hpwh", or other)
            efficiency: System efficiency (0-1 for gas, COP for heat pumps)

        Returns:
            Annual DHW fuel consumption in kWh
        """
        if system_type == "gas":
            # Gas: divide by thermal efficiency
            return dhw_thermal_kwh / efficiency
        elif system_type in ("heat_pump", "hpwh"):
            # Heat pump: divide by COP
            return dhw_thermal_kwh / efficiency
        else:
            # Electric resistance or unknown: assume COP = 1.0
            return dhw_thermal_kwh

    def collect_complete_model(self,
                               building_uid: str,
                               pdf_paths: List[str],
                               qa_responses: Dict[str, Any],
                               modeler_geometry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Main orchestration: collect complete building model from all sources.

        Cascades through data sources in priority order:
        1. Audette MCP (get_building_model_details)
        2. PDF documents (extract_from_pdf)
        3. 3D geometry modeler (integrate_modeler_geometry)
        4. Interactive Q&A (fill_gaps_via_qa)

        Re-identifies gaps after each source and validates final model.

        Args:
            building_uid: Unique identifier for the building
            pdf_paths: List of paths to PDF documents
            qa_responses: Dict mapping gap paths to user-provided values
            modeler_geometry: Optional geometry data from 3D modeler

        Returns:
            Complete validated building model

        Raises:
            ValueError: If final model fails validation
        """
        # Step 1: Query Audette MCP
        building_data = self.query_mcp(building_uid)

        # Step 2: Extract data from PDFs
        for pdf_path in pdf_paths:
            pdf_data = self.extract_from_pdf(pdf_path)

            # Merge PDF data into building_data
            for section, section_data in pdf_data.items():
                if section not in building_data:
                    building_data[section] = section_data
                elif isinstance(section_data, dict) and isinstance(building_data[section], dict):
                    # Merge nested dicts (e.g., envelope, systems)
                    building_data[section].update(section_data)
                else:
                    # Replace non-dict values
                    building_data[section] = section_data

            # Re-identify gaps after PDF extraction
            gaps = self.identify_gaps(building_data)

        # Step 3: Integrate 3D modeler geometry if provided
        if modeler_geometry is not None:
            building_data = self.integrate_modeler_geometry(building_data, modeler_geometry)

        # Re-identify gaps after modeler
        gaps = self.identify_gaps(building_data)

        # Step 4: Fill remaining gaps via Q&A
        building_data = self.fill_gaps_via_qa(building_data, gaps, qa_responses)

        # Step 5: Filter to schema-defined fields only (remove extra fields from MCP)
        # The schema has additionalProperties: false, so we need to filter
        schema_fields = [
            'building_uid', 'geometry', 'envelope', 'systems',
            'lighting', 'schedules', 'data_sources'
        ]
        filtered_data = {k: v for k, v in building_data.items() if k in schema_fields}

        # Step 6: Validate final model
        is_valid, errors = self.validate_complete_model(filtered_data)

        if not is_valid:
            error_msg = "Model validation failed:\n" + "\n".join(errors)
            raise ValueError(error_msg)

        return filtered_data
