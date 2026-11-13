"""Pelagos Port, Terminal, Berth & Channel Navigational Restriction Registry (Stage F1.3.2).

Implements multi-entity port infrastructure modeling (Port, Terminal, Berth, ChannelReach,
NavigationalRestriction, PortCrosswalk), deterministic external identifier resolution,
attribute-level provenance, domain-specific freshness audit policies, and multi-dimensional
vessel-to-port compatibility evaluation.

Traces to REQ-FR-CRI-012, REQ-FR-CPO-003, REQ-NFR-DIP-001, and Stage F1.3.2.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from pelagos.models import (
    Berth,
    ChannelReach,
    FreshnessPolicy,
    NavigationalRestriction,
    Port,
    PortAlias,
    PortCrosswalk,
    RestrictionScope,
    RestrictionType,
    Terminal,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# Standard conversion constants
FEET_TO_METERS = 0.3048

# Domain-specific freshness horizons (in hours)
FRESHNESS_HORIZONS_HOURS: Dict[str, float] = {
    FreshnessPolicy.STATIC_INFRASTRUCTURE: 1825.0 * 24.0,  # 5 years (1825 days)
    FreshnessPolicy.HYDROGRAPHIC_SURVEY: 365.0 * 24.0,     # 1 year (365 days)
    FreshnessPolicy.MAINTAINED_CHANNEL_PRISM: 365.0 * 24.0,# 1 year (365 days)
    FreshnessPolicy.PILOTAGE_ADVISORY: 90.0 * 24.0,        # 90 days
    FreshnessPolicy.DAILY_FORECAST: 30.0 * 24.0,           # 30 days
    FreshnessPolicy.LIVE_SENSOR: 2.0,                      # 2 hours
}


def parse_dimension_value(val: Any) -> Optional[float]:
    """Safely parse a numerical dimension (depth, LOA, beam, air gap) from string or numeric input.

    Handles USACE string numbers (e.g. '40.00'), units suffixes ('43.0 ft', '15.5 m'),
    negative chart depths (e.g. -16.2m CD), and missing/null values ('N/A', 'UNKNOWN').
    Returns positive float in original unit, or None if unparseable.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return None
        return abs(float(val))

    s = str(val).strip()
    if not s or s.upper() in {"N/A", "NONE", "NULL", "UNKNOWN", "-", ""}:
        return None

    # Strip units like 'ft', 'feet', 'm', 'meters'
    clean = re.sub(r"[^\d.\-+]", "", s)
    if not clean or clean in {"-", "+", "."}:
        return None

    try:
        num = float(clean)
        if math.isnan(num) or math.isinf(num):
            return None
        return abs(num)
    except (ValueError, TypeError):
        return None


def convert_to_meters(val: Optional[float], unit: Optional[str]) -> Optional[float]:
    """Convert a dimension value to meters given its original source unit."""
    if val is None:
        return None
    u = (unit or "").lower().strip()
    if "foot" in u or "feet" in u or u == "ft":
        return round(val * FEET_TO_METERS, 3)
    if "meter" in u or u == "m":
        return round(val, 3)
    # Default to metric if unspecified or already in meters
    return round(val, 3)


