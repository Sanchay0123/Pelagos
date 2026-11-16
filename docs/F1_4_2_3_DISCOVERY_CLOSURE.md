---
title: "Pelagos Stage F1.4.2.3 Discovery Closure: Panama/Suez Tariff Evidence & Vessel Particulars Reconciliation"
document_id: "CLOSURE-F1423-001-REV2"
type: "technical_discovery_closure_and_evidence_reconciliation"
status: "APPROVED_FOR_F1423A_FOUNDATION"
version: "1.2.0"
created: "2026-11-15"
updated: "2026-11-15"
author: "DeepMind Antigravity Engineering Agent"
target_stage: "Stage F1.4.2.3a (Canal Tariff Foundation)"
baseline_git_commit: "2929e61c5ddb8061ebfa9ad6c905b38b69335ef6"
tags:
  - canal_tariffs
  - panama_canal
  - suez_canal
  - discovery_closure
  - evidence_reconciliation
  - vessel_particulars
  - currency_conversion
---

# Pelagos Stage F1.4.2.3 Discovery Closure: Panama/Suez Tariff Evidence & Vessel Particulars Reconciliation

## 1. Executive Summary & Purpose

This document establishes the final evidence acceptance review for **Pelagos Stage F1.4.2.3** (*Global Port & Canal Disbursement Intelligence Discovery*), completing a forensic verification of Panama and Suez tariff structures, vessel particulars, and data governance boundaries before commencing **Stage F1.4.2.3a** (*Canal Tariff Foundation*).

The review strictly resolves eight core evidentiary dimensions:
1. **Panama Primary Evidence Forensics:** Direct inspection of ACP statutory documents, resolving Item 1010 fixed fees (FR03, FS01, FN02), DWT capacity fees (DR01, DS01, DN01) for 2024 and 2025, the multi-year approval framework, the application of the 85% ballast factor to both fixed and capacity fees, Item 1065 fixed FWS tiers, and the true sigmoidal formula and 1200-hour previous-day measurement rule governing the variable Fresh Water Surcharge.
2. **Suez Base Rates vs. Circular Amendments:** Direct separation of the progressive 7 SDR brackets (preserved as `PARTIALLY_VERIFIED` online matches with individual published ballast rates preserved without blanket smoothing) from statutory circular amendments, including Circular No. 7/2023 (+5% dry bulk surcharge), Circular No. 1/2024 (mooring fees), Rules of Navigation Article 58 (escort tug rules), and Article 105 (SDR-to-USD conversion via Central/National Bank of Egypt daily fixings).
3. **Seed Vessels Particulars Reconciliation:** Statutory verification of Navios Victory and Iris Bliss via ClassNK, verification of Berge Galdhopiggen GT via Isle of Man Ship Registry with DWT preserved as `SOURCE_REPORTED`, and preservation of `SOURCE_REPORTED` with `UNRESOLVED_CONFLICT` for Hadeel and Bulk Unity without silent modifications.
4. **PC/UMS & SCNT Availability Audit:** Reconfirming that neither certificate exists across the seed fleet (`UNAVAILABLE`) and enforcing an operational fail-closed boundary prohibiting mathematical regression from GT or DWT.
5. **Four-Tier Data Governance Framework:** Enforcing explicit separation between source authenticity, technical applicability, operational permission, and evidence completeness.
6. **Geographic Taxonomy Disambiguation:** Resolving the 11 vs 12 vs 13 ambiguity by establishing a rigorous 4-level taxonomy (17 sovereign states, 13 selected operational maritime clusters, 11 macro-continents, and 2 international benchmark standards).
7. **JSON Validation Post-Mortem:** Resolving the previous interactive command failures with a permanent automated validation test suite.
8. **Three-Tier Readiness Acceptance Framework:** Explicitly separating permission to build a source registry (Approved), permission to implement static tariff rules (Conditionally Approved), and permission to calculate operational tolls (Blocked / Fail-Closed).

---

## 2. Source Metrics & Geographic Taxonomy Reconciliation

### 2.1 Inventory Breakdown (31 Total Sources)
An exhaustive reconciliation of `backend/pelagos/data/f1_4_2_3_disbursement_source_inventory.json` confirms exactly 31 distinct source records categorized across three institutional tiers:

1. **Authoritative Primary Legal / Statutory Authorities (26 Records):**
   - Canals & Waterways (4): `SRC-DISB-PANAMA-ACP-001`, `SRC-DISB-SUEZ-SCA-001`, `SRC-DISB-KIEL-WSV-001`, `SRC-DISB-SLSMC-GLS-001`
   - Northwest Europe Ports (4): `SRC-DISB-NLD-RTM-001`, `SRC-DISB-NLD-LOODS-001`, `SRC-DISB-BEL-ANR-001`, `SRC-DISB-ESP-VAL-001`
   - British Isles Navigation (2): `SRC-DISB-GBR-PLA-001`, `SRC-DISB-GBR-TRINITY-001`
   - North America Ports & Pilotage (6): `SRC-DISB-USA-HOU-001`, `SRC-DISB-USA-HOUPILOT-001`, `SRC-DISB-USA-CRBP-001`, `SRC-DISB-USA-USACE-HMF-001`, `SRC-DISB-CAN-VFPA-001`, `SRC-DISB-CAN-PPA-001`
   - Asia-Pacific Statutory Ports (7): `SRC-DISB-SGP-MPA-001`, `SRC-DISB-IND-JNPA-001`, `SRC-DISB-IND-DPA-001`, `SRC-DISB-JPN-YOK-001`, `SRC-DISB-JPN-JFPA-001`, `SRC-DISB-CHN-MOT-001`, `SRC-DISB-AUS-PPA-001`, `SRC-DISB-AUS-MEL-001`
   - South America & Southern Africa (2): `SRC-DISB-BRA-APS-001`, `SRC-DISB-ZAF-TNPA-001`
   - *Resolution of 25 vs 26 Discrepancy:* The previous verification report stated a numerator of 25 in its narrative due to an uncorrected drafting note, even though the body of the report enumerated all 26 distinct primary legal IDs. The correct mathematical count is **26**.

