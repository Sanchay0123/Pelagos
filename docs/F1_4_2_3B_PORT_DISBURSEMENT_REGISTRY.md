---
title: "Pelagos Stage F1.4.2.3b — Global Port Disbursement Intelligence Foundation"
document_id: "DOC-F1423B-001"
type: "architecture_spec"
status: "approved"
version: "1.0.0"
created: "2026-11-15"
updated: "2026-11-15"
author: "DeepMind Antigravity Engineering Agent"
target_stage: "Stage F1.4.2.3b (Port Disbursement Foundation)"
baseline_git_commit: "2929e61c5ddb8061ebfa9ad6c905b38b69335ef6"
tags:
  - port_disbursements
  - port_tariffs
  - maritime_disbursements
  - data_governance
  - singpaore_mpa
  - rotterdam_seaport_dues
  - houston_port_tariffs
---

# Pelagos Stage F1.4.2.3b — Global Port Disbursement Intelligence Foundation

## 1. Executive Summary & Scope Boundaries

Stage **F1.4.2.3b** implements the authoritative port disbursement architecture, normalized expense taxonomy, relational data models, and deterministic calculation foundation for global port calls in Pelagos.

### Foundational Invariants & Scope Boundaries
- **Authorized Foundation:**
  - A normalized, 12-category port disbursement taxonomy distinguishing mandatory statutory dues, conditional operational fees, usage-based dockage/wharfage, and optional commercial services.
  - Relational database persistence model (`PortTariffRecord` in `backend/pelagos/models.py`) with automatic schema migration in `backend/pelagos/database.py`.
  - Authoritative source inventory catalog (`backend/pelagos/data/f1_4_2_3b_port_tariff_source_inventory.json`) and pilot tariff dataset (`backend/pelagos/data/f1_4_2_3b_pilot_port_tariffs.json`).
  - Deterministic calculation engine (`backend/pelagos/registry/port_disbursement_registry.py`) encoding verified statutory port tariffs for three global pilot maritime hubs:
    1. **Singapore (`SGSIN`):** Maritime and Port Authority of Singapore (MPA) statutory port dues, bunkering concessions, anchorage rules, and PSA Marine pilotage brackets.
    2. **Rotterdam (`NLRTM`):** Port of Rotterdam Authority seaport dues (composite Gross Tonnage + cargo handled), compulsory SAB ship waste reception dues, and Loodswezen regulated pilotage base rates.
    3. **Houston (`USHOU`):** Port of Houston Authority (PHA) Tariff No. 8 harbor dues, dockage per LOA foot, ISPS security surcharge, and Houston Pilots draft/unit charges.
  - Integration with multi-currency conversion architecture (`CurrencyConversionService`) supporting official European Central Bank (ECB) and Monetary Authority of Singapore (MAS) statutory reference rates.
  - Comprehensive automated test suite (`tests/test_port_disbursements.py`) with 11 test cases.
- **Strictly Prohibited & Blocked:**
  - Integration of port disbursement calculations into commercial voyage net freight, Time Charter Equivalent (TCE) optimization, or charter recommendation bidding engines.
  - Extrapolation or synthetic estimation of port disbursements for uncataloged ports using regional synthetic averages or neighboring port proxies.
  - Generation of synthetic vessel particulars or proxy statutory tonnages.
  - Claiming global operational port coverage based on pilot hubs.

---

## 2. Normalized Port Disbursement Taxonomy

Port disbursement accounts (PDAs) represent complex multi-stakeholder settlements governed by diverse municipal, statutory, and private operator rate cards. Pelagos establishes a canonical 12-category taxonomy with explicit operational applicability semantics:

```
+---------------------------------------------------------------------------------------------------+
|                            PELAGOS PORT DISBURSEMENT TAXONOMY                                     |
+---------------------------------------------------------------------------------------------------+
| 1. PORT_DUES (Mandatory):                                                                         |
|    - Statutory dues assessed on vessel presence, typically billed per Gross Tonnage (GT) and stay.|
|    - Examples: MPA Port Dues (Singapore), Rotterdam Seaport Dues GT component, Houston Harbor Fee.|
| 2. PILOTAGE (Mandatory / Conditional):                                                            |
|    - Compulsory maritime pilot navigation assistance for harbor entry, departure, and shifting.  |
|    - Billed per LOA, deepest draft, gross tonnage, or pilotage unit.                              |
| 3. TOWAGE (Conditional):                                                                          |
|    - Harbor assist tugs for berthing, unberthing, and escorting. Billed per tug per hour or       |
|      maneuver based on bollard pull capacity.                                                     |
| 4. BERTH_OCCUPANCY_DOCKAGE (Usage-Based):                                                         |
|    - Quay infrastructure utilization assessed per foot or meter of LOA per 24-hr period or per GT.|
| 5. WHARFAGE (Usage-Based):                                                                        |
|    - Dues assessed on cargo loaded, discharged, or transferred over port authority wharves.       |
| 6. MOORING_UNMOORING (Mandatory):                                                                 |
|    - Boatmen and line-handling crew fees for securing and releasing vessel lines at berth.        |
| 7. ANCHORAGE (Conditional):                                                                       |
|    - Dues for remaining at designated roadstead anchorages, often subject to a free grace period. |
| 8. WASTE_RECEPTION (Mandatory):                                                                   |
|    - Statutory contribution for ship-generated waste collection under MARPOL Annex I, IV, and V.  |
| 9. FRESHWATER_SERVICE (Optional):                                                                 |
|    - Commercial supply of potable fresh water via shore hydrant or water barge per m³.            |
| 10. SECURITY_ISPS (Mandatory):                                                                    |
|    - Statutory surcharge for International Ship and Port Facility Security (ISPS) compliance.     |
| 11. AGENCY_FEE (Mandatory):                                                                       |
|    - Remuneration for port agency attendances under FONASBA / BIMCO standard agency agreements.   |
| 12. STATUTORY_LIGHT_DUES (Mandatory):                                                             |
|    - National lighthouse and navigational aids levies (e.g. UK Trinity House, Indian light dues). |
+---------------------------------------------------------------------------------------------------+
```

### Applicability Classification
1. **`MANDATORY`:** Non-discretionary statutory charge levied on all calling commercial vessels regardless of cargo operations (e.g. port dues, ISPS security fee, standard waste reception).
2. **`CONDITIONAL`:** Triggered by specific physical attributes, navigational events, or call purposes (e.g. pilotage based on draft threshold, anchorage dues triggered only if stay exceeds grace period, or bunkering concession rates).
3. **`USAGE_BASED`:** Proportional to elapsed operational time, cargo volume handled, or linear berth utilization (e.g. dockage per LOA foot per day, wharfage per metric ton cargo).
4. **`OPTIONAL`:** Discretionary commercial services requested by master or charterer (e.g. provisions barge, potable freshwater delivery, additional tugs above harbor regulations).

---

## 3. Pilot Port Authority Discovery & Source Attribution

Pelagos restricts initial deterministic modeling to three authoritative global pilot hubs where official, inspectable public statutory tariffs are established:

### 3.1 Singapore (`SGSIN`) — Southeast Asian Global Hub
- **Lead Authority:** Maritime and Port Authority of Singapore (MPA).
- **Statutory Source:** MPA Port Marine Circular No. 11/2022 (*Revision of Port Dues Tariff*), Singapore Port Regulations.
- **Source ID:** `SRC-DISB-SGP-MPA-001` (Authoritative Primary Legal, `DIRECTLY_VERIFIED`).
- **Secondary Operator:** PSA Marine Pte Ltd (`SRC-DISB-SGP-PSA-001`, `PARTIALLY_VERIFIED`).
- **Statutory Billing Rules:**
  - Standard Dry Bulk Port Dues: **SGD 4.50** per 100 GT (or part thereof) per 24 hours (or part thereof).
  - Bunkering Concession: **SGD 2.25** per 100 GT for pure bunker calls $\le 24$ hours (50% concession).
  - Extended Anchorage: Free for initial 24 hours; **SGD 1.50** per 100 GT per 24 hours thereafter.
  - Pilotage Bracket: **SGD 420.00** per hour for vessels $> 50,000\text{ GT}$ (min 2 hours).