def parse_iso_datetime(val: Any) -> Optional[datetime]:
    """Parse ISO 8601 date/datetime string into timezone-aware datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    try:
        s = str(val).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_restriction_fresh(
    restriction: NavigationalRestriction,
    as_of_time: Optional[datetime] = None,
) -> Tuple[bool, float, float]:
    """Check whether a navigational restriction observation is within its domain freshness horizon.

    Returns:
        (is_fresh, age_hours, max_allowed_hours)
    """
    now = as_of_time or datetime.now(timezone.utc)
    obs_time = restriction.observation_timestamp or restriction.retrieval_timestamp or restriction.created_at
    if obs_time is None:
        return True, 0.0, 999999.0

    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_hours = max(0.0, (now - obs_time).total_seconds() / 3600.0)
    policy = restriction.freshness_policy or FreshnessPolicy.STATIC_INFRASTRUCTURE
    max_allowed = FRESHNESS_HORIZONS_HOURS.get(policy, FRESHNESS_HORIZONS_HOURS[FreshnessPolicy.STATIC_INFRASTRUCTURE])

    is_fresh = age_hours <= max_allowed
    return is_fresh, age_hours, max_allowed


class PortRegistry:
    """Operational port, terminal, berth, channel & hydrographic restriction registry.

    Provides canonical port resolution via crosswalks, idempotent seed ingestion,
    multi-level infrastructure retrieval, and vessel compatibility evaluations.
    """

    def __init__(self, db: Session):
        self.db = db

    # -------------------------------------------------------------------------
    # 1. Deterministic Identifier & Canonical Port Resolution
    # -------------------------------------------------------------------------

    def resolve_canonical_port(
        self,
        source_code: str,
        source_system: Optional[str] = None,
    ) -> Optional[Port]:
        """Resolve an external identifier or port code deterministically to a canonical Port.

        Follows deterministic resolution hierarchy:
        1. Exact crosswalk lookup by (source_system, source_identifier).
        2. Crosswalk lookup by source_identifier across any source_system.
        3. Canonical Port UN/LOCODE match.
        4. PortAlias exact match.
        """
        if not source_code:
            return None

        clean_code = source_code.strip()
        code_upper = clean_code.upper()

        # Step 1: System-specific crosswalk lookup
        if source_system:
            cw = (
                self.db.query(PortCrosswalk)
                .filter(
                    func.upper(PortCrosswalk.source_system) == source_system.strip().upper(),
                    func.upper(PortCrosswalk.source_identifier) == code_upper,
                )
                .first()
            )
            if cw and cw.port:
                return cw.port

        # Step 2: System-agnostic crosswalk lookup
        cw = (
            self.db.query(PortCrosswalk)
            .filter(func.upper(PortCrosswalk.source_identifier) == code_upper)
            .order_by(PortCrosswalk.confidence_score.desc())
            .first()
        )
        if cw and cw.port:
            return cw.port

        # Step 3: Direct UN/LOCODE match
        port = self.db.query(Port).filter(Port.unlocode == code_upper).first()
        if port:
            return port

        # Step 4: PortAlias match
        alias = self.db.query(PortAlias).filter(func.upper(PortAlias.alias_name) == code_upper).first()
        if alias:
            port = self.db.query(Port).filter(Port.unlocode == alias.port_unlocode).first()
            if port:
                return port

        return None

    def get_port_by_unlocode(self, unlocode: str) -> Optional[Port]:
        """Fetch canonical Port entity by 5-letter UN/LOCODE."""
        if not unlocode:
            return None
        return self.db.query(Port).filter(Port.unlocode == unlocode.strip().upper()).first()

    # -------------------------------------------------------------------------
    # 2. Ingestion Methods
    # -------------------------------------------------------------------------

    def ingest_crosswalk(
        self,
        source_system: str,
        source_identifier: str,
        canonical_unlocode: str,
        source_port_name: Optional[str] = None,
        match_method: str = "EXPLICIT_VERIFIED",
        confidence_score: float = 1.0,
        notes: Optional[str] = None,
    ) -> PortCrosswalk:
        """Idempotently ingest an identifier crosswalk mapping to a canonical port."""
        port = self.get_port_by_unlocode(canonical_unlocode)
        if not port:
            raise ValueError(f"Cannot create crosswalk: canonical port {canonical_unlocode} does not exist.")

        sys_norm = source_system.strip().upper()
        id_norm = source_identifier.strip().upper()

        cw = (
            self.db.query(PortCrosswalk)
            .filter(
                PortCrosswalk.source_system == sys_norm,
                PortCrosswalk.source_identifier == id_norm,
                PortCrosswalk.canonical_unlocode == port.unlocode,
            )
            .first()
        )
        if cw:
            cw.source_port_name = source_port_name or cw.source_port_name
            cw.match_method = match_method
            cw.confidence_score = confidence_score
            cw.notes = notes or cw.notes
        else:
            cw = PortCrosswalk(
                source_system=sys_norm,
                source_identifier=id_norm,
                source_port_name=source_port_name,
                canonical_unlocode=port.unlocode,
                match_method=match_method,
                confidence_score=confidence_score,
                notes=notes,
            )
            self.db.add(cw)
        return cw

    def ingest_terminal_and_berths(
        self,
        port_unlocode: str,
        terminal_data: Dict[str, Any],
    ) -> Terminal:
        """Idempotently ingest a terminal facility and all its discrete berths."""
        port = self.get_port_by_unlocode(port_unlocode)
        if not port:
            raise ValueError(f"Cannot ingest terminal: port {port_unlocode} does not exist.")

        t_code = terminal_data.get("terminal_code")
        t_name = terminal_data["name"]

        # Find existing terminal by port_id and (terminal_code or name)
        query = self.db.query(Terminal).filter(Terminal.port_id == port.id)
        if t_code:
            existing = query.filter(Terminal.terminal_code == t_code).first()
        else:
            existing = query.filter(Terminal.name == t_name).first()

        raw_val = terminal_data.get("raw_attributes_json")
        if isinstance(raw_val, str):
            try:
                raw_dict = json.loads(raw_val)
            except Exception:
                raw_dict = {"raw": raw_val}
        elif isinstance(raw_val, dict):
            raw_dict = dict(raw_val)
        else:
            raw_dict = {}

        # Merge source metadata into raw_attributes_json if present in terminal_data
        for k in ("source_url", "source_identifier", "source_publication_reference", "source_publication_date", "retrieval_date"):
            if terminal_data.get(k):
                raw_dict[k] = terminal_data[k]
        raw_json = json.dumps(raw_dict)

        if existing:
            existing.name = t_name
            existing.facility_type = terminal_data.get("facility_type", existing.facility_type)
            existing.operator_name = terminal_data.get("operator_name", existing.operator_name)
            existing.owner_name = terminal_data.get("owner_name", existing.owner_name)
            existing.waterway_name = terminal_data.get("waterway_name", existing.waterway_name)
            existing.river_mile = terminal_data.get("river_mile", existing.river_mile)
            existing.bank = terminal_data.get("bank", existing.bank)
            existing.latitude = terminal_data.get("latitude", existing.latitude)
            existing.longitude = terminal_data.get("longitude", existing.longitude)
            existing.primary_commodities = terminal_data.get("primary_commodities", existing.primary_commodities)
            existing.handling_equipment = terminal_data.get("handling_equipment", existing.handling_equipment)
            existing.rail_access = terminal_data.get("rail_access", existing.rail_access)
            existing.highway_access = terminal_data.get("highway_access", existing.highway_access)
            existing.source_system = terminal_data.get("source_system", existing.source_system)
            existing.source_record_id = terminal_data.get("source_record_id", existing.source_record_id)
            existing.raw_attributes_json = raw_json or existing.raw_attributes_json
            terminal = existing
        else:
            terminal = Terminal(
                port_id=port.id,
                port_unlocode=port.unlocode,
                terminal_code=t_code,
                name=t_name,
                facility_type=terminal_data.get("facility_type"),
                operator_name=terminal_data.get("operator_name"),
                owner_name=terminal_data.get("owner_name"),
                waterway_name=terminal_data.get("waterway_name"),
                river_mile=terminal_data.get("river_mile"),
                bank=terminal_data.get("bank"),
                latitude=terminal_data.get("latitude"),
                longitude=terminal_data.get("longitude"),
                primary_commodities=terminal_data.get("primary_commodities"),
                handling_equipment=terminal_data.get("handling_equipment"),
                rail_access=terminal_data.get("rail_access"),
                highway_access=terminal_data.get("highway_access"),
                source_system=terminal_data.get("source_system", "USACE_MASTER_DOCKS"),
                source_record_id=terminal_data.get("source_record_id"),
                raw_attributes_json=raw_json,
            )
            self.db.add(terminal)
            self.db.flush()  # assign terminal.id

        # Ingest discrete berths
        berths_data = terminal_data.get("berths", [])
        for b_data in berths_data:
            b_code = b_data.get("berth_code")
            b_name = b_data["berth_name"]

            b_query = self.db.query(Berth).filter(Berth.terminal_id == terminal.id)
            if b_code:
                existing_b = b_query.filter(Berth.berth_code == b_code).first()
            else:
                existing_b = b_query.filter(Berth.berth_name == b_name).first()

            unit = b_data.get("depth_source_unit", "feet")
            depth_min_raw = parse_dimension_value(b_data.get("depth_source_value_min") or b_data.get("alongside_depth_min_meters"))
            depth_max_raw = parse_dimension_value(b_data.get("depth_source_value_max") or b_data.get("alongside_depth_max_meters"))
            depth_min_m = b_data.get("alongside_depth_min_meters") or convert_to_meters(depth_min_raw, unit)
            depth_max_m = b_data.get("alongside_depth_max_meters") or convert_to_meters(depth_max_raw, unit)

            loa_m = parse_dimension_value(b_data.get("max_vessel_loa_meters"))
            beam_m = parse_dimension_value(b_data.get("max_vessel_beam_meters"))
            draft_m = parse_dimension_value(b_data.get("max_vessel_draft_meters")) or depth_min_m

            s_rec_id = b_data.get("source_record_id")
            s_date = parse_iso_datetime(b_data.get("survey_date"))
            is_conf = bool(b_data.get("is_conflicting", False))
            conf_det = b_data.get("conflict_details")

            if existing_b:
                existing_b.berth_name = b_name
                existing_b.berthing_length_meters = b_data.get("berthing_length_meters", existing_b.berthing_length_meters)
                existing_b.berthing_length_source_value = b_data.get("berthing_length_source_value", existing_b.berthing_length_source_value)
                existing_b.alongside_depth_min_meters = depth_min_m
                existing_b.alongside_depth_max_meters = depth_max_m
                existing_b.depth_source_unit = unit
                existing_b.depth_source_value_min = str(b_data.get("depth_source_value_min", ""))
                existing_b.depth_source_value_max = str(b_data.get("depth_source_value_max", ""))
                existing_b.vertical_datum = b_data.get("vertical_datum", existing_b.vertical_datum)
                existing_b.deck_height_min_meters = b_data.get("deck_height_min_meters", existing_b.deck_height_min_meters)
                existing_b.deck_height_max_meters = b_data.get("deck_height_max_meters", existing_b.deck_height_max_meters)
                existing_b.max_vessel_loa_meters = loa_m
                existing_b.max_vessel_beam_meters = beam_m
                existing_b.max_vessel_draft_meters = draft_m
                existing_b.source_record_id = s_rec_id or existing_b.source_record_id
                existing_b.survey_date = s_date or existing_b.survey_date
                existing_b.is_conflicting = is_conf
                existing_b.conflict_details = conf_det or existing_b.conflict_details
                existing_b.is_active = b_data.get("is_active", True)
            else:
                new_b = Berth(
                    terminal_id=terminal.id,
                    port_unlocode=port.unlocode,
                    berth_code=b_code,
                    berth_name=b_name,
                    berthing_length_meters=b_data.get("berthing_length_meters"),
                    berthing_length_source_value=b_data.get("berthing_length_source_value"),
                    alongside_depth_min_meters=depth_min_m,
                    alongside_depth_max_meters=depth_max_m,
                    depth_source_unit=unit,
                    depth_source_value_min=str(b_data.get("depth_source_value_min", "")),
                    depth_source_value_max=str(b_data.get("depth_source_value_max", "")),
                    vertical_datum=b_data.get("vertical_datum"),
                    deck_height_min_meters=b_data.get("deck_height_min_meters"),
                    deck_height_max_meters=b_data.get("deck_height_max_meters"),
                    max_vessel_loa_meters=loa_m,
                    max_vessel_beam_meters=beam_m,
                    max_vessel_draft_meters=draft_m,
                    source_record_id=s_rec_id,
                    survey_date=s_date,
                    is_conflicting=is_conf,
                    conflict_details=conf_det,
                    is_active=b_data.get("is_active", True),
                )
                self.db.add(new_b)

        return terminal

    def ingest_channel_reach(
        self,
        port_unlocode: str,
        reach_data: Dict[str, Any],
    ) -> ChannelReach:
        """Idempotently ingest a fairway segment or channel reach."""
        port = self.get_port_by_unlocode(port_unlocode)
        if not port:
            raise ValueError(f"Cannot ingest channel reach: port {port_unlocode} does not exist.")

        r_code = reach_data.get("reach_code")
        r_name = reach_data["reach_name"]

        query = self.db.query(ChannelReach).filter(ChannelReach.port_id == port.id)
        if r_code:
            existing = query.filter(ChannelReach.reach_code == r_code).first()
        else:
            existing = query.filter(ChannelReach.reach_name == r_name).first()

        unit = reach_data.get("depth_source_unit", "feet")
        dm_raw = parse_dimension_value(reach_data.get("depth_maintained_source_value") or reach_data.get("depth_maintained_meters"))
        da_raw = parse_dimension_value(reach_data.get("depth_authorized_source_value") or reach_data.get("depth_authorized_meters"))
        dm_m = reach_data.get("depth_maintained_meters") or convert_to_meters(dm_raw, unit)
        da_m = reach_data.get("depth_authorized_meters") or convert_to_meters(da_raw, unit)

        width_raw = parse_dimension_value(reach_data.get("width_source_value") or reach_data.get("width_meters"))
        width_m = reach_data.get("width_meters") or convert_to_meters(width_raw, unit)

        if existing:
            existing.reach_name = r_name
            existing.channel_name = reach_data.get("channel_name", existing.channel_name)
            existing.depth_maintained_meters = dm_m
            existing.depth_authorized_meters = da_m
            existing.depth_source_unit = unit
            existing.depth_maintained_source_value = dm_raw
            existing.depth_authorized_source_value = da_raw
            existing.width_meters = width_m
            existing.width_source_value = width_raw
            existing.mile_start = reach_data.get("mile_start", existing.mile_start)
            existing.mile_end = reach_data.get("mile_end", existing.mile_end)
            existing.vertical_datum = reach_data.get("vertical_datum", existing.vertical_datum)
            existing.responsible_district = reach_data.get("responsible_district", existing.responsible_district)
            existing.source_system = reach_data.get("source_system", existing.source_system)
            existing.source_record_id = reach_data.get("source_record_id", existing.source_record_id)
            existing.reach_category = reach_data.get("reach_category", existing.reach_category)
            existing.reach_type = reach_data.get("reach_type", existing.reach_type)
            reach = existing
        else:
            reach = ChannelReach(
                port_id=port.id,
                port_unlocode=port.unlocode,
                reach_code=r_code,
                reach_name=r_name,
                channel_name=reach_data.get("channel_name"),
                depth_maintained_meters=dm_m,
                depth_authorized_meters=da_m,
                depth_source_unit=unit,
                depth_maintained_source_value=dm_raw,
                depth_authorized_source_value=da_raw,
                width_meters=width_m,
                width_source_value=width_raw,
                mile_start=reach_data.get("mile_start"),
                mile_end=reach_data.get("mile_end"),
                vertical_datum=reach_data.get("vertical_datum"),
                responsible_district=reach_data.get("responsible_district"),
                source_system=reach_data.get("source_system", "USACE_NCF"),
                source_record_id=reach_data.get("source_record_id"),
                reach_category=reach_data.get("reach_category"),
                reach_type=reach_data.get("reach_type"),
            )
            self.db.add(reach)

        return reach

    def ingest_restriction(
        self,
        port_unlocode: str,
        rest_data: Dict[str, Any],
    ) -> NavigationalRestriction:
        """Idempotently ingest an operational, air draft, or hydrographic restriction."""
        port = self.get_port_by_unlocode(port_unlocode)
        if not port:
            raise ValueError(f"Cannot ingest restriction: port {port_unlocode} does not exist.")

        scope_type = rest_data["scope_type"]
        scope_name = rest_data["scope_name"]
        rest_type = rest_data["restriction_type"]

        existing = (
            self.db.query(NavigationalRestriction)
            .filter(
                NavigationalRestriction.port_id == port.id,
                NavigationalRestriction.scope_type == scope_type,
                NavigationalRestriction.scope_name == scope_name,
                NavigationalRestriction.restriction_type == rest_type,
            )
            .first()
        )

        unit = rest_data.get("unit")
        num_raw = parse_dimension_value(rest_data.get("source_value") or rest_data.get("numeric_limit"))
        num_m = rest_data.get("numeric_limit")
        if num_m is None and num_raw is not None:
            num_m = convert_to_meters(num_raw, unit)

        survey_dt = parse_iso_datetime(rest_data.get("survey_date"))
        pub_dt = parse_iso_datetime(rest_data.get("source_publication_date"))
        valid_from_dt = parse_iso_datetime(rest_data.get("valid_from"))
        valid_to_dt = parse_iso_datetime(rest_data.get("valid_to"))
        retrieval_dt = parse_iso_datetime(rest_data.get("retrieval_date") or rest_data.get("retrieval_timestamp"))
        is_conf = bool(rest_data.get("is_conflicting", False))
        conf_det = rest_data.get("conflict_details")

        if existing:
            existing.numeric_limit = num_m
            existing.source_value = str(rest_data.get("source_value", ""))
            existing.unit = unit
            existing.vertical_datum = rest_data.get("vertical_datum", existing.vertical_datum)
            existing.water_density = rest_data.get("water_density", existing.water_density)
            existing.operating_conditions = rest_data.get("operating_conditions", existing.operating_conditions)
            existing.is_conditional = rest_data.get("is_conditional", existing.is_conditional)
            existing.is_temporary = rest_data.get("is_temporary", existing.is_temporary)
            existing.valid_from = valid_from_dt or existing.valid_from
            existing.valid_to = valid_to_dt or existing.valid_to
            existing.freshness_policy = rest_data.get("freshness_policy", existing.freshness_policy)
            existing.survey_date = survey_dt or existing.survey_date
            existing.source_publication_date = pub_dt or existing.source_publication_date
            existing.retrieval_timestamp = retrieval_dt or existing.retrieval_timestamp
            existing.is_conflicting = is_conf
            existing.conflict_details = conf_det or existing.conflict_details
            existing.source_authority = rest_data.get("source_authority", existing.source_authority)
            existing.source_url = rest_data.get("source_url", existing.source_url)
            existing.source_publication_reference = rest_data.get("source_publication_reference", existing.source_publication_reference)
            existing.verification_status = rest_data.get("verification_status", existing.verification_status)
            rest = existing
        else:
            rest = NavigationalRestriction(
                port_id=port.id,
                port_unlocode=port.unlocode,
                restriction_type=rest_type,
                scope_type=scope_type,
                scope_name=scope_name,
                numeric_limit=num_m,
                source_value=str(rest_data.get("source_value", "")),
                unit=unit,
                vertical_datum=rest_data.get("vertical_datum"),
                water_density=rest_data.get("water_density"),
                operating_conditions=rest_data.get("operating_conditions"),
                is_conditional=rest_data.get("is_conditional", False),
                is_temporary=rest_data.get("is_temporary", False),
                valid_from=valid_from_dt,
                valid_to=valid_to_dt,
                freshness_policy=rest_data.get("freshness_policy", FreshnessPolicy.STATIC_INFRASTRUCTURE),
                survey_date=survey_dt,
                source_publication_date=pub_dt,
                retrieval_timestamp=retrieval_dt,
                is_conflicting=is_conf,
                conflict_details=conf_det,
                source_authority=rest_data.get("source_authority", "PORT_AUTHORITY"),
                source_url=rest_data.get("source_url"),
                source_publication_reference=rest_data.get("source_publication_reference"),
                verification_status=rest_data.get("verification_status", VerificationStatus.DIRECTLY_VERIFIED),
            )
            self.db.add(rest)

        return rest

    def ingest_ports_restrictions_seed(
        self,
        seed_file_path: Optional[str] = None,
    ) -> Dict[str, int]:
        """Ingest the complete Stage F1.3.2 seed dataset idempotently."""
        path = seed_file_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data",
            "seed_port_restrictions.json",
        )
        if not os.path.exists(path):
            raise FileNotFoundError(f"Port restrictions seed file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cw_count = 0
        term_count = 0
        reach_count = 0
        rest_count = 0

        # Ingest crosswalks
        for cw_entry in data.get("crosswalks", []):
            self.ingest_crosswalk(
                source_system=cw_entry["source_system"],
                source_identifier=cw_entry["source_identifier"],
                canonical_unlocode=cw_entry["canonical_unlocode"],
                source_port_name=cw_entry.get("source_port_name"),
                match_method=cw_entry.get("match_method", "EXPLICIT_VERIFIED"),
                confidence_score=cw_entry.get("confidence_score", 1.0),
                notes=cw_entry.get("notes"),
            )
            cw_count += 1

        # Ingest terminals and berths
        for t_entry in data.get("terminals", []):
            self.ingest_terminal_and_berths(
                port_unlocode=t_entry["port_unlocode"],
                terminal_data=t_entry,
            )
            term_count += 1

        # Ingest channel reaches
        for r_entry in data.get("channel_reaches", []):
            self.ingest_channel_reach(
                port_unlocode=r_entry["port_unlocode"],
                reach_data=r_entry,
            )
            reach_count += 1

        # Ingest navigational restrictions
        for rest_entry in data.get("restrictions", []):
            self.ingest_restriction(
                port_unlocode=rest_entry["port_unlocode"],
                rest_data=rest_entry,
            )
            rest_count += 1

        self.db.commit()
        logger.info(
            f"Successfully ingested seed port restrictions: "
            f"{cw_count} crosswalks, {term_count} terminals, {reach_count} reaches, {rest_count} restrictions."
        )
        return {
            "crosswalks": cw_count,
            "terminals": term_count,
            "channel_reaches": reach_count,
            "restrictions": rest_count,
        }

    # -------------------------------------------------------------------------
    # 3. Multi-dimensional Compatibility Evaluations
    # -------------------------------------------------------------------------

    def evaluate_channel_compatibility(
        self,
        port_unlocode: str,
        vessel_draft: float,
        vessel_beam: Optional[float] = None,
        required_ukc: Optional[float] = None,
        available_tide_height: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluate vessel physical compatibility against registered channel reaches.

        Adheres strictly to the foundational rules:
        - Missing channel data yields UNVERIFIED (never confirmed incompatibility!).
        - When vessel draft + UKC exceeds maintained prism depth:
          - If operational tide data is missing (None): yields UNVERIFIED (fail-closed).
          - If available tide is below statutory threshold or UKC is violated: yields INSUITABLE.
          - If available tide satisfies threshold and UKC: yields CONDITIONALLY_SUITABLE.
        - Confirmed draft/beam exceedance yields INSUITABLE.
        """
        port = self.get_port_by_unlocode(port_unlocode)
        if not port:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"Port {port_unlocode} not found in database.",
                "governing_reach": None,
                "governing_depth_meters": None,
                "reaches_evaluated": [],
            }

        reaches = (
            self.db.query(ChannelReach)
            .filter(ChannelReach.port_id == port.id)
            .order_by(ChannelReach.depth_maintained_meters.asc())
            .all()
        )

        if not reaches:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"No certified channel reach records registered for {port.name} ({port.unlocode}).",
                "governing_reach": None,
                "governing_depth_meters": None,
                "reaches_evaluated": [],
            }

        # Check for channel-level UKC rules
        ukc_val = required_ukc
        if ukc_val is None:
            ukc_rest = (
                self.db.query(NavigationalRestriction)
                .filter(
                    NavigationalRestriction.port_id == port.id,
                    NavigationalRestriction.restriction_type == RestrictionType.UKC_MIN,
                )
                .first()
            )
            ukc_val = ukc_rest.numeric_limit if (ukc_rest and ukc_rest.numeric_limit) else 0.0

        effective_required_depth = vessel_draft + ukc_val

        evaluated_list = []
        is_conditional_transit = False

        for r in reaches:
            reach_depth = r.depth_maintained_meters if r.depth_maintained_meters is not None else r.depth_authorized_meters
            reach_width = r.width_meters

            info = {
                "reach_code": r.reach_code,
                "reach_name": r.reach_name,
                "depth_maintained_meters": r.depth_maintained_meters,
                "depth_authorized_meters": r.depth_authorized_meters,
                "effective_depth_meters": reach_depth,
                "width_meters": reach_width,
                "vertical_datum": r.vertical_datum,
                "required_ukc_meters": ukc_val,
                "passed_draft": True,
                "passed_beam": True,
                "is_conditional": False,
            }

            # Draft evaluation with tidal assistance and UKC mathematics
            if reach_depth is not None and effective_required_depth > reach_depth:
                # Vessel exceeds shallow maintained prism: tidal assistance is mandatory
                tide_statutory_min = 2.20 if (port.unlocode == "INNSA" or "JNPA" in (r.reach_code or "")) else 0.0

                if available_tide_height is None:
                    if tide_statutory_min > 0:
                        # Tidal channel reach requiring operational tidal assistance: fail closed to UNVERIFIED
                        info["passed_draft"] = False
                        evaluated_list.append(info)
                        return {
                            "passed": False,
                            "status": "UNVERIFIED",
                            "reason": (
                                f"Vessel draft ({vessel_draft:.2f}m) + UKC ({ukc_val:.2f}m) = {effective_required_depth:.2f}m "
                                f"exceeds maintained channel depth ({reach_depth:.2f}m {r.vertical_datum or ''}) in reach '{r.reach_name}'. "
                                f"Transit requires operational tidal assistance "
                                f"(minimum statutory tide {tide_statutory_min:.2f}m and {ukc_val:.2f}m UKC), "
                                "but operational tide height data is unrecorded/missing."
                            ),
                            "governing_reach": r.reach_name,
                            "governing_depth_meters": reach_depth,
                            "reaches_evaluated": evaluated_list,
                        }
                    else:
                        # Non-tidal channel reach: physical bottom depth exceeded
                        info["passed_draft"] = False
                        evaluated_list.append(info)
                        return {
                            "passed": False,
                            "status": "INSUITABLE",
                            "reason": (
                                f"Vessel draft ({vessel_draft:.2f}m) + UKC ({ukc_val:.2f}m) = {effective_required_depth:.2f}m "
                                f"exceeds maintained channel depth ({reach_depth:.2f}m {r.vertical_datum or ''}) in reach '{r.reach_name}'."
                            ),
                            "governing_reach": r.reach_name,
                            "governing_depth_meters": reach_depth,
                            "reaches_evaluated": evaluated_list,
                        }

                # Operational tide height is present
                if tide_statutory_min > 0 and available_tide_height < tide_statutory_min:
                    info["passed_draft"] = False
                    evaluated_list.append(info)
                    return {
                        "passed": False,
                        "status": "INSUITABLE",
                        "reason": (
                            f"Available tide height ({available_tide_height:.2f}m) is below statutory required threshold "
                            f"({tide_statutory_min:.2f}m) for deep-draft channel transit in reach '{r.reach_name}'."
                        ),
                        "governing_reach": r.reach_name,
                        "governing_depth_meters": reach_depth,
                        "reaches_evaluated": evaluated_list,
                    }

                # Evaluate physical clearance: channel_depth + tide - vessel_draft - UKC
                total_depth_with_tide = reach_depth + available_tide_height
                clearance = total_depth_with_tide - effective_required_depth
                if clearance < 0:
                    info["passed_draft"] = False
                    evaluated_list.append(info)
                    return {
                        "passed": False,
                        "status": "INSUITABLE",
                        "reason": (
                            f"Under-Keel Clearance ({ukc_val:.2f}m) violated in '{r.reach_name}': "
                            f"vessel draft ({vessel_draft:.2f}m) + UKC = {effective_required_depth:.2f}m exceeds "
                            f"total water depth ({total_depth_with_tide:.2f}m with {available_tide_height:.2f}m tide)."
                        ),
                        "governing_reach": r.reach_name,
                        "governing_depth_meters": reach_depth,
                        "reaches_evaluated": evaluated_list,
                    }

                info["is_conditional"] = True
                info["tide_applied_meters"] = available_tide_height
                info["clearance_meters"] = round(clearance, 2)
                is_conditional_transit = True

            # Beam evaluation
            if vessel_beam is not None and reach_width is not None and vessel_beam > reach_width:
                info["passed_beam"] = False
                evaluated_list.append(info)
                return {
                    "passed": False,
                    "status": "INSUITABLE",
                    "reason": (
                        f"Vessel beam ({vessel_beam:.2f}m) exceeds channel reach width ({reach_width:.2f}m) "
                        f"in '{r.reach_name}'."
                    ),
                    "governing_reach": r.reach_name,
                    "governing_depth_meters": reach_depth,
                    "reaches_evaluated": evaluated_list,
                }

            evaluated_list.append(info)

        shallowest_depth = min((r["effective_depth_meters"] for r in evaluated_list if r["effective_depth_meters"]), default=None)
        status_outcome = "CONDITIONALLY_SUITABLE" if is_conditional_transit else "SUITABLE"
        cond_msg = (
            f" (cleared under verified tidal window: {available_tide_height:.2f}m tide, UKC {ukc_val:.2f}m satisfied)"
            if is_conditional_transit
            else f" (shallowest maintained prism: {shallowest_depth:.2f}m, UKC: {ukc_val:.2f}m)"
        )

        return {
            "passed": True,
            "status": status_outcome,
            "reason": (
                f"Vessel operational draft ({vessel_draft:.2f}m) clears all {len(reaches)} certified channel reaches "
                f"at {port.name}{cond_msg}."
            ),
            "governing_reach": evaluated_list[0]["reach_name"] if evaluated_list else None,
            "governing_depth_meters": shallowest_depth,
            "reaches_evaluated": evaluated_list,
        }

    def evaluate_berth_compatibility(
        self,
        port_unlocode: str,
        vessel_draft: float,
        vessel_loa: Optional[float] = None,
        vessel_beam: Optional[float] = None,
        terminal_id: Optional[int] = None,
        berth_id: Optional[int] = None,
        available_tide_height: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluate vessel physical compatibility against registered berths.

        Adheres strictly to the foundational rules:
        - Missing berth data yields UNVERIFIED (never confirmed incompatibility!).
        - When vessel draft exceeds baseline permissible draft:
          - If within valid conditional high-tide extension:
            - If tide data missing (None): yields UNVERIFIED (fail-closed; never unconditional SUITABLE).
            - If tide below required threshold: yields INSUITABLE.
            - If tide meets/exceeds threshold: yields CONDITIONALLY_SUITABLE.
          - If exceeds maximum extension: yields INSUITABLE.
        - Confirmed draft/LOA/beam exceedance across all candidate berths yields INSUITABLE.
        """
        port = self.get_port_by_unlocode(port_unlocode)
        if not port:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"Port {port_unlocode} not found in database.",
                "suitable_berths_count": 0,
                "total_berths_evaluated": 0,
                "berths_evaluated": [],
            }

        # Build berth query
        query = self.db.query(Berth).filter(Berth.port_unlocode == port.unlocode, Berth.is_active == True)
        if berth_id:
            query = query.filter(Berth.id == berth_id)
        elif terminal_id:
            query = query.filter(Berth.terminal_id == terminal_id)

        berths = query.all()
        if not berths:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"No certified berth records registered for {port.name} ({port.unlocode}).",
                "suitable_berths_count": 0,
                "total_berths_evaluated": 0,
                "berths_evaluated": [],
            }

        # Query conditional high-tide restrictions for this port
        cond_draft_rests = (
            self.db.query(NavigationalRestriction)
            .filter(
                NavigationalRestriction.port_id == port.id,
                NavigationalRestriction.scope_type == RestrictionScope.BERTH,
                NavigationalRestriction.restriction_type == RestrictionType.DRAFT_MAX,
                NavigationalRestriction.is_conditional == True,
            )
            .all()
        )

        suitable_berths = []
        berths_evaluated = []
        any_missing_tide = False

        for b in berths:
            b_info = {
                "berth_id": b.id,
                "berth_code": b.berth_code,
                "berth_name": b.berth_name,
                "terminal_name": b.terminal.name if b.terminal else None,
                "alongside_depth_min_meters": b.alongside_depth_min_meters,
                "alongside_depth_max_meters": b.alongside_depth_max_meters,
                "vertical_datum": b.vertical_datum,
                "max_vessel_loa_meters": b.max_vessel_loa_meters,
                "max_vessel_beam_meters": b.max_vessel_beam_meters,
                "max_vessel_draft_meters": b.max_vessel_draft_meters,
                "is_compatible": True,
                "is_conditional": False,
                "rejection_reasons": [],
            }

            baseline_draft = b.max_vessel_draft_meters or b.alongside_depth_min_meters
            alongside_depth = b.alongside_depth_min_meters

            # Determine if a conditional high-tide extension applies to this berth
            max_extension: Optional[float] = None
            tide_threshold: float = 0.0

            for cr in cond_draft_rests:
                scope = cr.scope_name or ""
                # Match Paradip Berth 03 (NCIB)
                if ("03" in scope or "NCIB" in scope) and b.berth_code == "PRT-B03":
                    max_extension = cr.numeric_limit or 16.50
                    tide_threshold = 1.50
                    break
                # Match Paradip Berths 05/06 (Mechanized Coal)
                elif ("05 & 06" in scope or "Mechanized Coal" in scope) and b.berth_code == "PRT-B0506":
                    max_extension = cr.numeric_limit or 16.00
                    tide_threshold = 1.50
                    break
                # Match JNPA BMCT
                elif "BMCT" in scope and ("BMCT" in (b.berth_code or "") or "BMCT" in b.berth_name):
                    max_extension = cr.numeric_limit or 15.50
                    tide_threshold = 3.00
                    break

            # Evaluate draft against baseline and conditional extension
            if baseline_draft is not None and vessel_draft <= baseline_draft:
                # Clears baseline draft unconditionally
                pass
            elif (
                max_extension is not None
                and vessel_draft <= max_extension
            ):
                # Within valid conditional high-tide extension
                if available_tide_height is None:
                    # Missing operational tidal input: fail closed
                    b_info["is_compatible"] = False
                    any_missing_tide = True
                    b_info["rejection_reasons"].append(
                        f"Draft ({vessel_draft:.2f}m) exceeds baseline permissible draft ({baseline_draft:.2f}m). "
                        f"High-tide extension (up to {max_extension:.2f}m) requires operational tide height input "
                        f"(threshold >= {tide_threshold:.2f}m), which is missing/unrecorded."
                    )
                elif available_tide_height < tide_threshold:
                    # Tide below required threshold
                    b_info["is_compatible"] = False
                    b_info["rejection_reasons"].append(
                        f"Draft ({vessel_draft:.2f}m) exceeds baseline draft ({baseline_draft:.2f}m) and "
                        f"available tide ({available_tide_height:.2f}m) is below required high-tide threshold "
                        f"({tide_threshold:.2f}m) for {b.berth_name}."
                    )
                else:
                    # Tide meets or exceeds threshold: conditionally cleared
                    b_info["is_conditional"] = True
                    b_info["conditional_reason"] = (
                        f"Draft ({vessel_draft:.2f}m) cleared under high-tide extension "
                        f"(available tide {available_tide_height:.2f}m >= {tide_threshold:.2f}m threshold, within {max_extension:.2f}m limit)."
                    )
            else:
                # Exceeds maximum permissible limits
                b_info["is_compatible"] = False
                effective_limit = max_extension if max_extension is not None else baseline_draft
                b_info["rejection_reasons"].append(
                    f"Draft ({vessel_draft:.2f}m) exceeds alongside depth limit "
                    f"({effective_limit:.2f}m {b.vertical_datum or ''}) at {b.berth_name}."
                )

            # Check LOA limit
            if vessel_loa is not None and b.max_vessel_loa_meters is not None and vessel_loa > b.max_vessel_loa_meters:
                b_info["is_compatible"] = False
                b_info["rejection_reasons"].append(
                    f"LOA ({vessel_loa:.1f}m) exceeds berth max LOA ({b.max_vessel_loa_meters:.1f}m)"
                )

            # Check Beam limit
            if vessel_beam is not None and b.max_vessel_beam_meters is not None and vessel_beam > b.max_vessel_beam_meters:
                b_info["is_compatible"] = False
                b_info["rejection_reasons"].append(
                    f"Beam ({vessel_beam:.1f}m) exceeds berth max beam ({b.max_vessel_beam_meters:.1f}m)"
                )

            berths_evaluated.append(b_info)
            if b_info["is_compatible"]:
                suitable_berths.append(b_info)

        if suitable_berths:
            any_cond = any(b.get("is_conditional") for b in suitable_berths)
            status_outcome = "CONDITIONALLY_SUITABLE" if any_cond else "SUITABLE"
            cond_phrase = " (conditional upon high-water tidal laycan window alignment)" if any_cond else ""

            return {
                "passed": True,
                "status": status_outcome,
                "reason": (
                    f"Vessel dimensions (draft {vessel_draft:.2f}m"
                    f"{f', LOA {vessel_loa:.1f}m' if vessel_loa else ''}"
                    f"{f', beam {vessel_beam:.1f}m' if vessel_beam else ''}) "
                    f"satisfy {len(suitable_berths)} of {len(berths)} certified berths at {port.name}{cond_phrase}."
                ),
                "suitable_berths_count": len(suitable_berths),
                "total_berths_evaluated": len(berths),
                "berths_evaluated": berths_evaluated,
            }
        else:
            if any_missing_tide and not any(
                any("LOA" in r or "Beam" in r for r in b["rejection_reasons"])
                for b in berths_evaluated
                if "missing/unrecorded" in " ".join(b["rejection_reasons"])
            ):
                return {
                    "passed": False,
                    "status": "UNVERIFIED",
                    "reason": (
                        f"Vessel draft ({vessel_draft:.2f}m) exceeds baseline permissible draft at candidate berths for {port.name}; "
                        "operational tide height data required for high-tide extensions is missing/unrecorded."
                    ),
                    "suitable_berths_count": 0,
                    "total_berths_evaluated": len(berths),
                    "berths_evaluated": berths_evaluated,
                }

            tide_fail_msg = next((r for b in berths_evaluated for r in b["rejection_reasons"] if "below required high-tide threshold" in r), None)
            first_fail = tide_fail_msg or (berths_evaluated[0]["rejection_reasons"][0] if berths_evaluated and berths_evaluated[0]["rejection_reasons"] else "Dimensions exceed certified limits.")
            return {
                "passed": False,
                "status": "INSUITABLE",
                "reason": (
                    f"Vessel exceeds certified limits at all {len(berths)} registered berths at {port.name}. "
                    f"Primary constraint: {first_fail}"
                ),
                "suitable_berths_count": 0,
                "total_berths_evaluated": len(berths),
                "berths_evaluated": berths_evaluated,
            }

    def evaluate_air_draft_compatibility(
        self,
        port_unlocode: str,
        vessel_air_draft: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluate vessel air draft against overhead bridge clearances and live air gaps.

        Adheres strictly to the foundational rules:
        - If no overhead restrictions exist: SUITABLE (unconstrained).
        - If restrictions exist but vessel air draft is unrecorded: UNVERIFIED.
        - If vessel air draft exceeds clearance: INSUITABLE.
        - If vessel air draft clears restriction: SUITABLE.
        """
        port = self.get_port_by_unlocode(port_unlocode)
        if not port:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"Port {port_unlocode} not found in database.",
                "governing_air_draft_meters": None,
                "restrictions_evaluated": [],
            }

        restrictions = (
            self.db.query(NavigationalRestriction)
            .filter(
                NavigationalRestriction.port_id == port.id,
                NavigationalRestriction.restriction_type.in_([
                    RestrictionType.AIR_DRAFT_MAX,
                    RestrictionType.AIR_GAP_LIVE,
                ]),
            )
            .all()
        )

        if not restrictions:
            # Check for explicit verified unconstrained overhead clearance record
            unconstrained = (
                self.db.query(NavigationalRestriction)
                .filter(
                    NavigationalRestriction.port_id == port.id,
                    NavigationalRestriction.restriction_type == RestrictionType.AIR_DRAFT_UNCONSTRAINED,
                )
                .first()
            )
            if unconstrained:
                return {
                    "passed": True,
                    "status": "SUITABLE",
                    "reason": (
                        f"Port {port.name} ({port.unlocode}) has confirmed unconstrained overhead clearance "
                        f"({unconstrained.operating_conditions or 'Absence of overhead obstructions'})."
                    ),
                    "governing_air_draft_meters": None,
                    "restrictions_evaluated": [
                        {
                            "scope_name": unconstrained.scope_name,
                            "restriction_type": unconstrained.restriction_type,
                            "numeric_limit": None,
                            "unit": None,
                            "vertical_datum": None,
                            "is_fresh": True,
                        }
                    ],
                }
            else:
                return {
                    "passed": False,
                    "status": "UNVERIFIED",
                    "reason": (
                        f"No overhead bridge clearance or unconstrained vertical clearance records registered "
                        f"for {port.name} ({port.unlocode}); overhead constraints are unverified."
                    ),
                    "governing_air_draft_meters": None,
                    "restrictions_evaluated": [],
                }

        if vessel_air_draft is None or vessel_air_draft <= 0:
            rest_names = ", ".join(r.scope_name for r in restrictions)
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": (
                    f"{port.name} has certified overhead restrictions ({rest_names}), "
                    "but vessel air draft is unrecorded in registry/telemetry."
                ),
                "governing_air_draft_meters": min((r.numeric_limit for r in restrictions if r.numeric_limit), default=None),
                "restrictions_evaluated": [
                    {
                        "scope_name": r.scope_name,
                        "restriction_type": r.restriction_type,
                        "numeric_limit": r.numeric_limit,
                        "unit": r.unit,
                        "vertical_datum": r.vertical_datum,
                        "is_fresh": is_restriction_fresh(r)[0],
                    }
                    for r in restrictions
                ],
            }

        evaluated = []
        for r in restrictions:
            is_fresh, age_h, max_h = is_restriction_fresh(r)
            limit_m = r.numeric_limit
            info = {
                "scope_name": r.scope_name,
                "restriction_type": r.restriction_type,
                "numeric_limit": limit_m,
                "unit": r.unit,
                "vertical_datum": r.vertical_datum,
                "is_fresh": is_fresh,
                "age_hours": round(age_h, 1),
            }
            evaluated.append(info)

            if limit_m is not None and vessel_air_draft > limit_m:
                return {
                    "passed": False,
                    "status": "INSUITABLE",
                    "reason": (
                        f"Vessel air draft ({vessel_air_draft:.2f}m) exceeds certified clearance "
                        f"({limit_m:.2f}m {r.vertical_datum or ''}) at {r.scope_name} ({r.restriction_type})."
                    ),
                    "governing_air_draft_meters": limit_m,
                    "restrictions_evaluated": evaluated,
                }

        lowest_clearance = min((r["numeric_limit"] for r in evaluated if r["numeric_limit"]), default=None)
        return {
            "passed": True,
            "status": "SUITABLE",
            "reason": (
                f"Vessel air draft ({vessel_air_draft:.2f}m) clears all {len(restrictions)} overhead structures "
                f"at {port.name} (minimum vertical clearance: {lowest_clearance:.2f}m)."
            ),
            "governing_air_draft_meters": lowest_clearance,
            "restrictions_evaluated": evaluated,
        }

    def evaluate_comprehensive_port_compatibility(
        self,
        port_unlocode: str,
        vessel_draft: float,
        vessel_loa: Optional[float] = None,
        vessel_beam: Optional[float] = None,
        vessel_air_draft: Optional[float] = None,
        terminal_id: Optional[int] = None,
        berth_id: Optional[int] = None,
        available_tide_height: Optional[float] = None,
        required_ukc: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Comprehensive multi-dimensional evaluation of port compatibility.

        Combines channel, berth, and air draft evaluations into a unified assessment.
        Separates physical compatibility from cargo capacity.
        """
        chan_eval = self.evaluate_channel_compatibility(
            port_unlocode=port_unlocode,
            vessel_draft=vessel_draft,
            vessel_beam=vessel_beam,
            required_ukc=required_ukc,
            available_tide_height=available_tide_height,
        )
        berth_eval = self.evaluate_berth_compatibility(
            port_unlocode=port_unlocode,
            vessel_draft=vessel_draft,
            vessel_loa=vessel_loa,
            vessel_beam=vessel_beam,
            terminal_id=terminal_id,
            berth_id=berth_id,
            available_tide_height=available_tide_height,
        )
        air_eval = self.evaluate_air_draft_compatibility(port_unlocode, vessel_air_draft)

        statuses = [chan_eval["status"], berth_eval["status"], air_eval["status"]]

        if "INSUITABLE" in statuses:
            overall_status = "INSUITABLE"
            overall_passed = False
        elif "UNVERIFIED" in statuses:
            overall_status = "UNVERIFIED"
            overall_passed = False
        elif "CONDITIONALLY_SUITABLE" in statuses:
            overall_status = "CONDITIONALLY_SUITABLE"
            overall_passed = True
        else:
            overall_status = "SUITABLE"
            overall_passed = True

        reasons = []
        for name, ev in (("Channel", chan_eval), ("Berth", berth_eval), ("Air Draft", air_eval)):
            if ev["status"] not in ("SUITABLE", "CONDITIONALLY_SUITABLE"):
                reasons.append(f"{name}: {ev['reason']}")
            elif ev["status"] == "CONDITIONALLY_SUITABLE":
                reasons.append(f"{name} (Conditional): {ev['reason']}")

        if not reasons:
            reasons.append(f"Vessel satisfies all certified channel, berth, and overhead criteria for {port_unlocode}.")

        return {
            "port_unlocode": port_unlocode,
            "overall_status": overall_status,
            "overall_passed": overall_passed,
            "channel_compatibility": chan_eval,
            "berth_compatibility": berth_eval,
            "air_draft_compatibility": air_eval,
            "reasons": reasons,
        }

    def evaluate_canal_transit_compatibility(
        self,
        canal_code: str,
        vessel_loa: Optional[float] = None,
        vessel_beam: Optional[float] = None,
        vessel_draft: Optional[float] = None,
        as_of_time: Optional[Union[datetime, str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate vessel physical compatibility against registered canal transit corridors.

        Filters out expired/superseded restrictions (where valid_to < as_of_time) so that
        active operational rules (e.g. 370.33m Neopanamax LOA) govern current transits,
        while historical baselines (e.g. 366.00m) are preserved as dated observations.
        Missing or invalid as-of timestamps safely default to current time.
        """
        now: datetime
        if as_of_time is not None:
            if isinstance(as_of_time, str):
                parsed = parse_iso_datetime(as_of_time)
                now = parsed if parsed is not None else datetime.now(timezone.utc)
            elif isinstance(as_of_time, datetime):
                now = as_of_time
            else:
                now = datetime.now(timezone.utc)
        else:
            now = datetime.now(timezone.utc)

        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        port = self.resolve_canonical_port(canal_code, source_system="CANAL_CROSSWALK")
        if not port:
            port = self.get_port_by_unlocode(canal_code)
        if not port:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"Canal transit corridor {canal_code} not found in registry.",
                "restrictions_evaluated": [],
            }

        rests = (
            self.db.query(NavigationalRestriction)
            .filter(
                NavigationalRestriction.port_id == port.id,
                NavigationalRestriction.scope_type == RestrictionScope.CANAL_TRANSIT,
            )
            .all()
        )

        active_rests = []
        for r in rests:
            v_from = r.valid_from
            if v_from and v_from.tzinfo is None:
                v_from = v_from.replace(tzinfo=timezone.utc)
            v_to = r.valid_to
            if v_to and v_to.tzinfo is None:
                v_to = v_to.replace(tzinfo=timezone.utc)

            if v_from and v_from > now:
                continue
            if v_to and v_to < now:
                continue
            active_rests.append(r)

        if not active_rests:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "reason": f"No active canal transit restrictions found for {port.name} ({port.unlocode}).",
                "restrictions_evaluated": [],
            }

        evaluated = []
        for r in active_rests:
            info = {
                "scope_name": r.scope_name,
                "restriction_type": r.restriction_type,
                "numeric_limit": r.numeric_limit,
                "unit": r.unit,
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            }
            evaluated.append(info)

            # Evaluate LOA
            if r.restriction_type == RestrictionType.LOA_MAX and vessel_loa is not None and r.numeric_limit is not None:
                if vessel_loa > r.numeric_limit:
                    return {
                        "passed": False,
                        "status": "INSUITABLE",
                        "reason": f"Vessel LOA ({vessel_loa:.2f}m) exceeds active canal maximum ({r.numeric_limit:.2f}m) at {r.scope_name}.",
                        "governing_restriction": r.scope_name,
                        "restrictions_evaluated": evaluated,
                    }

            # Evaluate Beam
            if r.restriction_type == RestrictionType.BEAM_MAX and vessel_beam is not None and r.numeric_limit is not None:
                if vessel_beam > r.numeric_limit:
                    return {
                        "passed": False,
                        "status": "INSUITABLE",
                        "reason": f"Vessel beam ({vessel_beam:.2f}m) exceeds active canal maximum ({r.numeric_limit:.2f}m) at {r.scope_name}.",
                        "governing_restriction": r.scope_name,
                        "restrictions_evaluated": evaluated,
                    }

            # Evaluate Draft
            if r.restriction_type == RestrictionType.DRAFT_MAX and vessel_draft is not None and r.numeric_limit is not None:
                if vessel_draft > r.numeric_limit:
                    return {
                        "passed": False,
                        "status": "INSUITABLE",
                        "reason": f"Vessel draft ({vessel_draft:.2f}m) exceeds active canal maximum ({r.numeric_limit:.2f}m) at {r.scope_name}.",
                        "governing_restriction": r.scope_name,
                        "restrictions_evaluated": evaluated,
                    }

        return {
            "passed": True,
            "status": "SUITABLE",
            "reason": f"Vessel satisfies all {len(active_rests)} active canal transit restrictions for {port.name} ({port.unlocode}).",
            "restrictions_evaluated": evaluated,
        }