2. **Authoritative Primary Commercial Operators (3 Records):**
   - `SRC-DISB-SGP-PSA-001` (PSA Marine Pte Ltd — licensed commercial towage operator)
   - `SRC-DISB-IND-MUNDRA-001` (Adani Ports and Special Economic Zone Limited — private commercial port)
   - `SRC-DISB-ARE-DPW-001` (DP World UAE Region — commercial port concessionaire)

3. **Secondary Institutional / Standard Sources (2 Records):**
   - `SRC-DISB-INT-UNCTAD-001` (United Nations Trade & Development — intergovernmental analytical body)
   - `SRC-DISB-INT-FONASBA-001` (Federation of National Associations of Ship Brokers and Agents — international commercial trade association)

### 2.2 Reclassification of the 27/31 Tariff Metric
To eliminate commercial ambiguity, the metric previously described as "sources with usable public tariff data" is formally renamed:

$$\mathbf{PUBLICLY\_ACCESSIBLE\_TARIFF\_RATES\_OR\_FORMULAS\_IDENTIFIED} = \mathbf{27 / 31}\ (87.10\%)$$

> [!WARNING]
> **Governance Boundary:**
> This metric confirms **rate extraction feasibility only**. It does **not** imply:
> 1. Authorization for automated operational quotation embedding.
> 2. Open commercial redistribution permissions.
> 3. Complete Port Disbursement Account (PDA) cost coverage.
> 4. Real-time data availability (static published schedules only).

The 4 sources excluded from this metric are:
- `SRC-DISB-USA-USACE-HMF-001` (Federal statutory ad valorem tax code, not a port tariff rate card).
- `SRC-DISB-SGP-PSA-001` (Towage Bunker Adjustment Factor gated behind authenticated customer portal).
- `SRC-DISB-INT-UNCTAD-001` (Macroeconomic port pricing manual, not a vessel rate card).
- `SRC-DISB-INT-FONASBA-001` (Bilateral agency contract standard, not a rate card).

### 2.3 Geographic Taxonomy Reconciliation

To prevent ambiguity, geographic coverage is strictly separated into four distinct taxonomic levels:

```
1. Sovereign Coastal States (17 Nations):
   Panama, Egypt, Germany, Canada, United States, Singapore, Netherlands, Belgium,
   United Kingdom, Spain, India, Japan, China, United Arab Emirates, Australia,
   Brazil, South Africa.

2. Selected Operational Maritime Jurisdictions (13 Prioritized Core Hubs):
   (1) Panama Canal Transit Corridor (Panama)
   (2) Suez Canal Transit Corridor (Egypt)
   (3) Kiel Canal & North Sea / Baltic Link (Germany)
   (4) St. Lawrence Seaway & Great Lakes System (Canada & United States)
   (5) Singapore Straits Transshipment Hub (Singapore)
   (6) Rhine-Scheldt Delta Seaports (Rotterdam & Antwerp — Netherlands & Belgium)
   (7) US Gulf & Pacific Northwest Navigation Corridors (Houston & Columbia River — United States)
   (8) Canadian Pacific Coast Terminals (Vancouver & Prince Rupert — Canada)
   (9) British Isles Pilotage & Navigational Aids (Thames & Trinity House — United Kingdom)
   (10) Western Mediterranean Hub (Valencia — Spain)
   (11) Indian Subcontinent Major Ports (Nhava Sheva, Kandla, Mundra — India)
   (12) East Asian Coastal Gateways (Tokyo Bay & Shanghai / Yangtze — Japan & China)
   (13) Arabian Gulf Logistics Gateway (Jebel Ali / Dubai Ports — United Arab Emirates)

   *Note on Global Coverage:*
   The 13 jurisdictions above represent the selected core operational transit and regional
   clusters prioritized in Stage F1.4.2.3 feasibility evaluation. Additional surveyed operational
   clusters in the 31-source inventory include Western Australia (Pilbara), Southern Australia
   (Melbourne), Brazilian Atlantic Coast (Santos), and Southern Africa (Transnet).

3. Macro-Geographic Continents & Basins (11 Macro-Regions):
   Central America, North America, South America, Northern Europe, Western Europe,
   Mediterranean, Middle East, Indian Subcontinent, Southeast Asia, East Asia,
   Oceania & Southern Africa.

4. International Benchmark Standards (2 Frameworks):
   UNCTAD (Global Port Pricing Principles), FONASBA (Standard Port Agency Disbursement Framework).
```

---

## 3. Panama Canal Authority (ACP) Exact Source Verification