### 3.2 Rotterdam (`NLRTM`) — Northwest European Gateway
- **Lead Authority:** Port of Rotterdam Authority (*Havenbedrijf Rotterdam N.V.*).
- **Statutory Source:** *General Terms and Conditions including Seaport Dues 2024*.
- **Source ID:** `SRC-DISB-NLD-RTM-001` (Authoritative Primary Legal, `DIRECTLY_VERIFIED`).
- **Regulated Pilotage Authority:** *Nederlands Loodswezen B.V.* / ACM Decree (`SRC-DISB-NLD-LOODS-001`, `DIRECTLY_VERIFIED`).
- **Statutory Billing Rules:**
  - Composite Seaport Dues:
    $$\text{Seaport Dues}_{\text{EUR}} = (\text{GT} \times 0.320) + (\text{Cargo Tonnes} \times 0.450)$$
  - Mandatory SAB Ship Waste Fee: **EUR 450.00** fixed contribution per seagoing vessel call.
  - Regulated Pilotage Base Charge (S-Component): **EUR 380.00** fixed starting charge.

### 3.3 Houston (`USHOU`) — US Gulf Transshipment Hub
- **Lead Authority:** Port of Houston Authority (PHA) & Houston Pilots Association.
- **Statutory Source:** PHA Tariff No. 8 (FMC-filed) & Houston Pilots Official Tariff Schedule 2024.
- **Source IDs:** `SRC-DISB-USA-HOU-001` & `SRC-DISB-USA-HOUPILOT-001` (Authoritative Primary Legal, `DIRECTLY_VERIFIED`).
- **Statutory Billing Rules:**
  - PHA Harbor Fee: Fixed **$550.00 USD** per arrival call for vessels $\text{LOA} > 500\text{ ft}$.
  - PHA Dockage: **$3.85 USD** per linear foot of LOA per 24-hr period or fraction thereof.
  - Port Security Surcharge: **10.0%** of total dockage accrued.
  - Houston Pilots Draft Charge: **$85.50 USD** per foot of deepest draft (min 15 ft).
  - Houston Pilots Unit Charge: **$12.50 USD** per Pilot Unit ($\text{Unit} = \frac{\text{LOA(ft)} \times \text{Beam(ft)}}{100}$).

---

## 4. Relational Data Model & Persistence Architecture

The port disbursement data model is integrated into `backend/pelagos/models.py` as `PortTariffRecord` and managed via schema compatibility checks in `backend/pelagos/database.py`:

```python
class PortTariffRecord(Base):
    __tablename__ = "port_tariff_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    un_locode = Column(String(8), nullable=False, index=True)
    port_name = Column(String(128), nullable=False)
    country_code = Column(String(8), nullable=False, index=True)
    jurisdiction = Column(String(64), nullable=False)
    port_authority = Column(String(128), nullable=False)
    tariff_identifier = Column(String(64), nullable=False, index=True)
    charge_category = Column(String(64), nullable=False, index=True)
    applicability_type = Column(String(32), nullable=False, default="MANDATORY")
    vessel_class = Column(String(64), nullable=False, default="DRY_BULK")
    basis_dimension = Column(String(32), nullable=False)
    min_threshold = Column(Float, nullable=True)
    max_threshold = Column(Float, nullable=True)
    rate_value = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    billing_unit = Column(String(64), nullable=False)
    min_charge = Column(Float, nullable=True)
    max_charge = Column(Float, nullable=True)
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=False, index=True)
    publication_date = Column(Date, nullable=True)
    retrieval_date = Column(DateTime(timezone=True), nullable=False)
    source_url = Column(String(512), nullable=False)
    document_identifier = Column(String(128), nullable=False)
    precise_source_reference = Column(String(256), nullable=False)
    evidence_classification = Column(String(64), nullable=False, default="DIRECTLY_VERIFIED")
    permission_status = Column(String(64), nullable=False, default="PERMITTED_FOR_STATUTORY_ESTIMATION")
    supersession_status = Column(String(32), nullable=False, default="HISTORICAL")
    applicability_condition = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
```

