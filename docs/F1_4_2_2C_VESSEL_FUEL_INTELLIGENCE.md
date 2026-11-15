# Pelagos Stage F1.4.2.2c / F1.4.2.2c.3 — Vessel Fuel Consumption Intelligence Specification

**Document Identifier:** `DOC-F1422C-001-REV3`  
**Stage:** F1.4.2.2c, F1.4.2.2c.1, F1.4.2.2c.2 & F1.4.2.2c.3 (Final Fuel Model Consistency Closure)  
**Status:** Approved Statutory Evidence & Technical Consistency Standard  
**Effective Date:** 2026-11-15  
**Governing Standard:** Naval Architecture Hydrodynamics & Statutory Class Society Non-Synthetic Data Governance  

---

## 1. Executive Summary & Foundational Invariants

Stage **F1.4.2.2c.3** provides final technical consistency and evidence governance closure for the Pelagos vessel-level fuel consumption intelligence foundation.

### Core Architectural Invariants

1. **SFOC Alone Does Not Determine Vessel Fuel Consumption:**
   Specific Fuel Oil Consumption (SFOC, in $\text{g/kWh}$) defines the thermodynamic conversion efficiency of fuel to mechanical work at the engine shaft. It does **not** determine how much power the vessel requires to maintain a given speed through water. Estimating vessel-level fuel consumption requires an independent, defensible estimate of required engine brake power $P_{\text{req}}$ at a given speed and loading condition.

2. **DWT Is NOT Displacement ($\Delta \neq \text{DWT}$):**
   Deadweight Tonnage ($\text{DWT}$) represents the carrying capacity of cargo, bunker fuel, lube oil, fresh water, and ballast. Displacement ($\Delta$) represents the total immersed mass of the vessel, which strictly equals:
   $$\Delta = \text{DWT} + \text{LDT}$$
   where $\text{LDT}$ is the lightweight tonnage of the empty hull, machinery, and outfit.
   - For bulk carriers, lightweight represents $12\%\text{--}18\%$ of total laden displacement (e.g., $13,200\text{ MT}$ on an $82,103\text{ DWT}$ Kamsarmax).
   - Substituting $\text{DWT}$ for displacement underestimates $\Delta^{2/3}$ by $8\%\text{--}15\%$, producing severe systemic underestimations of propulsion power and daily fuel burn.
   - In Pelagos, substituting DWT for displacement is strictly prohibited and rejected by `InvalidDisplacementError`.

3. **Laden vs Ballast Condition Segregation (Zero Arbitrary Scaling):**
   Laden and ballast hydrostatics are hydrodynamically and geometrically distinct. Public classification society registers certify Summer scantling laden parameters, but **do not publish certified ballast displacement tables or ballast Admiralty calibrations**. Pelagos strictly prohibits deriving ballast displacement via arbitrary percentages (e.g. ~44%) or silently falling back to laden coefficients. When a ballast condition calculation is requested without verified ballast hydrostatics, the system fails closed to `UNAVAILABLE`.

4. **Weather Margin Research Sensitivity Governance:**
   The weather margin percentage is an explicitly labeled **research sensitivity parameter** (e.g., $0.0\%$ calm water, $15.0\%$ standard research allowance). It is **not** a validated dynamic weather correction derived from NOAA wind or wave hindcasts. Speed Through Water (STW) differs physically from Speed Over Ground (SOG): ocean currents directly impart vector displacement on SOG, while aerodynamic wind resistance and wave added resistance increase required shaft power at STW.