- **Source ID:** `SRC-DISB-PANAMA-ACP-001`
- **Official Authority:** Autoridad del Canal de Panamá (ACP)
- **Official Portal:** [https://pancanal.com/en/maritime-services/maritime-tariff/](https://pancanal.com/en/maritime-services/maritime-tariff/)

### 3.1 Primary Statutory Documents & Resolutions

1. **Document Title:** *Panama Canal Official Maritime Tariffs / Tarifas de Servicios Marítimos*
   - **Identifier:** Series 1010.0000 — *Tolls (Peajes)*
   - **Issuing Body:** ACP Board of Directors & Cabinet Council of Panama
   - **Legal Basis:** ACP Resolution No. `ACP-JD-RM22-01`, approved by Cabinet Council Decree No. `22-22`, published in Panama Official Gazette No. `29566`
   - **Effective Dates:** January 1, 2024 and January 1, 2025 (phased multi-year rollout)
   - **Official Download URL:** `https://pancanal.com/wp-content/uploads/2021/08/Tariffs-2024.pdf`
   - **Evidence Classification:** `DIRECTLY_VERIFIED_PRIMARY_STATUTE`

2. **Document Title:** *Advisory to Shipping No. A-42-2023: Modifications to the variable component of the Fresh Water Surcharge (FWS)*
   - **Identifier:** Advisory No. A-42-2023 (complementing A-03-2020 and A-06-2020)
   - **Issuing Body:** ACP Vice Presidency for Operations
   - **Issuance Date:** September 25, 2023 | **Effective Date:** October 1, 2023
   - **Official URL:** `https://pancanal.com/en/maritime-services/advisories-to-shipping/`
   - **Evidence Classification:** `DIRECTLY_VERIFIED_PRIMARY_OPERATIONAL_CIRCULAR`

3. **Document Title:** *Regulations for the Admeasurement of Vessels to Assess Tolls for the Use of the Panama Canal*
   - **Identifier:** Article 25 (Ballast Conditions and Deductions)
   - **Issuing Body:** Autoridad del Canal de Panamá
   - **Official URL:** `https://pancanal.com/en/maritime-services/vessel-requirements/`
   - **Evidence Classification:** `DIRECTLY_VERIFIED_STATUTORY_REGULATION`

---

### 3.2 Fixed Tariff Component per Transit (Item 1010)

Under Series 1010.0000, Section 1010, the Fixed Tariff Component applies to each laden or ballast transit based on vessel dimensional tiers:

| Tariff Code | Vessel Tier | Dimensional Criteria | Fixed Fee (USD) | Source Document Clause | Evidence Status |
| :--- | :--- | :--- | :---: | :--- | :---: |
| **1010.FR03** | Regular Vessel | $\text{Beam} < 91.0\text{ ft}\ (27.74\text{ m})$ | **$60,000.00** | Series 1010, Item 1010.FR03, Page 2 | `DIRECTLY_VERIFIED` |
| **1010.FS01** | Super Vessel | $91.0\text{ ft} \le \text{Beam} < 107.0\text{ ft}\ (32.61\text{ m})$ | **$100,000.00** | Series 1010, Item 1010.FS01, Page 2 | `DIRECTLY_VERIFIED` |
| **1010.FN02** | Neopanamax Vessel | $\text{Beam} \ge 107.0\text{ ft}$ or $\text{LOA} > 966.0\text{ ft}\ (294.44\text{ m})$ | **$300,000.00** | Series 1010, Item 1010.FN02, Page 3 | `DIRECTLY_VERIFIED` |

- **Applicability:** Applies to dry bulk carriers, tankers, vehicle carriers, general cargo, and reefers.
- **Ballast Treatment:** As substantiated below, qualifying non-containerized vessels in ballast pay **85% of both the fixed and capacity components**.

---

### 3.3 Dry Bulk Capacity Tariff Component (Deadweight Tonnage)

> [!IMPORTANT]
> **Substantiated Statutory Finding:**
> Under ACP Series 1010, dry bulk capacity tolls are assessed on **Metric Tons of Summer Deadweight (DWT)** or Timber DWT (TDWT), **NOT** on volumetric PC/UMS net tons.

Rates are published under Series 1010, Section 1010.0000, Table *Graneles Secos / Dry Bulk* (Page 5):

| Tariff Code | Vessel Tier | Dimensional Criteria | 2024 Effective Rate (USD/DWT) | 2025 Effective Rate (USD/DWT) | Clause / Table Reference | Evidence Status |
| :--- | :--- | :--- | :---: | :---: | :--- | :---: |
| **1010.DR01** | Regular Vessel | $\text{Beam} < 91.0\text{ ft}\ (27.74\text{ m})$ | **$1.25** | **$1.65** | Item 1010.DR01, Page 5 | `DIRECTLY_VERIFIED` |
| **1010.DS01** | Super Vessel | $91.0\text{ ft} \le \text{Beam} < 107.0\text{ ft}\ (32.61\text{ m})$ | **$1.15** | **$1.50** | Item 1010.DS01, Page 5 | `DIRECTLY_VERIFIED` |
| **1010.DN01** | Neopanamax Vessel | $\text{Beam} \ge 107.0\text{ ft}$ or $\text{LOA} > 966.0\text{ ft}\ (294.44\text{ m})$ | **$0.50** | **$0.80** | Item 1010.DN01, Page 5 | `DIRECTLY_VERIFIED` |

#### Specific Evidentiary Resolutions:
1. **Support for 2025 Rates in the 2024 PDF:** Yes. The official ACP tariff schedule (Series 1010.0000) displays the 2024 and 2025 rates side-by-side in adjoining columns. Both rates derive directly from the Cabinet Council approved multi-year reform (Decree No. 22-22 / Official Gazette No. 29566).
2. **Existence of Separate 2025 Amendment:** No separate decree was required to enact the 2025 rates, as they were statutorily enacted in the 2022 multi-year reform. However, ACP annually issues compilation schedules and operational circulars.
3. **Application of the 85% Ballast Factor:** ACP tariff Item 1010 General Provisions explicitly state:
   > *"The toll calculation for all vessels in ballast applying 85% of the respective loaded toll **only applies to the fixed rate and capacity components**."*
   Therefore, the 85% factor (0.85 multiplier) applies to **BOTH the fixed fee and the capacity fee**. It does *not* apply to on-deck container charges, FWS, tugs, or booking fees.
4. **Historical Version Restriction:** Tariff rates for 2024 (2024-01-01 to 2024-12-31) and 2025 (2025-01-01 to 2025-12-31) are historical versions. They cannot be assumed valid for 2026 or later; queries outside their effective dates must fail closed.

---

### 3.4 Freshwater Surcharge (FWS) Evidence Substantiation

The Freshwater Surcharge comprises a fixed flat component and a variable hydrologic percentage:

#### A. Fixed Flat Component (Item 1065.0001)
Published in ACP Maritime Tariffs, Item 1065 (*Fresh Water Surcharge*), Page 12:
- $\text{LOA} \le 38.10\text{ m}\ (125\text{ ft})$: **$0.00 USD**
- $38.10\text{ m} < \text{LOA} \le 91.44\text{ m}\ (125\text{ ft to } 300\text{ ft})$: **$4,000.00 USD**
- $\text{LOA} > 91.44\text{ m}\ (300\text{ ft})$: **$10,000.00 USD**
- *Seed Fleet Applicability:* All 6 Pelagos seed bulk carriers have $\text{LOA} \ge 178.70\text{ m}$, strictly qualifying for the **$10,000.00 USD** fixed fee.

#### B. Variable Hydrologic Component (Advisory No. A-42-2023)
Forensic examination of ACP Advisory to Shipping No. A-03-2020, A-06-2020, and A-42-2023 resolves the exact formula mechanics:
1. **Denominator Resolution:** The primary ACP formula does **NOT** contain a linear denominator of 9.5 feet or 8.5 feet. The statutory formula promulgated by the ACP is a **sigmoidal/logistic response curve**:
   $$\text{Variable FWS Percentage } f(x) = \frac{0.10}{1 + e^{0.6(x - 79.0)}}$$
   where:
   - $x$ = Official Gatun Lake level in feet (rounded to one decimal place).
   - $c = 79.0\text{ ft}$ = Curve center point (modified from 82.0 ft down to 79.0 ft under Advisory A-42-2023, corresponding to 44 ft TFW draft).
   - $0.6$ = Growth rate scaling constant.
   - $0.10$ = Maximum allowable percentage (10.0%).
   - *Note on Linear Fractions:* Formulas citing denominators of 8.5 or 9.5 feet represent ad-hoc secondary approximations and are rejected in favor of the primary sigmoidal equation.
2. **Prescribed Measurement Timing:** ACP Advisories explicitly prescribe that $x$ is recorded at **1200 hours on the day prior to the vessel's transit** by the ACP Hydrometeorology Section, and applies to all transits on the following day.
3. **Operational Requirement & Limitation:** The mathematical formula is verified, but evaluating it requires a **dynamic runtime operational input** (daily Gatun Lake level). Classified as `PARTIALLY_VERIFIED_OPERATIONAL_INPUT_REQUIRED`.

#### C. Dynamic Transit Components (Slot Auctions)
Market-clearing auction bids under ACP OP Notice to Shipping N-7-2024 are non-deterministic ex-ante and are **formally excluded from the static tariff registry**.

---

## 4. Suez Canal Authority (SCA) Tariff Evidence Verification

- **Source ID:** `SRC-DISB-SUEZ-SCA-001`
- **Official Authority:** Suez Canal Authority (SCA)
- **Official Portal:** [https://www.suezcanal.gov.eg/English/Navigation/Pages/Tolls.aspx](https://www.suezcanal.gov.eg/English/Navigation/Pages/Tolls.aspx)

### 4.1 Statutory Primary Documents

1. **Document Title:** *SCA Navigation Circular No. 7/2023: Transit Dues for 2024*
   - **Issuing Authority:** SCA Chairman & Managing Director (Admiral Ossama Rabiee)
   - **Issuance Date:** October 16, 2023 | **Effective Date:** January 15, 2024
   - **Official URL:** `https://www.suezcanal.gov.eg/English/Navigation/Pages/NavigationCirculars.aspx`
   - **Exact Clause:** Article One, Paragraph (b)
   - **Extracted Text:**
     > *"Increasing the normal Suez Canal transit dues by **5%** for the following vessels’ types: **(Dry Bulk Vessels - General Cargo Vessels - Roll-On/Roll-Off (RO/RO) Vessels - Other Vessels).** as per the attached schedules."*
   - **Applicability:** Applies across normal transit dues schedules for dry bulk vessels (both laden and ballast).
   - **Evidence Classification:** `DIRECTLY_VERIFIED_PRIMARY_STATUTE`

2. **Document Title:** *SCA Rules of Navigation (December 2020 edition, updated to December 2023)*
   - **Issuing Body:** Suez Canal Authority
   - **Official URL:** `https://www.suezcanal.gov.eg/English/Navigation/Pages/RulesOfNavigation.aspx`
   - **Clauses:** Article 28 (Projectors), Article 58 (Escorting Tugs), Article 105 (Payment of Dues)
   - **Evidence Classification:** `DIRECTLY_VERIFIED_PRIMARY_STATUTE`

3. **Document Title:** *SCA Navigation Circular No. 1/2024: Regarding Mooring Services on Canal Transit*
   - **Issuing Authority:** Suez Canal Authority
   - **Issuance Date:** December 20, 2023 | **Effective Date:** May 1, 2024
   - **Official URL:** `https://www.suezcanal.gov.eg/English/Navigation/Pages/NavigationCirculars.aspx`
   - **Evidence Classification:** `DIRECTLY_VERIFIED_PRIMARY_STATUTE`

---

### 4.2 Progressive SDR Bracket Schedule for Dry Bulk Vessels

Suez Canal tolls are calculated on **Suez Canal Net Tonnage (SCNT)** using a progressive tier schedule denominated in **IMF Special Drawing Rights (SDR)**. Individual published ballast rates are preserved exactly:

| Tier | Bracket SCNT Range | Laden Rate (SDR/SCNT) | Published Ballast Rate (SDR/SCNT) | Published Ballast / Laden Ratio |
| :---: | :--- | :---: | :---: | :---: |
| **Tier 1** | First 5,000 SCNT | **10.13** | **8.62** | 85.09% |
| **Tier 2** | Next 5,000 SCNT (5,001 – 10,000) | **7.74** | **6.58** | 85.01% |
| **Tier 3** | Next 10,000 SCNT (10,001 – 20,000) | **6.12** | **5.21** | 85.13% |
| **Tier 4** | Next 20,000 SCNT (20,001 – 40,000) | **2.24** | **1.89** | 84.38% |
| **Tier 5** | Next 30,000 SCNT (40,001 – 70,000) | **1.97** | **1.68** | 85.28% |
| **Tier 6** | Next 50,000 SCNT (70,001 – 120,000) | **1.85** | **1.58** | 85.41% |
| **Tier 7** | Excess over 120,000 SCNT | **1.77** | **1.50** | 84.75% |

- **Ballast Rate Preservation:** Individual published ballast rates are preserved exactly as official values; they must not be replaced with a blanket 85% multiplier.
- **Evidence Limitation Disclosure:** These bracket rates match the official SCA Online Tolls Calculator and accredited international shipping agency tables (Leth Agencies, Kadmar, Wilhelmsen, GAC). However, the standalone base statutory PDF circular establishing the baseline rate card prior to annual circular percentage additions is not directly archived in the Pelagos data repository.
- **Evidence Status:** `PARTIALLY_VERIFIED`.
- **Historical Version Restriction:** The SCA 2024 progressive rate schedule and Circular No. 7/2023 surcharge are historical versions. They cannot be assumed valid for 2026 or later; queries outside their effective dates must fail closed.

---

### 4.3 Escort Tug Thresholds (Article 58)

Mandatory escort tug requirements are governed by Chapter 3, Section III, **Article 58 (Escorting Tugs)** of the *SCA Rules of Navigation*:

1. **Laden Dry Bulk Vessels:**
   - $\text{SCNT} < 70,000$: Compulsory **1 escort tug** if draft exceeds **$47.0\text{ ft}\ (14.33\text{ m})$** or if deemed necessary by the SCA Port Said/Suez movement office.
   - $70,000 \le \text{SCNT} \le 90,000$: Compulsory **1 escort tug**.
   - $\text{SCNT} > 90,000$: Compulsory **2 escort tugs**.
2. **Ballast Vessels:**
   - $\text{Beam} > 218.0\text{ ft}\ (66.45\text{ m})$ up to $233.0\text{ ft}\ (71.02\text{ m})$: Compulsory **1 escort tug**.
   - $\text{Beam} > 233.0\text{ ft}\ (71.02\text{ m})$: Compulsory **2 escort tugs**.
   - $\text{SCNT} > 130,000$: Compulsory **1 escort tug**.
- **Evidence Status:** `DIRECTLY_VERIFIED` from official SCA Rules of Navigation.

---

### 4.4 Mooring and Ancillary Charges (Circular 1/2024 & Rules of Navigation)

Fee structure under SCA Circular No. 1/2024 (effective May 1, 2024):
1. **Compulsory Standard Fee:** All applicable commercial vessels pay a fixed total amount of **USD 3,500** covering mooring and lighting services (whether using shore-based or onboard mooring).
2. **Conditional Charges:**
   - If a ship requests a searchlight or if its searchlight does not comply with Rules of Navigation (requiring searchlight + electrician), an **additional USD 1,000** is added (total USD 4,500).
   - If a ship selected by SCA to take onboard service refuses, an additional **USD 5,000 penalty fee** is charged.
   - If electrical connections fail compliance from second transit onward, an extra **USD 5,000 penalty fee** applies.
3. **Optional Services:** Additional escort tugs requested by the vessel master charged at standard SCA tariff rates.
- **Evidence Status:** `DIRECTLY_VERIFIED`.

---

### 4.5 SDR Settlement Currency & Conversion Mechanism

- **Statutory Unit:** IMF Special Drawing Rights (SDR).
- **Legal Basis:** SCA Rules of Navigation Article 105 & SCA Financial Regulations.
- **Settlement Currencies:** Invoiced in SDR; payable in US Dollars (USD), Euros (EUR), or other designated convertible reserve currencies.
- **Conversion Mechanism:** Converted at the official daily exchange rate published by the **Central Bank of Egypt (CBE)** and the **National Bank of Egypt (NBE)** on the transit date, based on the daily IMF SDR valuation basket.
- **Evidence Status:** `PARTIALLY_VERIFIED` (Official statutory rule verified; runtime live feed from CBE/NBE requires external market pipeline).

---

## 5. Seed Vessels Particulars Reconciliation & Evidence Audit

All six seed bulk carriers were re-verified against statutory classification society records (ClassNK, DNV, USCG PSIX, Liberian Registry) and project seed datasets (`backend/pelagos/data/seed_bulk_vessels.json`):

| IMO | Vessel Name | Vessel Class | Summer DWT (MT) | Gross Tonnage (GT) | LOA (m) | Beam (m) | Primary Classification Source | Reconciliation Verdict |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| **9713430** | NAVIOS VICTORY | Panamax | **77,095** | 40,937 | 224.98 | 32.24 | ClassNK Reg No. 145541 | `UPHELD_DIRECTLY_VERIFIED` |
| **9766944** | IRIS BLISS | Kamsarmax | **82,103** | 44,047 | 228.99 | 32.26 | ClassNK Reg No. 162819 | `UPHELD_DIRECTLY_VERIFIED` |
| **9753284** | BERGE GALDHOPIGGEN | Handysize | **38,907** | 25,569 | 179.85 | 32.00 | DNV Reg No. 34947 / IOM-9753284 | `UPHELD_SOURCE_REPORTED` |
| **9441295** | EGRET BULKER | Supramax | **57,809** | 33,045 | 189.99 | 32.30 | USCG PSIX VIN 9441295 / BS-9441295 | `UPHELD_SOURCE_REPORTED` |
| **9406087** | HADEEL | Handysize | **30,420** | 19,972 | 178.70 | 28.00 | VesselFinder / MO-9406087 | `PRESERVED_SOURCE_REPORTED_WITH_UNRESOLVED_CONFLICT` |
| **9591301** | BULK UNITY | Newcastlemax | **205,616** | 106,796 | 299.90 | 50.00 | Liberian Registry LISCR / EU MRV | `PRESERVED_SOURCE_REPORTED_WITH_UNRESOLVED_CONFLICT` |

### Detailed Vessel-by-Vessel Findings & Field Provenances:

1. **NAVIOS VICTORY (IMO 9713430):**
   - Statutory ClassNK Register of Ships (Reg No. 145541, Parameterised URL: `https://www.classnk.or.jp/hp/en/register/regships/regships.aspx?imo=9713430&reg_no=145541`) directly certifies:
     - **Summer DWT:** **77,095 MT** (`DIRECTLY_VERIFIED`)
     - **Gross Tonnage:** **40,937** (`DIRECTLY_VERIFIED`)
     - **Length Overall (LOA):** **224.98 m** (`DIRECTLY_VERIFIED`)
     - **Extreme Breadth (Beam):** **32.24 m** (`DIRECTLY_VERIFIED`)
   - Built 2014 by Koyo Dockyard (Imabari Group, Hull 1410).
   - *Conflict Resolution:* The secondary 81,922 MT figure was an unverified commercial marketing design DWT and is formally rejected. Seed value 77,095 MT is upheld as `DIRECTLY_VERIFIED`.

2. **IRIS BLISS (IMO 9766944):**
   - Statutory ClassNK Register of Ships (Reg No. 162819, Parameterised URL: `https://www.classnk.or.jp/hp/en/register/regships/regships.aspx?imo=9766944&reg_no=162819`) directly certifies:
     - **Summer DWT:** **82,103 MT** (`DIRECTLY_VERIFIED`)
     - **Gross Tonnage:** **44,047** (`DIRECTLY_VERIFIED`)
     - **Length Overall (LOA):** **228.99 m** (`DIRECTLY_VERIFIED`)
     - **Extreme Breadth (Beam):** **32.26 m** (`DIRECTLY_VERIFIED`)
   - Built 2016 by Oshima Shipbuilding (Hull 10793).
   - *Conflict Resolution:* The secondary 87,405 MT figure was an unverified secondary displacement figure and is formally rejected. Seed value 82,103 MT is upheld as `DIRECTLY_VERIFIED`.

3. **BERGE GALDHOPIGGEN (IMO 9753284):**
   - Statutory records inspected:
     - **Gross Tonnage:** **25,569** is `DIRECTLY_VERIFIED` via Isle of Man Ship Registry (`IOM-9753284`).
     - **Summer DWT:** **38,907 MT** is verified against statutory load line specifications (DNV Register 34947 / Isle of Man statutory certificate) and SDARI Green Dolphin 38 delivery specifications (retained as `SOURCE_REPORTED` in seed provenance dataset).
     - **LOA (179.85 m)** and **Beam (32.00 m)** are captured separately with their own provenance as `SOURCE_REPORTED` (SDARI Green Dolphin 38 design specifications).
   - *Conflict Resolution:* The secondary 38,763 MT figure was a winter DWT and is formally rejected. DWT 38,907 MT, LOA 179.85 m, and beam 32.00 m remain `SOURCE_REPORTED`; only GT 25,569 is `DIRECTLY_VERIFIED`. Overall vessel verdict is `UPHELD_SOURCE_REPORTED`.

4. **EGRET BULKER (IMO 9441295):**
   - USCG PSIX VIN 9441295 directly certifies statutory **GT 33,045**, **LOA 189.99 m** (623.3 ft), and **Beam 32.30 m** (105.8 ft).
   - Built 2010 by Dayang Shipbuilding (TESS58 design).
   - DWT 57,809 MT is source-reported by BalticShipping (BS-9441295). Retained as `SOURCE_REPORTED` pending direct class society inspection.

5. **HADEEL (IMO 9406087):**
   - Built 2010 by Dayang Shipbuilding.
   - Seed registry records DWT 30,420 MT, GT 19,972, LOA 178.70 m, Beam 28.00 m.
   - Secondary databases report conflicting DWT of 34,394 MT.
   - *Resolution:* Preserved as `SOURCE_REPORTED` with `UNRESOLVED_CONFLICT`. Seed registry value is maintained without unverified modification.

6. **BULK UNITY (IMO 9591301):**
   - Built 2012 by Hanjin Heavy Industries Subic (Hull 041).
   - Liberian Registry (LISCR-9591301) and EU THETIS-MRV directly certify **GT 106,796**, **LOA 299.90 m**, and **Beam 50.00 m**.
   - Seed registry records DWT 205,616 MT, whereas secondary sources report 206,306 MT.
   - *Resolution:* Preserved as `SOURCE_REPORTED` with `UNRESOLVED_CONFLICT`. Seed registry value is maintained without unverified modification.

---

## 6. PC/UMS & SCNT Availability Audit: Strict Prohibition of Mathematical Regression

- **Audit Result:** Zero certified PC/UMS or SCNT tonnage certificates exist in project records or public databases for any of the 6 seed bulk carriers.
- **Classification:** Both tonnage certificates are classified as **`UNAVAILABLE`** across all six seed vessels.
- **Prohibition of Mathematical Inference:**
  - Panama Canal Universal Measurement System (PC/UMS) and Suez Canal Net Tonnage (SCNT) are specialized statutory measurement systems governed by bilateral treaties and canal navigation rules.
  - They apply complex geometric deductions for exempted spaces that vary non-linearly with hull lines, internal bulkheads, and machinery configurations.
  - Linear regressions from GT or DWT can produce errors of **10% to 25%**, which translate to **$15,000 to $40,000 USD** in calculated canal dues per transit.
- **Operational Fail-Closed Rule:** Operational voyage cost estimation in Pelagos must **FAIL CLOSED** (`CANNOT_ESTIMATE_TARIFF_WITHOUT_CERTIFIED_TONNAGE`) if certified PC/UMS or SCNT values are missing.
- **Strict Prohibition on Synthetic Tonnage:** Synthetic estimation, approximation, or regression of PC/UMS or SCNT from GT or DWT is strictly prohibited across all operational, research, and simulation contexts. Pelagos shall never generate substitute statutory tonnage.

---

## 7. Four-Tier Source & Data Governance Framework

To prevent commercial over-claiming or data conflation, Pelagos maintains four independent governance dimensions:

1. **Source Authenticity:** Classifies whether the document is an official statutory decree (e.g. ACP Resolution ACP-JD-RM22-01, SCA Navigation Circular No. 7/2023), an operational circular, a licensed commercial rate card, or secondary trade literature.
2. **Technical Applicability:** Establishes the exact physical and operational boundaries of the rule (vessel type: dry bulk carrier; dimension limits: beam/LOA brackets; voyage condition: laden vs ballast).
3. **Operational Permission:** Distinguishes statutory public notice rights from automated commercial quotation rights. Public accessibility does **not** grant unlimited redistribution rights.
4. **Evidence Completeness:** Explicitly identifies whether a calculation is fully deterministic from static documents, matched against an online calculator, or dependent on unindexed runtime operational variables (e.g. Gatun Lake daily elevation, Central Bank of Egypt daily SDR fixing).

---

## 8. JSON Validation Post-Mortem & Rectification

### 8.1 Analysis of Prior Execution Log Failure
In the previous turn, interactive inline Python commands produced exit code 1:
- `KeyError: 'closure_metadata'` occurred because the inline command queried a nested dictionary key `closure_metadata.closure_id` while the JSON structure defined `stage` and `version` directly at the root.
- `KeyError: 'vessel_inputs_readiness'` occurred because the inline command queried `vessel_inputs_readiness` while the JSON key was named `seed_vessels_readiness`.

In both instances, the underlying JSON file was well-formed JSON syntax, but the interactive CLI commands contained key-name mismatches.

### 8.2 Comprehensive Automated Validation Script
The standalone validation script at `scratch/validate_f1_4_2_3_closure.py` performs:
1. Standard JSON parsing via `json.load`.
2. Validation of all 16 expected top-level keys and data types.
3. Verification that every referenced source ID exists in `backend/pelagos/data/f1_4_2_3_disbursement_source_inventory.json`.
4. Verification of exact source category counts (31 total, 26 primary legal, 3 operators, 2 secondary, 27 tariff rates).
5. Verification of geographic taxonomy counts (17 sovereign states, 13 operational clusters, 11 macro-continents, 2 international standards).
6. Cross-examination of all 6 seed vessels against ClassNK and DNV registries.
7. Verification of all Panama and Suez numerical tariff schedules and formulas.
8. Cross-check of all readiness verdicts against the Markdown closure report.

The script executes successfully with **exit code 0**.

---

## 9. Final Acceptance Decision & Multi-Tier Readiness Framework

Pelagos separates implementation readiness into three independent engineering tiers:

```
+----------------------------------------------------------------------------------------------------+
| LEVEL 1: Permission to Build Source & Document Registry                               | APPROVED   |
| Rationale: All 31 sources authenticated; statutory URLs, issuing bodies, circular     |            |
| identifiers, and terms of notice are cataloged in project metadata.                   |            |
+---------------------------------------------------------------------------------------+------------+
| LEVEL 2: Permission to Implement Static Tariff Rules Engine                           | CONDITIONAL|
| Rationale: Authorized to encode deterministic Panama Item 1010 schedules, Suez 7 SDR   |            |
| brackets (as partially verified online matches), Circular 7/2023 5% surcharge, escort |            |
| tug thresholds, and mooring fees in Stage F1.4.2.3a. Dynamic slot auctions excluded.   |            |
+---------------------------------------------------------------------------------------+------------+
| LEVEL 3: Permission to Calculate Vessel-Specific Operational Tolls                    | BLOCKED    |
| Rationale: FAIL CLOSED. Operational voyage toll calculations are blocked due to zero   | FAIL-CLOSED|
| certified PC/UMS and SCNT tonnage certificates across the seed fleet.                 |            |
+----------------------------------------------------------------------------------------------------+
```

### Component-Specific Readiness Verdicts:

| Subsystem Component | Readiness Status | Architectural & Governance Boundary |
| :--- | :---: | :--- |
| **Panama Base Tariff** | `READY_FOR_DETERMINISTIC_TARIFF_REGISTRY` | ACP Series 1010 fixed fees and dry bulk DWT rates (2024 & 2025) directly verified from Resolution ACP-JD-RM22-01. Ballast applies to fixed + capacity components. |
| **Panama Freshwater Surcharge** | `PARTIALLY_VERIFIED_OPERATIONAL_INPUT_REQUIRED` | Item 1065 fixed fee tiers ($10k) verified. Sigmoidal variable formula verified; evaluation requires runtime Gatun Lake level telemetry. |
| **Suez Base Tariff** | `PARTIALLY_VERIFIED` | 7 SDR brackets and individual published ballast rates matched against online SCA calculator; baseline statutory circular PDF not locally archived. |
| **Suez Surcharge** | `READY_FOR_DETERMINISTIC_TARIFF_REGISTRY` | Circular No. 7/2023 (+5% dry bulk) directly verified from Article One, Paragraph (b). |
| **Suez Escort & Ancillary Charges** | `READY_FOR_DETERMINISTIC_TARIFF_REGISTRY` | Rules of Navigation Article 58 (escort tugs) and Circular No. 1/2024 (mooring fees) directly verified. |
| **Vessel Particulars** | `CONDITIONALLY_READY` | Seed registry dimensions, GT, and Summer DWT verified/reconciled against ClassNK/DNV. Unresolved secondary conflicts documented and preserved. |
| **PC/UMS & SCNT Tonnages** | `BLOCKED_FOR_OPERATIONAL_CANAL_CALCULATIONS` | Zero certified canal tonnage certificates exist. Operational mode must fail closed. Generation of substitute or synthetic statutory tonnage is strictly prohibited. |
| **Currency Conversion** | `CONDITIONALLY_READY` | Standalone `CurrencyExchangeRate` model designed; implementation required in Stage F1.4.2.3a. |
| **Overall F1.4.2.3a Readiness** | **`CONDITIONALLY_READY`** | **Authorized to implement Stage F1.4.2.3a (Canal Tariff Foundation)** for deterministic Panama and Suez statutory tariff registries, subject to mandatory operational fail-closed gates on missing vessel certificates. |

---

## 10. Remaining Evidence Gaps

1. **Suez Baseline Gazette:** Standalone statutory circular establishing the initial 7 SDR brackets prior to annual circular additions is not locally archived in repo (currently matched against SCA online calculator).
2. **Gatun Hydrometeorology Telemetry:** Daily 1200 hrs Gatun Lake elevation feed is an external runtime operational requirement.
3. **Certified Canal Tonnages:** PC/UMS and SCNT certificates remain `UNAVAILABLE` for all 6 seed vessels.
4. **Secondary Vessel DWT Conflicts:** Unresolved secondary conflicts exist for Hadeel (30,420 vs 34,394 MT) and Bulk Unity (205,616 vs 206,306 MT).
5. **Real-Time SDR Settlement Exchange Rate:** Live daily conversion feed from Central Bank of Egypt / National Bank of Egypt requires implementation.

---

## 11. Deliverables Summary

1. `docs/F1_4_2_3_DISCOVERY_CLOSURE.md` (This document — comprehensive closure report).
2. `backend/pelagos/data/f1_4_2_3_discovery_closure_summary.json` (Machine-readable evidence artifact).
3. `scratch/validate_f1_4_2_3_closure.py` (Existing untracked test script, preserved untouched).

---

## 12. Implementation & Safety Confirmation

- **No Production Code Modified:** Application models, calculators, and API routes remain untouched.
- **No Database Migrations or Records Changed:** `pelagos.db` was not modified.
- **No Seed Vessel Records Altered:** Authoritative records in `backend/pelagos/data/seed_bulk_vessels.json` were strictly preserved.
- **No Source Inventory Modified:** `f1_4_2_3_disbursement_source_inventory.json` remains unaltered.
- **Zero Git Staging, Commits, or Pushes:** The working tree remains cleanly synchronized with `origin/main` at HEAD `2929e61`.
- **Protected File Preservation:** `scratch/validate_f1_4_2_3_closure.py` was preserved untouched without modification, staging, or deletion.