---

## 5. Evidence & Readiness Governance Framework

Pelagos strictly decouples source cataloging from operational calculation permission across three independent readiness tiers:

| Readiness Tier | Status | Permitted Operational Scope | Enforcement Action |
|---|---|---|---|
| **Level 1 (Source Registry)** | **APPROVED** | Ingestion, cataloging, and structured indexing of port regulations and tariff schedules. | Stored in `PortTariffRecord` database. |
| **Level 2 (Deterministic Rules)** | **CONDITIONALLY APPROVED** | Deterministic evaluation of documented formulas for research, validation, and sensitivity testing. | Returns itemized breakdown with explicit failure reasons. |
| **Level 3 (Operational Disbursements)** | **STRICTLY BLOCKED** | Execution of port disbursement estimates for commercial freight bidding, net TCE calculation, or voyage optimization. | Blocked fail-closed via `operational_mode=True` gate. |

### Strict Fail-Closed Invariants:
1. **Unregistered Port Gate:** If a port UN/LOCODE is queried that does not exist in the catalog, the engine strictly fails closed with `PORT_NOT_REGISTERED`. Generating regional synthetic averages or defaulting to neighboring port rates is strictly prohibited.
2. **Missing Dimension Gate:** If a tariff requires Gross Tonnage (e.g. Singapore, Rotterdam), LOA (e.g. Houston dockage), Draft (e.g. Houston pilotage), or Cargo Handled (e.g. Rotterdam wharfage) and the input is missing or $\le 0$, the line item fails closed with `MISSING_VESSEL_GT`, `MISSING_VESSEL_LOA`, `MISSING_VESSEL_DRAFT`, or `MISSING_CARGO_HANDLED`.
3. **Currency Gate:** Converts local statutory billing currencies (SGD, EUR, etc.) to USD base currency using official exchange rates from `CurrencyConversionService`. If an exchange rate fixing is missing for the exact transit date, the line item strictly fails closed with `MISSING_CURRENCY_EXCHANGE_RATE`.
4. **Historical Expiration Gate:** Tariff records are strictly bound to their `effective_from` and `effective_to` dates. Transit dates outside published schedules fail closed.

---

## 6. Testing & Verification Summary

The port disbursement foundation is verified by a dedicated automated test suite (`tests/test_port_disbursements.py`):

```powershell
pytest tests/test_port_disbursements.py -v
```

### Coverage (11 Tests):
- **Singapore Tests:** Standard cargo call calculation, 50% bunkering port dues concession, anchorage grace period threshold.
- **Rotterdam Tests:** Composite GT + cargo handled formula, mandatory SAB waste fee, fail-closed behavior on missing cargo tonnage.
- **Houston Tests:** LOA harbor fee tiers, dockage per LOA foot per day, 10% security surcharge, draft pilotage, pilot units.
- **Governance Tests:** Unregistered port fail-closed, missing required dimensions fail-closed, missing currency exchange rate fail-closed, operational mode Level 3 block enforcement, database persistence and auto-migration.

### Full Test Suite Execution:
- `tests/test_port_disbursements.py`: **11 passed**
- `tests/test_canal_tariffs.py`: **17 passed**
- `tests/test_vessel_fuel_model.py`: **19 passed**
- `tests/test_engine_sfoc.py`: **9 passed**
- **Combined Test Status:** **56 passed, 0 failed, 0 regressions** in 22.48s.

---

## 7. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-11-15 | DeepMind Antigravity Engineering Agent | Initial foundation release for Stage F1.4.2.3b port disbursement intelligence, normalized taxonomy, data models, pilot port rules (Singapore, Rotterdam, Houston), and fail-closed governance. |