5. **Continuous Rated-Power Boundary Overload Semantics:**
   - Operating load percentage is calculated relative to verified continuous engine rating from statutory registers (e.g., continuous rated MCR brake power).
   - If required propulsion power exceeds $100\%$ rated continuous capacity, the model rejects the calculation with `CONTINUOUS_RATED_POWER_BOUNDARY_EXCEEDED (ENGINE_OVERLOAD)`.
   - The fuel model strictly establishes that its continuous rated-power boundary ($\le 100\%\text{ MCR}$) has been exceeded, and does not evaluate operating regimes beyond continuous rated power. It avoids making unverified universal claims regarding specific overload percentages, classification survey time limits, or manufacturer warranty clauses.
   - If required power corresponds to a load below the minimum documented SFOC curve point (e.g., $< 50\%$ MCR), the engine can physically operate (e.g. slow steaming, harbor maneuvering with auxiliary blowers running). However, because manufacturer documentation provides no verified SFOC points below $50\%$, the model returns `SFOC_UNAVAILABLE`. Zero-extrapolation governance prevents guessing unverified consumption rates.

6. **Power Definition Consistency (Engine Brake Power):**
   - The Admiralty coefficient is calibrated using Continuous Service Rating engine brake power ($P_{\text{B,CSR}}$) at the engine flywheel coupling.
   - Calculated power output represents estimated required engine brake power ($P_{\text{B,req}}$). It is neither delivered shaft power ($P_S$) nor propeller power ($P_D$).
   - Both the numerator ($P_{\text{B,req}}$) and denominator ($P_{\text{B,MCR}}$) in engine-load evaluation are defined on a consistent brake-power basis, eliminating the need for unverified shaft transmission efficiency conversions ($\eta_{\text{shaft}}$).

7. **Auxiliary & Port Consumption Scenario Governance:**
   Auxiliary and port consumption figures are aggregate fleet-average research scenarios derived from the Fourth IMO GHG Study 2020 (Table 23), **not observed or verified vessel telemetry**. The geared deck-crane operational increment ($300\text{ kW}$, approx $1.35\text{ MT/day}$) is an engineering research scenario, not a primary tabular distinction in the Fourth IMO GHG Study.

8. **Output Semantics & Separation of Concerns:**
   All combined fuel consumption estimates are classified as `CONSTRUCTED_RESEARCH_SCENARIO` (`is_constructed_research_scenario: true`, `is_observed_vessel_telemetry: false`). Under no circumstances may they be presented or labeled as observed, measured, verified, or actual vessel consumption. Individual components (propulsion power, engine load, SFOC, main engine rate, auxiliary rate, port rates) and their individual provenance are strictly preserved.

---

## 2. Admiralty Calibration Evidence & CSR Provenance Investigation

### Summary of Evidence Distinctions

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ DIRECTLY INSPECTED STATUTORY REGISTERS (ClassNK, DNV)                       │
│ - IMO, Vessel Name, Shipbuilder, Hull Number, Delivery Date                 │
│ - Summer Scantling Deadweight (DWT), Summer Scantling Draft                 │
│ - Main Engine Designer/Model, Cylinder Count, Bore, Stroke                  │
│ - Maximum Continuous Rating (MCR) [kW Brake Power] and Rated RPM            │
│ - Cargo Gear (Cranes count & SWL)                                           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ SECONDARY SOURCE-REPORTED SHIPYARD PARTICULARS                              │
│ - Lightweight Tonnage (LDT)                                                 │
│ - Laden Scantling Displacement (Delta = DWT + LDT)                          │
│ - Certified Design / Sea-Trial Speed (V_design)                             │
│ - Continuous Service Rating (CSR) Brake Power [kW]                          │
│ - CSR = ~85% MCR Coincidence -> UNVERIFIED_SECONDARY_RELATIONSHIP            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### CSR Provenance & Derivation Investigation (The 85% MCR Coincidence)

An investigation of the three candidate vessels revealed that all three reported CSR values correspond to approximately **85% of verified statutory MCR** (nominal 15% sea margin):
1. **IRIS BLISS:** Statutory MCR = $9,660\text{ kW}$. Reported CSR = $8,210\text{ kW}$ ($8,210 / 9,660 \approx 84.9896\%$, approximately $\mathbf{84.990\%}$).
2. **NAVIOS VICTORY:** Statutory MCR = $8,330\text{ kW}$. Reported CSR = $7,080\text{ kW}$ ($7,080 / 8,330 \approx 84.9939\%$, approximately $\mathbf{84.994\%}$, rounded to nearest $10\text{ kW}$ from $0.85 \times 8,330 = 7,080.5\text{ kW}$).
3. **BERGE GALDHOPIGGEN:** Statutory MCR = $6,100\text{ kW}$. Reported CSR = $5,185\text{ kW}$ ($5,185 / 6,100 = \mathbf{85.000\%}$, exact integer match for $0.85 \times 6,100 = 5,185\text{ kW}$).

