"""Pelagos Chartering Intelligence, Route/Cargo Compatibility, & Decision Readiness Engine.

Implements Stage D / Milestone D10 & D10.2 vessel-to-cargo and vessel-to-route assessment
plus chartering decision readiness gating (`POST /api/v1/charter/recommend`, `ARCH-001`).
Traces to REQ-FR-CDV-002, REQ-FR-CPO-001, REQ-FR-CPO-002, REQ-FR-CPO-003,
REQ-FR-OMR-001, REQ-NFR-DIP-001, and REQ-NFR-ARG-001.

Commercial Integrity Guarantees:
- Never fabricates DWT, draft, berth limits, bunker quotes, charter rates, or fixtures.
- Explicitly distinguishes historical AIS snapshots & Vessel registry sync status from
  actual commercial open-tonnage and laycan availability.
- Emits actionable chartering recommendations ONLY when verified authentic inputs satisfy
  every required domain gate; otherwise returns explicit `insufficient_data` or `unavailable`.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from sqlalchemy.orm import Session

from pelagos.models import FreightRoute, Vessel, VesselPosition
from pelagos.optimization.calculator import calculate_voyage_freight
from pelagos.optimization.matcher import (
    evaluate_vessel_suitability,
    extract_port_draft_limit_meters,
    match_suitable_vessels,
    resolve_corridor_and_ports,
    run_operational_route_check,
)
from pelagos.optimization.schemas import (
    CalculationStatus,
    CharterRecommendRequest,
    CharterRecommendationResponse,
    ContractCostComparison,
    DataGateStatus,
    SuitabilityStatus,
    VerifiedCharterInputs,
    VesselCandidateMatch,
    VesselCompatibilityRequest,
    VesselCompatibilityResult,
    VoyageCalculationRequest,
    VoyageCorridorInfo,
)
from pelagos.routing.bunker import resolve_corridor_bunker_fuel_references
from pelagos.routing.corridors import CorridorStatus
from pelagos.routing.environmental import resolve_corridor_environmental_conditions

logger = logging.getLogger(__name__)


def _parse_iso_utc(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a UTC-aware datetime."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def _build_corridor_info(
    corridor_res: Any,
    route_record: Optional[FreightRoute],
    commodity: str,
) -> Optional[VoyageCorridorInfo]:
    """Construct standard VoyageCorridorInfo from resolved corridor and DB record."""
    if corridor_res.status != CorridorStatus.MONITORED:
        return None
    return VoyageCorridorInfo(
        route_code=corridor_res.route_code or "CORRIDOR",
        route_name=route_record.route_name if route_record else (corridor_res.route_name or "Trade Corridor"),
        origin_region=route_record.origin_region if route_record else (corridor_res.origin_region or "Origin"),
        destination_region=route_record.destination_region if route_record else (corridor_res.destination_region or "Japan"),
        commodity=route_record.cargo_type if route_record else (corridor_res.commodity or commodity),
        vessel_class=route_record.vessel_class if route_record else (corridor_res.vessel_class or "Panamax / Supramax"),
    )


def assess_vessel_for_voyage(
    db: Session,
    request: Union[VesselCompatibilityRequest, Dict[str, Any]],
    routing_engine: Optional[Any] = None,
) -> VesselCompatibilityResult:
    """Assess a specific vessel against cargo parcel, port limits, and route connectivity."""
    req: VesselCompatibilityRequest
    if isinstance(request, dict):
        try:
            req = VesselCompatibilityRequest(**request)
        except Exception as exc:
            return VesselCompatibilityResult(
                status="invalid_input",
                message=f"Invalid vessel compatibility request: {exc}",
            )
    elif isinstance(request, VesselCompatibilityRequest):
        req = request
    else:
        return VesselCompatibilityResult(
            status="invalid_input",
            message="Unsupported request object.",
        )

    port_repo, corridor_res, orig_port, dest_port, orig_coords, _ = resolve_corridor_and_ports(
        origin=req.origin,
        destination=req.destination,
        route_code=req.route_code,
        db=db,
    )
    route_record = (
        db.query(FreightRoute).filter(FreightRoute.route_code == corridor_res.route_code).first()
        if corridor_res.route_code
        else None
    )
    corridor_info = _build_corridor_info(corridor_res, route_record, req.commodity)

    load_port_coords: Optional[Tuple[float, float]] = (
        (float(orig_coords[1]), float(orig_coords[0])) if orig_coords and len(orig_coords) >= 2 else None
    )
    route_eval = run_operational_route_check(
        origin_input=req.origin or corridor_res.origin_port_id,
        destination_input=req.destination or corridor_res.destination_port_id,
        canal_policy=req.canal_policy,
        port_repo=port_repo,
        routing_engine=routing_engine,
    )

    pos_row: Optional[VesselPosition] = None
    reg_row: Optional[Vessel] = None
    hold_clean: Optional[bool] = None

    if req.vessel is not None:
        v_in = req.vessel
        obs_dt = _parse_iso_utc(v_in.observation_time) or datetime(1970, 1, 1, tzinfo=timezone.utc)
        pos_row = VesselPosition(
            mmsi=v_in.mmsi,
            imo=v_in.imo,
            vessel_name=v_in.vessel_name or f"MMSI {v_in.mmsi}",
            vessel_type=v_in.vessel_type or "Unknown",
            vessel_type_code=v_in.vessel_type_code,
            latitude=v_in.latitude,
            longitude=v_in.longitude,
            speed_knots=v_in.speed_knots,
            draft_meters=v_in.draft_meters,
            length_meters=v_in.length_meters,
            width_meters=v_in.width_meters,
            observation_time=obs_dt,
            source_name="caller_supplied_particulars",
            license_type="Unverified / Caller Supplied",
            idempotency_key=f"transient:{v_in.mmsi}",
        )
        if v_in.dwt_tonnage is not None:
            reg_row = Vessel(
                mmsi=v_in.mmsi,
                imo=v_in.imo,
                vessel_name=v_in.vessel_name or f"MMSI {v_in.mmsi}",
                vessel_type=v_in.vessel_type or "Unknown",
                vessel_type_code=v_in.vessel_type_code,
                dwt_tonnage=v_in.dwt_tonnage,
                length_meters=v_in.length_meters,
                width_meters=v_in.width_meters,
                draft_max_meters=v_in.draft_meters,
                source_name="caller_supplied_particulars",
                commercial_status="unknown",
            )
        hold_clean = v_in.hold_cleanliness_verified
    else:
        clean_mmsi = str(req.mmsi).strip()
        pos_row = (
            db.query(VesselPosition)
            .filter(VesselPosition.mmsi == clean_mmsi)
            .order_by(VesselPosition.observation_time.desc(), VesselPosition.id.desc())
            .first()
        )
        reg_row = db.query(Vessel).filter(Vessel.mmsi == clean_mmsi).first()

        if pos_row is None and reg_row is None:
            return VesselCompatibilityResult(
                status="vessel_not_found",
                corridor=corridor_info,
                message=f"No vessel or AIS observation found for MMSI '{clean_mmsi}'.",
            )

        if pos_row is None and reg_row is not None:
            # Registry entry without position fix: evaluate dims without fabricating coordinates
            pos_row = VesselPosition(
                mmsi=reg_row.mmsi,
                imo=reg_row.imo,
                vessel_name=reg_row.vessel_name,
                vessel_type=reg_row.vessel_type,
                vessel_type_code=reg_row.vessel_type_code,
                latitude=None,
                longitude=None,
                draft_meters=reg_row.draft_max_meters,
                length_meters=reg_row.length_meters,
                width_meters=reg_row.width_meters,
                observation_time=datetime(1970, 1, 1, tzinfo=timezone.utc),
                source_name=reg_row.source_name,
                license_type="Registry Record",
                idempotency_key=f"registry_only:{reg_row.mmsi}",
            )

    cand = evaluate_vessel_suitability(
        vessel=pos_row,
        target_cargo_tonnes=req.cargo_tonnes,
        load_port_coords=load_port_coords,
        registry_vessel=reg_row,
        origin_port=orig_port,
        destination_port=dest_port,
        route_eval=route_eval,
        commodity=req.commodity,
        hold_cleanliness_verified=hold_clean,
    )

    ais_status = {
        "observation_time": cand.observation_time,
        "observation_age_seconds": cand.observation_age_seconds,
        "is_stale": cand.is_stale,
        "data_mode": cand.data_mode,
        "latitude": cand.latitude,
        "longitude": cand.longitude,
        "distance_qualification": cand.distance_qualification,
    }
    commercial_status = {
        "commercial_availability": cand.commercial_availability,
        "commercial_availability_note": cand.commercial_availability_note,
        "laycan_feasibility": cand.laycan_feasibility,
        "registry_commercial_status": cand.registry_commercial_status,
        "registry_status_meaning": (
            "Vessel.commercial_status ('active') reflects historical AIS registry synchronization only and "
            "MUST NOT be interpreted as open-tonnage charter availability."
        ),
        "verified_cargo_capacity_dwt": cand.verified_cargo_capacity_dwt,
        "cargo_capacity_status": cand.cargo_capacity_status,
    }

    return VesselCompatibilityResult(
        status="assessed",
        overall_suitability=cand.suitability_status,
        corridor=corridor_info,
        candidate=cand,
        ais_observation_status=ais_status,
        commercial_availability_status=commercial_status,
        message=cand.suitability_reason,
    )


def _evaluate_verified_charter_fixtures(
    req: CharterRecommendRequest,
    verified: VerifiedCharterInputs,
    orig_port: Any,
    dest_port: Any,
    orig_coords: Optional[List[float]],
    route_eval: Optional[Dict[str, Any]],
    spot_rate_usd_per_mt: float,
    laden_distance_nm: float,
) -> Tuple[List[VesselCandidateMatch], List[ContractCostComparison], Optional[str], Optional[str], str]:
    """Filter and rank caller-provided verified fixtures against CPO-001/002/003 & CDV-002 gates."""
    explicit_limits = {
        "origin_max_draft_meters": verified.origin_max_draft_meters,
        "destination_max_draft_meters": verified.destination_max_draft_meters,
        "origin_max_loa_meters": verified.origin_max_loa_meters,
        "destination_max_loa_meters": verified.destination_max_loa_meters,
        "origin_max_beam_meters": verified.origin_max_beam_meters,
        "destination_max_beam_meters": verified.destination_max_beam_meters,
    }
    load_lat_lon = (float(orig_coords[1]), float(orig_coords[0])) if orig_coords and len(orig_coords) >= 2 else None
    laycan_deadline = (
        datetime.combine(req.laycan_end, time(23, 59, 59), tzinfo=timezone.utc)
        if req.laycan_end
        else None
    )

    compliant_matches: List[Tuple[float, VesselCandidateMatch, Any]] = []
    for fix in verified.vessel_fixtures:
        if fix.commercial_status.strip().lower() != "open" or not fix.hold_cleanliness_verified:
            continue

        obs_dt = _parse_iso_utc(fix.observation_time) or datetime.now(timezone.utc)
        pos = VesselPosition(
            mmsi=fix.mmsi,
            vessel_name=fix.vessel_name,
            vessel_type=fix.vessel_type,
            vessel_type_code=fix.vessel_type_code,
            latitude=fix.current_latitude,
            longitude=fix.current_longitude,
            speed_knots=fix.service_speed_knots,
            draft_meters=fix.draft_meters,
            length_meters=fix.length_meters,
            width_meters=fix.width_meters,
            observation_time=obs_dt,
            source_name=verified.source_provenance,
            license_type="Verified Commercial Fixture",
            idempotency_key=f"verified:{fix.mmsi}",
        )
        reg = Vessel(
            mmsi=fix.mmsi,
            vessel_name=fix.vessel_name,
            vessel_type=fix.vessel_type,
            vessel_type_code=fix.vessel_type_code,
            dwt_tonnage=fix.dwt_tonnage,
            length_meters=fix.length_meters,
            width_meters=fix.width_meters,
            draft_max_meters=fix.draft_meters,
            source_name=verified.source_provenance,
            commercial_status=fix.commercial_status,
        )
        cand = evaluate_vessel_suitability(
            vessel=pos,
            target_cargo_tonnes=req.cargo_tonnes,
            load_port_coords=load_lat_lon,
            now_utc=obs_dt,
            registry_vessel=reg,
            origin_port=orig_port,
            destination_port=dest_port,
            route_eval=route_eval,
            commodity=req.commodity,
            hold_cleanliness_verified=fix.hold_cleanliness_verified,
            explicit_limits=explicit_limits,
        )

        # Strict physical, cargo, port, and canal compatibility pruning (REQ-FR-CPO-003)
        if cand.suitability_status != SuitabilityStatus.POTENTIALLY_SUITABLE:
            continue
        if cand.route_compatibility and cand.route_compatibility.status == "incompatible":
            continue
        if cand.cargo_compatibility and cand.cargo_compatibility.status == "incompatible":
            continue

        # Laycan arrival cutoff pruning (REQ-FR-CDV-002)
        ballast_nm = cand.distance_to_load_port_nm or 0.0
        ballast_days = (ballast_nm / fix.service_speed_knots) / 24.0
        eta_dt = obs_dt + timedelta(days=ballast_days)
        if laycan_deadline is not None and eta_dt > laycan_deadline:
            continue

        cand.commercial_availability = "VERIFIED_OPEN"
        cand.commercial_availability_note = (
            f"Verified open tonnage via {verified.source_provenance}; hold cleanliness verified."
        )
        cand.laycan_feasibility = "FEASIBLE" if laycan_deadline else "UNSPECIFIED_LAYCAN_WINDOW"
        cand.laycan_feasibility_note = (
            f"Estimated ballast arrival {eta_dt.isoformat()} clears laycan deadline {laycan_deadline.isoformat()}."
            if laycan_deadline
            else "No cancelling date filter specified; ballast transit evaluated."
        )
        compliant_matches.append((ballast_days, cand, fix))

    if not compliant_matches:
        return [], [], None, None, "No verified open candidate satisfied DWT, hold cleanliness, port/canal limits, and laycan requirements."

    compliant_matches.sort(key=lambda item: (item[0], item[1].mmsi))
    _, best_cand, best_fix = compliant_matches[0]
    ballast_days = (best_cand.distance_to_load_port_nm or 0.0) / best_fix.service_speed_knots / 24.0
    laden_days = (laden_distance_nm / best_fix.service_speed_knots) / 24.0
    total_voyage_days = ballast_days + laden_days + verified.voyage_loading_discharge_days
    bunker_cost = total_voyage_days * best_fix.daily_fuel_consumption_mt * verified.bunker_price_usd_per_mt
    extra_port_canal = verified.port_disbursements_usd + verified.canal_tolls_usd

    comparisons: List[ContractCostComparison] = []
    spot_unit = best_fix.spot_charter_rate_usd_per_mt or spot_rate_usd_per_mt
    spot_total = req.cargo_tonnes * spot_unit + bunker_cost + extra_port_canal
    comparisons.append(
        ContractCostComparison(
            contract_structure="Spot Single Voyage",
            total_cost_usd=round(spot_total, 2),
            unit_cost_usd_per_mt=round(spot_total / req.cargo_tonnes, 2),
            voyages_evaluated=1,
            assumptions_used={
                "vessel_mmsi": best_fix.mmsi,
                "spot_rate_usd_per_mt": spot_unit,
                "total_voyage_days": round(total_voyage_days, 2),
                "bunker_price_usd_per_mt": verified.bunker_price_usd_per_mt,
                "bunker_cost_usd": round(bunker_cost, 2),
                "port_disbursements_usd": verified.port_disbursements_usd,
                "canal_tolls_usd": verified.canal_tolls_usd,
            },
        )
    )

    if best_fix.coa_consecutive_voyage_rate_usd_per_mt is not None:
        n_voy = verified.consecutive_voyages_count
        total_cargo = req.cargo_tonnes * n_voy
        coa_total = (
            total_cargo * best_fix.coa_consecutive_voyage_rate_usd_per_mt
            + n_voy * (bunker_cost + extra_port_canal)
        )
        comparisons.append(
            ContractCostComparison(
                contract_structure="Consecutive Voyage Charter (COA)",
                total_cost_usd=round(coa_total, 2),
                unit_cost_usd_per_mt=round(coa_total / total_cargo, 2),
                voyages_evaluated=n_voy,
                assumptions_used={
                    "coa_rate_usd_per_mt": best_fix.coa_consecutive_voyage_rate_usd_per_mt,
                    "consecutive_voyages": n_voy,
                },
            )
        )

    if best_fix.time_charter_daily_hire_usd is not None:
        tc_total = (
            total_voyage_days * best_fix.time_charter_daily_hire_usd + bunker_cost + extra_port_canal
        )
        comparisons.append(
            ContractCostComparison(
                contract_structure="Time Charter (TC)",
                total_cost_usd=round(tc_total, 2),
                unit_cost_usd_per_mt=round(tc_total / req.cargo_tonnes, 2),
                voyages_evaluated=1,
                assumptions_used={
                    "daily_hire_usd": best_fix.time_charter_daily_hire_usd,
                    "total_voyage_days": round(total_voyage_days, 2),
                },
            )
        )

    comparisons.sort(key=lambda c: c.unit_cost_usd_per_mt)
    best_contract = comparisons[0].contract_structure

    fut_rate = verified.verified_future_rate_usd_per_mt
    if fut_rate is not None and fut_rate < spot_unit * 0.98 and best_contract == "Spot Single Voyage":
        action = "Wait"
    elif best_contract in {"Consecutive Voyage Charter (COA)", "Time Charter (TC)"} or (
        fut_rate is not None and fut_rate > spot_unit * 1.02
    ):
        action = "Fix Now"
    else:
        action = "Fix Now"

    return (
        [item[1] for item in compliant_matches[: req.max_vessels]],
        comparisons,
        action,
        best_contract,
        f"Evaluated {len(compliant_matches)} verified open vessel(s); lowest verified unit cost is {best_contract} ({comparisons[0].unit_cost_usd_per_mt:.2f} USD/MT).",
    )


def recommend_charter_strategy(
    db: Session,
    request: Union[CharterRecommendRequest, Dict[str, Any]],
    routing_engine: Optional[Any] = None,
) -> CharterRecommendationResponse:
    """Evaluate readiness gates and issue chartering recommendations strictly from authentic data."""
    req: CharterRecommendRequest
    if isinstance(request, dict):
        try:
            req = CharterRecommendRequest(**request)
        except Exception as exc:
            return CharterRecommendationResponse(
                status="invalid_input",
                message=f"Invalid charter recommendation request: {exc}",
            )
    elif isinstance(request, CharterRecommendRequest):
        req = request
    else:
        return CharterRecommendationResponse(
            status="invalid_input",
            message="Unsupported request object.",
        )

    from pelagos.forecasting.service import forecast_next_route_rate

    port_repo, corridor_res, orig_port, dest_port, orig_coords, _ = resolve_corridor_and_ports(
        origin=req.origin,
        destination=req.destination,
        route_code=req.route_code,
        db=db,
    )
    is_monitored = corridor_res.status == CorridorStatus.MONITORED
    route_code = corridor_res.route_code
    if not route_code and req.route_code:
        candidate_code = req.route_code.strip().upper()
        if candidate_code in {"PNW-JPN-BULK", "USG-JPN-BULK"}:
            route_code = candidate_code
            is_monitored = True

    route_record = (
        db.query(FreightRoute).filter(FreightRoute.route_code == route_code).first()
        if route_code
        else None
    )
    corridor_info = _build_corridor_info(corridor_res, route_record, req.commodity)

    route_origin_in = req.origin or corridor_res.origin_port_id or (orig_port.port_id if orig_port else None)
    route_dest_in = req.destination or corridor_res.destination_port_id or (dest_port.port_id if dest_port else None)
    route_eval = run_operational_route_check(
        origin_input=route_origin_in,
        destination_input=route_dest_in,
        canal_policy=req.canal_policy,
        port_repo=port_repo,
        routing_engine=routing_engine,
    )

    # Gate A: Operational route connectivity
    if route_eval is not None and route_eval.get("route_navigable") is False:
        return CharterRecommendationResponse(
            status="unavailable",
            recommendation_issued=False,
            recommended_action=None,
            recommended_contract_structure=None,
            corridor=corridor_info,
            cargo_tonnes=req.cargo_tonnes,
            commodity=req.commodity,
            data_readiness_gates={
                "operational_route_and_ports": DataGateStatus(
                    domain="operational_route_and_ports",
                    status="unavailable",
                    details=route_eval.get("error_message") or "Disconnected or unnavigable sea route.",
                    missing_items=["connected_maritime_sea_route"],
                )
            },
            missing_decision_inputs=["connected_maritime_sea_route"],
            message=route_eval.get("error_message") or "Operational sea route is unnavigable; charter recommendation unavailable.",
        )

    # Gate B: Monitored commercial corridor market rate data
    if not is_monitored or not route_code:
        return CharterRecommendationResponse(
            status="unavailable",
            recommendation_issued=False,
            recommended_action=None,
            recommended_contract_structure=None,
            corridor=None,
            cargo_tonnes=req.cargo_tonnes,
            commodity=req.commodity,
            data_readiness_gates={
                "corridor_and_freight_market": DataGateStatus(
                    domain="corridor_and_freight_market",
                    status="unavailable",
                    details=corridor_res.reason or "Route is outside monitored bulk freight corridors.",
                    missing_items=["monitored_freight_rate_history", "corridor_benchmark_observations"],
                )
            },
            missing_decision_inputs=["monitored_freight_rate_history"],
            message=corridor_res.reason or "Freight market intelligence is unavailable for unmonitored trade corridors.",
        )

    # Collect genuine supporting analytics from completed Stages B, C, and D10
    voyage_calc = calculate_voyage_freight(
        db=db,
        request=VoyageCalculationRequest(
            cargo_tonnes=req.cargo_tonnes,
            origin=req.origin,
            destination=req.destination,
            route_code=route_code,
            canal_policy=req.canal_policy,
        ),
        routing_engine=routing_engine,
    )

    forecasts_map: Dict[str, Any] = {}
    for h in (1, 3, 6):
        try:
            fc = forecast_next_route_rate(db=db, route_code=route_code, model_type="persistence", horizon_months=h)
            forecasts_map[f"T+{h}"] = {
                "model_type": fc.get("model_type"),
                "origin_date": str(fc.get("origin_date")),
                "target_date": str(fc.get("target_date")),
                "predicted_value": fc.get("predicted_value"),
                "unit": fc.get("unit_of_measure", "USD/MT"),
            }
        except Exception as exc:
            logger.debug("Forecast lookup skipped for %s T+%s: %s", route_code, h, exc)

    env_summary = None
    try:
        o_tuple = (float(orig_coords[0]), float(orig_coords[1])) if orig_coords and len(orig_coords) >= 2 else None
        env_summary = resolve_corridor_environmental_conditions(db=db, corridor_code=route_code, origin_coords=o_tuple)
    except Exception:
        env_summary = None

    bunker_summary = resolve_corridor_bunker_fuel_references(corridor_code=route_code, db=db)

    suitability_res = match_suitable_vessels(
        db=db,
        request={
            "cargo_tonnes": req.cargo_tonnes,
            "commodity": req.commodity,
            "origin": req.origin or (orig_port.port_id if orig_port else None),
            "destination": req.destination or (dest_port.port_id if dest_port else None),
            "route_code": route_code,
            "canal_policy": req.canal_policy,
            "max_vessels": req.max_vessels,
        },
        routing_engine=routing_engine,
    )

    # Evaluate individual decision-readiness gates honestly
    rate_available = (
        voyage_calc.status == CalculationStatus.CALCULATED and voyage_calc.applicable_rate is not None
    )
    market_gate = DataGateStatus(
        domain="corridor_and_freight_market",
        status="available" if rate_available else "unavailable",
        details=(
            f"Verified benchmark rate ${voyage_calc.applicable_rate.rate_value:.2f}/MT "
            f"({voyage_calc.applicable_rate.observation_date}); gross freight ${voyage_calc.gross_freight_cost:,.2f}."
            if rate_available and voyage_calc.applicable_rate and voyage_calc.gross_freight_cost is not None
            else (voyage_calc.message or "Verified spot freight rate missing.")
        ),
        missing_items=[] if rate_available else ["verified_spot_freight_rate"],
    )

    pred_values = [
        float(v["predicted_value"])
        for v in forecasts_map.values()
        if v.get("predicted_value") is not None
    ]
    forecast_spread = (max(pred_values) - min(pred_values)) if pred_values else 0.0
    has_verified_future_signal = (
        req.verified_commercial_inputs is not None
        and req.verified_commercial_inputs.verified_future_rate_usd_per_mt is not None
    )
    forecast_gate = DataGateStatus(
        domain="directional_forecast_edge",
        status="available" if (forecast_spread > 1e-6 or has_verified_future_signal) else "insufficient_data",
        details=(
            "Verified non-flat directional forward rate signal supplied."
            if has_verified_future_signal
            else "Active production forecasting baseline is persistence (T+1, T+3, T+6 all equal latest observed rate; zero directional edge for Wait vs. Fix Now timing)."
        ),
        missing_items=(
            []
            if (forecast_spread > 1e-6 or has_verified_future_signal)
            else ["calibrated_directional_forward_rate_edge"]
        ),
    )

    orig_draft_limit, _ = extract_port_draft_limit_meters(orig_port)
    dest_draft_limit, _ = extract_port_draft_limit_meters(dest_port)
    canals = route_eval.get("canals_transited", []) if route_eval else []
    vc = req.verified_commercial_inputs
    eff_orig_draft = (vc.origin_max_draft_meters if vc and vc.origin_max_draft_meters is not None else orig_draft_limit)
    eff_dest_draft = (vc.destination_max_draft_meters if vc and vc.destination_max_draft_meters is not None else dest_draft_limit)
    port_missing: List[str] = []
    if orig_port and eff_orig_draft is None:
        port_missing.append(f"{orig_port.port_id}_verified_berth_draft_and_loa_limits")
    elif orig_port and (vc is None or vc.origin_max_loa_meters is None or vc.origin_max_beam_meters is None):
        port_missing.append(f"{orig_port.port_id}_verified_berth_loa_and_beam_limits")
    if dest_port and eff_dest_draft is None:
        port_missing.append(f"{dest_port.port_id}_verified_berth_draft_and_loa_limits")
    elif dest_port and (vc is None or vc.destination_max_loa_meters is None or vc.destination_max_beam_meters is None):
        port_missing.append(f"{dest_port.port_id}_verified_berth_loa_and_beam_limits")
    if vc is None:
        for canal in canals:
            canal_slug = "panama" if "panama" in canal.lower() else ("suez" if "suez" in canal.lower() else canal.lower().replace(" ", "_"))
            port_missing.append(f"{canal_slug}_canal_live_authority_draft_and_slot_rules")

    route_gate = DataGateStatus(
        domain="operational_route_and_ports",
        status="available" if not port_missing else ("partial" if route_eval else "insufficient_data"),
        details=(
            f"Navigable sea route evaluated ({voyage_calc.distance.distance_nm:.1f} NM"
            f"{'; transits ' + ', '.join(canals) if canals else ''}); port/canal limits audited."
            if voyage_calc.distance
            else "Corridor benchmark distance referenced; berth & canal limits incomplete."
        ),
        missing_items=port_missing,
    )

    dwt_verified_candidates = [
        c for c in suitability_res.candidates if c.verified_cargo_capacity_dwt is not None
    ]
    if req.verified_commercial_inputs and req.verified_commercial_inputs.vessel_fixtures:
        vessel_phys_gate = DataGateStatus(
            domain="vessel_physical_particulars",
            status="available",
            details="Verified physical particulars supplied via commercial fixture bundle.",
            missing_items=[],
        )
    else:
        missing_phys = []
        if not dwt_verified_candidates:
            missing_phys.append("verified_vessel_dwt_tonnage")
        missing_phys.extend(["verified_hold_cleanliness", "verified_grain_cubic_capacity"])
        if any(c.draft_meters is None for c in suitability_res.candidates if c.vessel_type_code and 70 <= c.vessel_type_code <= 79):
            missing_phys.append("complete_cargo_vessel_draft_particulars")
        vessel_phys_gate = DataGateStatus(
            domain="vessel_physical_particulars",
            status="insufficient_data",
            details=(
                f"Evaluated {suitability_res.total_vessels_evaluated} genuine AIS vessel record(s); "
                f"0 verified DWT values, {suitability_res.potentially_suitable_count} dimensionally screened only."
            ),
            missing_items=missing_phys,
        )

    if req.verified_commercial_inputs and req.verified_commercial_inputs.vessel_fixtures:
        comm_gate = DataGateStatus(
            domain="commercial_open_tonnage_and_laycan",
            status="available",
            details=f"Verified open-tonnage fixtures supplied from {req.verified_commercial_inputs.source_provenance}.",
            missing_items=[],
        )
    else:
        comm_gate = DataGateStatus(
            domain="commercial_open_tonnage_and_laycan",
            status="unavailable",
            details=(
                "Live AIS and commercial open-tonnage broker lists are unconnected; current DB AIS snapshot is "
                f"{suitability_res.data_mode} (Jan 1, 2024 sample). Laycan reachability and vessel employment are unconfirmed."
            ),
            missing_items=["commercial_open_tonnage_list", "live_vessel_ais_positions", "laycan_itinerary_confirmation"],
        )

    if req.verified_commercial_inputs is not None:
        bunker_gate = DataGateStatus(
            domain="bunker_and_voyage_economics",
            status="available",
            details=(
                f"Verified bunker quote (${req.verified_commercial_inputs.bunker_price_usd_per_mt:.2f}/MT "
                f"{req.verified_commercial_inputs.bunker_fuel_grade}) and voyage parameters supplied."
            ),
            missing_items=[],
        )
    else:
        bunker_gate = DataGateStatus(
            domain="bunker_and_voyage_economics",
            status="unavailable",
            details=(
                f"Corridor bunker hub status is '{bunker_summary.status}' "
                f"({bunker_summary.hubs_with_prices_count}/{len(bunker_summary.bunkering_hubs_in_scope)} hubs quoted); "
                "fuel-consumption curves, port disbursements, demurrage, and COA/TC rates are unavailable."
            ),
            missing_items=[
                "corridor_hub_bunker_price_quotes",
                "vessel_fuel_consumption_curve",
                "port_disbursements_and_canal_tolls",
                "coa_and_time_charter_quotes",
            ],
        )

    gates = {
        "corridor_and_freight_market": market_gate,
        "directional_forecast_edge": forecast_gate,
        "operational_route_and_ports": route_gate,
        "vessel_physical_particulars": vessel_phys_gate,
        "commercial_open_tonnage_and_laycan": comm_gate,
        "bunker_and_voyage_economics": bunker_gate,
    }

    missing_inputs: List[str] = []
    seen = set()
    for gate in gates.values():
        for item in gate.missing_items:
            if item not in seen:
                seen.add(item)
                missing_inputs.append(item)

    supporting = {
        "gross_freight_cost_usd": voyage_calc.gross_freight_cost,
        "applicable_rate": voyage_calc.applicable_rate.model_dump() if voyage_calc.applicable_rate else None,
        "operational_route": route_eval,
        "persistence_forecasts": forecasts_map,
        "environmental_coverage": env_summary.coverage_status if env_summary else "unavailable",
        "bunker_fuel_status": bunker_summary.status,
        "bunker_hubs_quoted": f"{bunker_summary.hubs_with_prices_count}/{len(bunker_summary.bunkering_hubs_in_scope)}",
        "vessels_evaluated_count": suitability_res.total_vessels_evaluated,
        "dimensionally_screened_candidates_count": suitability_res.potentially_suitable_count,
        "data_mode": suitability_res.data_mode,
    }

    # Positive Charter Recommendation Path ONLY when verified commercial inputs satisfy all blockers
    if req.verified_commercial_inputs is not None and rate_available and voyage_calc.applicable_rate:
        laden_nm = (
            float(route_eval["route_distance_nm"])
            if route_eval and route_eval.get("route_distance_nm")
            else (voyage_calc.distance.distance_nm if voyage_calc.distance else 4520.0)
        )
        ranked_cands, comparisons, action, contract_struct, eval_msg = _evaluate_verified_charter_fixtures(
            req=req,
            verified=req.verified_commercial_inputs,
            orig_port=orig_port,
            dest_port=dest_port,
            orig_coords=orig_coords,
            route_eval=route_eval,
            spot_rate_usd_per_mt=voyage_calc.applicable_rate.rate_value,
            laden_distance_nm=laden_nm,
        )
        if not ranked_cands:
            return CharterRecommendationResponse(
                status="unavailable",
                recommendation_issued=False,
                recommended_action=None,
                recommended_contract_structure=None,
                corridor=corridor_info,
                cargo_tonnes=req.cargo_tonnes,
                commodity=req.commodity,
                data_readiness_gates=gates,
                missing_decision_inputs=missing_inputs,
                verified_supporting_analytics=supporting,
                ranked_vessel_candidates=[],
                message=eval_msg,
            )

        if missing_inputs:
            return CharterRecommendationResponse(
                status="insufficient_data",
                recommendation_issued=False,
                recommended_action=None,
                recommended_contract_structure=None,
                corridor=corridor_info,
                cargo_tonnes=req.cargo_tonnes,
                commodity=req.commodity,
                data_readiness_gates=gates,
                missing_decision_inputs=missing_inputs,
                verified_supporting_analytics=supporting,
                contract_comparisons=[],
                ranked_vessel_candidates=ranked_cands,
                message=(
                    f"Actionable chartering recommendation is withheld ('insufficient_data') because verified "
                    f"inputs remain incomplete: {', '.join(missing_inputs)}."
                ),
            )

        return CharterRecommendationResponse(
            status="recommended",
            recommendation_issued=True,
            recommended_action=action,
            recommended_contract_structure=contract_struct,
            corridor=corridor_info,
            cargo_tonnes=req.cargo_tonnes,
            commodity=req.commodity,
            data_readiness_gates=gates,
            missing_decision_inputs=missing_inputs,
            verified_supporting_analytics=supporting,
            contract_comparisons=comparisons,
            ranked_vessel_candidates=ranked_cands,
            ais_vs_commercial_summary=(
                f"Recommendation derived strictly from verified commercial fixture bundle ({req.verified_commercial_inputs.source_provenance}) "
                f"and verified DB freight benchmark ({voyage_calc.applicable_rate.observation_date})."
            ),
            message=eval_msg,
        )

    # Strict Fail-Closed Gate: Refuse to invent charter timing or contract fixtures without data
    return CharterRecommendationResponse(
        status="insufficient_data",
        recommendation_issued=False,
        recommended_action=None,
        recommended_contract_structure=None,
        corridor=corridor_info,
        cargo_tonnes=req.cargo_tonnes,
        commodity=req.commodity,
        data_readiness_gates=gates,
        missing_decision_inputs=missing_inputs,
        verified_supporting_analytics=supporting,
        contract_comparisons=[],
        ranked_vessel_candidates=suitability_res.candidates[: req.max_vessels],
        message=(
            f"Actionable chartering recommendation is withheld ('insufficient_data') for corridor '{route_code}'. "
            "Monitored freight benchmark, persistence forecasts, and historical AIS dimensional screening are available, "
            "but commercial open-tonnage fixtures, verified DWT, corridor bunker quotes, voyage economics, and directional forecast edge are missing."
        ),
    )