**Naval Architectural Context & Investigation Finding:**
- In commercial shipbuilding contracts and summary technical brochures (Oshima, Imabari, Chengxi/SDARI), Continuous Service Rating (CSR) or Normal Continuous Rating (NCR) is conventionally specified at a nominal $85\%\text{ MCR}$ rating (corresponding to designing the propeller to absorb $85\%\text{ MCR}$ in calm water at design speed, leaving a nominal $15\%$ sea margin).
- Because primary statutory class registers (ClassNK, DNV) **do not index CSR**, and original shipyard contract sea-trial protocols and model basin test results are proprietary and uninspectable in public archives, it cannot be independently verified whether the reported values represent independently measured trial power or a nominal $85\%\text{ MCR}$ rule-of-thumb derivation.
- Governance Mandate: The reported CSR values are retained as `SOURCE_REPORTED`, their derivation is formally marked as `UNVERIFIED_SECONDARY_RELATIONSHIP`, and the existing research-only restriction is preserved without silent recalculation or replacement.

---

### Detailed Vessel-by-Vessel Evidence Dossier

#### 1. IRIS BLISS (IMO 9766944)
- **Directly Inspected Statutory Record:** ClassNK Register of Ships, Reg No. 162819 ([ClassNK Ships](https://www.classnk.or.jp/register/regships/regships.aspx)). Inspected: 2026-11-13. IMO 9766944, Ship Name IRIS BLISS, Flag Panama, DWT 82,103 MT, scantling draft 14.45 m, Oshima Hull 10793, delivery 2016-09-02, MAN B&W 6S60ME-C8.2, continuous rated MCR 9,660 kW at 89.0 rpm.
- **Secondary Source-Reported Shipyard Delivery Benchmark:** Oshima Hull 10793 Technical Delivery Particulars / 82,000 DWT Kamsarmax Class Spec. Reported LDT 13,200 MT, laden scantling displacement $\Delta_{\text{laden}} = 95,303\text{ MT}$, design speed 14.2 knots at CSR 8,210 kW brake power.
- **Power Definition & Derivation Governance:**
  - Benchmark power is explicitly Continuous Service Rating engine brake power ($P_{\text{B,CSR}}$) at flywheel coupling.
  - Reported CSR 8,210 kW represents approximately $84.990\%$ MCR ($8,210 / 9,660 \approx 84.9896\%$, nominal $85\%$ rating rounded to $8,210\text{ kW}$). Classified as `UNVERIFIED_SECONDARY_RELATIONSHIP`.
- **Admiralty Calibration Formula:**
  $$C_{\text{adm}} = \frac{\Delta^{2/3} \cdot V^3}{P_{\text{B,CSR}}} = \frac{95303^{2/3} \cdot 14.2^3}{8210} = \frac{2085.64 \times 2863.29}{8210} = 727.37 \approx 727.4$$
- **Model Limitations:** Single-point calibration assumes constant $C_{\text{adm}}$. It is a first-order research approximation, NOT an independent validation of the full speed-power curve. Retains `RESEARCH_ONLY` / `RESTRICTED_PENDING_PERMISSION` status.
- **Unsupported Evidence:** Ballast hydrostatics unindexed in class registers; fails closed to `UNAVAILABLE`.

#### 2. NAVIOS VICTORY (IMO 9713430)
- **Directly Inspected Statutory Record:** ClassNK Register of Ships, Reg No. 145541. Inspected: 2026-11-13. IMO 9713430, Ship Name NAVIOS VICTORY, Flag Panama, DWT 77,095 MT, scantling draft 14.47 m, Koyo Hull 1410 (Imabari Group), delivery 2014-11-20, MAN B&W 6S60ME-C8.2, continuous rated MCR 8,330 kW at 81.0 rpm.
- **Secondary Source-Reported Shipyard Delivery Benchmark:** Koyo Hull 1410 Technical Delivery Specification. Reported LDT 12,915 MT, laden scantling displacement $\Delta_{\text{laden}} = 90,010\text{ MT}$, design speed 14.1 knots at CSR 7,080 kW brake power.
- **Power Definition & Derivation Governance:**
  - Benchmark power is explicitly Continuous Service Rating engine brake power ($P_{\text{B,CSR}}$) at flywheel coupling.
  - Reported CSR 7,080 kW represents approximately $85.0\%$ MCR ($8,330 \times 0.85 = 7,080.5\text{ kW}$, rounded to $7,080\text{ kW}$). Classified as `UNVERIFIED_SECONDARY_RELATIONSHIP`.
- **Admiralty Calibration Formula:**
  $$C_{\text{adm}} = \frac{\Delta^{2/3} \cdot V^3}{P_{\text{B,CSR}}} = \frac{90010^{2/3} \cdot 14.1^3}{7080} = \frac{2007.58 \times 2803.22}{7080} = 794.88 \approx 794.9$$
- **Model Limitations:** Single-point calibration is a first-order research approximation. Retains research status. Ballast hydrostatics unindexed; fails closed to `UNAVAILABLE`.

#### 3. BERGE GALDHOPIGGEN (IMO 9753284)
- **Directly Inspected Statutory Record:** DNV Vessel Register, ID 34947 ([DNV Register](https://vesselregister.dnv.com/vesselregister/vesseldetails.html?vesselid=34947)). Inspected: 2026-11-13. IMO 9753284, Ship Name BERGE GALDHOPIGGEN, Flag Isle of Man, DWT 38,907 MT, scantling draft 10.50 m, Chengxi Hull CX0385, delivery 2017-04-18, WinGD 5RT-flex50D, continuous rated MCR 6,100 kW at 99.0 rpm, $4 \times 30.5\text{t}$ SWL cranes.
- **Secondary Source-Reported Shipyard Delivery Benchmark:** SDARI Green Dolphin 38 Design Specification / Chengxi Hull CX0385 Delivery Particulars. Reported LDT 8,593 MT, laden scantling displacement $\Delta_{\text{laden}} = 47,500\text{ MT}$, design speed 14.0 knots at CSR 5,185 kW brake power.
- **Power Definition & Derivation Governance:**
  - Benchmark power is explicitly Continuous Service Rating engine brake power ($P_{\text{B,CSR}}$) at flywheel coupling.
  - Reported CSR 5,185 kW represents exactly $85.00\%$ MCR ($6,100 \times 0.85 = 5,185.0\text{ kW}$). Classified as `UNVERIFIED_SECONDARY_RELATIONSHIP`.
- **Admiralty Calibration Formula:**
  $$C_{\text{adm}} = \frac{\Delta^{2/3} \cdot V^3}{P_{\text{B,CSR}}} = \frac{47500^{2/3} \cdot 14.0^3}{5185} = \frac{1311.41 \times 2744.00}{5185} = 693.98 \approx 694.0$$
- **Model Limitations:** Single-point calibration is a first-order research approximation. Retains research status. Ballast hydrostatics unindexed; fails closed to `UNAVAILABLE`.

#### 4. Unindexed Candidate Vessels (Fail-Closed Governance)
- **EGRET BULKER (IMO 9441295):** Classed by Bureau Veritas; unindexed in ClassNK/DNV registries. Hydrostatics and CSR unavailable; fails closed (`UNAVAILABLE`).
- **HADEEL (IMO 9406087):** Statutory ClassNK registration withdrawn; fails closed (`UNAVAILABLE`).
- **BULK UNITY (IMO 9591301):** Unindexed in public statutory class society registries; fails closed (`UNAVAILABLE`).

---

## 3. Candidate Six-Vessel Assessment Matrix

| Vessel Name | IMO | Statutory Class Society | Machinery Particulars | Hydrostatics Status | Permitted Use | Operational Mode Result | Research Mode Result (Laden, 12 kts) | Ballast Mode Result |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **IRIS BLISS** | 9766944 | ClassNK (162819) | MAN B&W 6S60ME-C ($9,660\text{ kW}$ MCR) | $\Delta_{\text{laden}} = 95,303\text{ MT}$, $C_{\text{adm}} = 727.4$ (Oshima 10793) | `RESTRICTED_PENDING_PERMISSION` | **UNAVAILABLE** (Restricted) | **RESEARCH** ($20.4\text{ MT/day ME} + 1.85\text{ Aux} = 22.25\text{ MT/day}$) | **UNAVAILABLE** (Ballast unindexed) |
| **NAVIOS VICTORY** | 9713430 | ClassNK (145541) | MAN B&W 6S60ME-C ($8,330\text{ kW}$ MCR) | $\Delta_{\text{laden}} = 90,010\text{ MT}$, $C_{\text{adm}} = 794.9$ (Koyo 1410) | `RESTRICTED_PENDING_PERMISSION` | **UNAVAILABLE** (Restricted) | **RESEARCH** ($18.0\text{ MT/day ME} + 1.85\text{ Aux} = 19.85\text{ MT/day}$) | **UNAVAILABLE** (Ballast unindexed) |
| **BERGE GALDHOPIGGEN**| 9753284 | DNV (34947) | WinGD 5RT-flex50D ($6,100\text{ kW}$ MCR) | $\Delta_{\text{laden}} = 47,500\text{ MT}$, $C_{\text{adm}} = 694.0$ (SDARI GD38) | `RESEARCH_ONLY` | **UNAVAILABLE** (Restricted) | **RESEARCH** ($13.6\text{ MT/day ME} + 1.85\text{ Aux} = 15.45\text{ MT/day}$; Port working $3.20\text{ MT/day}$) | **UNAVAILABLE** (Ballast unindexed) |
| **EGRET BULKER** | 9441295 | Bureau Veritas | Unavailable in ClassNK/DNV | Unavailable | `RESTRICTED_PENDING_PERMISSION` | **UNAVAILABLE** (Fail-closed) | **UNAVAILABLE** (Fail-closed) | **UNAVAILABLE** (Fail-closed) |
| **HADEEL** | 9406087 | ClassNK (Withdrawn) | Class withdrawn | Unavailable | `RESTRICTED_PENDING_PERMISSION` | **UNAVAILABLE** (Fail-closed) | **UNAVAILABLE** (Fail-closed) | **UNAVAILABLE** (Fail-closed) |
| **BULK UNITY** | 9591301 | Unindexed | Unindexed | Unavailable | `RESTRICTED_PENDING_PERMISSION` | **UNAVAILABLE** (Fail-closed) | **UNAVAILABLE** (Fail-closed) | **UNAVAILABLE** (Fail-closed) |

---

## 4. Non-Goals and Integration Boundaries

To maintain statutory fidelity and commercial safety:
1. **No Voyage Economics Integration:** This model does **not** connect to voyage freight calculators, Net Voyage Contribution (NVC), or Time Charter Equivalent (TCE) calculations.
2. **No Voyage Fuel Predictions:** Complete voyage fuel requirements (total bunker tons consumed over an entire routing corridor) are deferred to future stages.
3. **No Synthetic Hull Generation:** Infeasible resistance models (Holtrop–Mennen) remain rejected rather than fabricating missing bulb/transom geometry.
4. **No Claims of Validated Accuracy:** Single-point Admiralty calibrations remain strictly classified as research approximations, without claims of full speed-power curve validation.
5. **No Telemetry Mislabeling:** Outputs are never labeled as actual, measured, or observed consumption.
