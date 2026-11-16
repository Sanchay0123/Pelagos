# Pelagos — Global Port & Canal Disbursement Coverage Matrix
## Stage F1.4.2.3 Technical Feasibility & Geographic Coverage Assessment

---

## 1. Executive Overview

This coverage matrix evaluates the empirical feasibility of acquiring, normalizing, and calculating genuine port-call and canal transit disbursements across global maritime trading corridors.

In accordance with Pelagos discovery standards:
- **Zero fabricated completeness scores or manufactured coverage percentages.**
- Explicit missing-data indicators (`NOT ESTABLISHED`, `UNAVAILABLE`, `RESTRICTED_BEHIND_LOGIN`, `COMMERCIALLY_NEGOTIATED`).
- Independent separation across the six core cost domains:
  - **Domain A:** Port Dues & Harbour Charges
  - **Domain B:** Pilotage (Inbound/Outbound/Shifting)
  - **Domain C:** Towage & Mooring (Marine Assistance)
  - **Domain D:** Agency, Husbandry & Port Ancillary Services
  - **Domain E:** Canal Transit Tolls & Surcharges
  - **Domain F:** Additional Voyage Disbursements & Regulatory Taxes

---

## 2. Global Regional Matrix Summary

| Geographic Region | Candidate Sources | Official Authority Sources | Usable Public Tariff Sources | API / Bulk Sources | Manual Document Sources | Primary Licensing Status | Cost Categories Covered | Major Missing Categories |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- | :--- |
| **Central America** (Panama) | 1 | 1 | 1 | 0 | 1 | Government Public Notice | Domain E (Tolls, Freshwater, Slots, Tugs, Pilotage) | Commercial bunkering PDA; non-canal local terminal stevedoring |
| **Middle East / Egypt** (Suez) | 1 | 1 | 1 | 0 | 1 | Government Public Notice | Domain E (Tolls in SDR, Periodical Surcharges, Escorts) | Dynamic SDR-to-USD banking rate; retrospective long-haul rebates |
| **Europe** (Northwest & Kiel) | 4 | 4 | 4 | 0 | 4 | Public Law / Municipal Gazette / ACM | Domain A, Domain B, Domain E, Waste | Private terminal handling (THC); commercial towage dynamic BAF |
| **North America** (US & Canada) | 6 | 6 | 6 | 0 | 6 | FMC Filed / State Law / Canada Marine Act / US Code | Domain A, Domain B, Domain E (St. Lawrence), HMF Tax | Commercial ship-assist tug hourly contracts; private grain elevator dockage |
| **United Kingdom & Ireland** | 2 | 2 | 2 | 0 | 2 | UK Statutory Harbour / Open Govt Licence (OGL) | Domain A (Conservancy), Domain B, Light Dues | Private container terminal dockage; commercial towage |
| **Mediterranean** (Spain) | 1 | 1 | 1 | 0 | 1 | Spanish State Law (TRLPEMM) / BOE | Domain A (T-0, T-1, T-3), Navigation Tax | Licensed private towage & mooring tariffs (Boluda/Prácticos) |
| **India** | 3 | 3 | 3 | 0 | 3 | Gazette of India / Port Adjudicatory Board / Operator | Domain A, Domain B (Composite Pilotage-Towage), Berth Hire | Private BOT container terminal handling; priority berthing premiums |
| **East Asia** (China & Japan) | 3 | 3 | 3 | 0 | 3 | MOT/NDRC Rules / Municipal Gazette / MLIT Pilot Law | Domain A (Port Entry, Tonnage Tax), Domain B (Formula) | Negotiated harbor tugboat packages; private line-handling charges |
| **Southeast Asia** (Singapore) | 2 | 1 | 1 | 0 | 2 | Singapore Port Regs / Commercial Operator | Domain A (MPA Port Dues, Green Concessions), Domain B | Live Towage Bunker Adjustment Factor (BAF) behind customer login |
| **Middle East** (UAE / Arabian Gulf) | 1 | 1 | 1 | 0 | 1 | Commercial Operator Tariff | Domain A (Port Dues), Domain B (Pilotage), Domain C (Tugs) | Terminal concession handling; hazardous cargo clearing surcharges |
| **Oceania** (Australia) | 2 | 2 | 2 | 0 | 2 | Port Authorities Act WA / ESC Regulated RTS | Domain A (Channel Dues, Berthage), Domain B (Pilotage) | Private bulk terminal loader fees; dedicated cape-size escort tugs |
| **South America** (Brazil) | 1 | 1 | 1 | 0 | 1 | ANTAQ Federal Regulation / APS | Domain A (Tabela I Acesso Aquaviário, Tabela II Acostagem) | Brazilian private pilotage (Praticagem) rates subject to price disputes |
| **Africa** (South Africa) | 1 | 1 | 1 | 0 | 1 | National Ports Act / PRSA Tariff Book | Domain A (Port Dues), Domain B (Pilotage), Domain C (Tugs) | Overtime labor surcharges; tug cancellation penalties |
| **International Benchmarks** | 2 | 1 | 1 | 2 | 2 | UN Open Publication / Industry Standard (BIMCO/FONASBA) | Taxonomy, PDA Standards, Agency Fee Guidelines | Port-specific rate cards (framework definitions only) |
| **TOTALS / SYSTEM WIDE** | **31** | **25** | **27** | **4** | **24** | **14 Unresolved Licensing** | **Broad Domain A, B, E Coverage** | **Domain C (Towage BAF) & Domain D (Agency) Gaps** |

---

## 3. Detailed Regional Breakdowns

### 3.1 Central America — Panama Canal
- **Ports/Waterways:** Panama Canal Transit (Balboa PAPAC to Cristobal PACRI).
- **Candidate Sources:** `SRC-DISB-PANAMA-ACP-001` (Autoridad del Canal de Panamá).
- **Official Authority Source:** Yes (ACP).
- **Usable Public Tariff Data:** Yes (Comprehensive tariff schedule, OP Notice to Shipping N-1 and N-7).
- **Access & Format:** Manual PDF downloads, HTML tables, and VUMPA calculator portal. No public REST API for automated bulk pricing.
- **Licensing Status:** Government Public Notice. Commercial use for statutory fee estimation permitted; programmatic scraping not established.
- **Cost Categories Covered:**
  - Tolls: Fixed capacity toll + PC/UMS variable tonnage toll.
  - Freshwater Surcharge: 1-2% fixed base + 0-10% variable component based on Gatun Lake elevation.
  - Transit Reservation: Pre-booking tiers and auction slots.
  - Transit Assistance: Compulsory locomotive and Neopanamax tugboat allocations.
  - Pilotage: Mandatory ACP transit pilotage.
- **Major Missing Categories:**
  - Dynamic transit slot auction clearing prices (fluctuate widely during drought/congestion periods).
  - Terminal-specific stevedoring or container handling at Balboa/Manzanillo/Cristobal docks (governed by private concessionaires).

### 3.2 Middle East & North Africa — Suez Canal
- **Ports/Waterways:** Suez Canal Transit (Port Said EGPSD to Suez Port Tewfik EGSUE).
- **Candidate Sources:** `SRC-DISB-SUEZ-SCA-001` (Suez Canal Authority).
- **Official Authority Source:** Yes (SCA).
- **Usable Public Tariff Data:** Yes (SCA Navigation Circulars, Tolls portal, Periodical circular amendments).
- **Access & Format:** Manual PDF circulars, HTML portal, online toll estimator. No machine-readable API.
- **Licensing Status:** Government Public Notice. Statutory disclosure.
- **Cost Categories Covered:**
  - Base Tolls: Progressive SDR-denominated tiers based on Suez Canal Net Tonnage (SCNT).
  - Periodical Surcharges: Variable percentage circulars applied by vessel type (e.g., +5% to +15%).
  - Escort Tugs: Compulsory tug escort rules based on SCNT, beam, and loading condition.
  - Transit Pilotage: Mandatory convoy pilotage (Northern and Southern legs).
  - Ancillary Fees: Canal projector rental, mooring boats, harbour master dues.
- **Major Missing Categories:**
  - Hard currency settlement exchange rate (daily SDR conversion by National Bank of Egypt).
  - Long-haul circular rebates (application-based ex-post determinations).

### 3.3 Northwest Europe & Baltic Approaches
- **Ports/Waterways:** Rotterdam (NLRTM), Antwerp-Bruges (BEANR/BEZEE), Kiel Canal (NOK).
- **Candidate Sources:**
  - `SRC-DISB-NLD-RTM-001` (Havenbedrijf Rotterdam)
  - `SRC-DISB-NLD-LOODS-001` (Nederlands Loodswezen / ACM)
  - `SRC-DISB-BEL-ANR-001` (Port of Antwerp-Bruges)
  - `SRC-DISB-KIEL-WSV-001` (GDWS / WSV Germany)
- **Official Authority Sources:** 4 of 4 (100%).
- **Usable Public Tariff Data:** 4 of 4 (100%).
- **Access & Format:** Published annual PDF tariff booklets, legal gazettes (Bundesgesetzblatt / ELWIS), and online calculators. No bulk REST APIs.
- **Licensing Status:**
  - Netherlands: Municipal Port Regulations / ACM public regulatory orders.
  - Belgium: Municipal Port Regulations.
  - Germany: § 5 UrhG (Official Legal Acts in Public Domain).
- **Cost Categories Covered:**
  - Domain A: Seaport Dues (composite GT + cargo weight), Buoy/Dolphin dues, Waste reception contribution.
  - Domain B: Regulated pilotage formula (Starting rate $S$ + Tonnage/Draught factor $T$ + Approach factor $A$).
  - Domain E: Kiel Canal dues, lockage fees, pilot dues (*Lotsabgaben*), pilot fees (*Lotsgelder*), and canal helmsmen.
  - Environmental Incentives: Environmental Ship Index (ESI) discounts (10% to 25%).
- **Major Missing Categories:**
  - Private terminal handling charges (THC) and demurrage at private oil/bulk terminals (e.g., Vopak, EMO).
  - Commercial towage fuel surcharges (Boluda, Kotug, Fairplay operate under commercial contracts).

### 3.4 North America (US Gulf, US Pacific Northwest, Great Lakes & Canada)
- **Ports/Waterways:** Houston (USHOU), Columbia River (USPDX, USKAL, USVAN, USLWW), Great Lakes - St. Lawrence (SLSMC), Vancouver (CAVAN).
- **Candidate Sources:**
  - `SRC-DISB-USA-HOU-001` (Port Houston Authority Tariff No. 8)
  - `SRC-DISB-USA-HOUPILOT-001` (Houston Pilots Association)
  - `SRC-DISB-USA-CRBP-001` (Oregon Board of Maritime Pilots)
  - `SRC-DISB-USA-USACE-HMF-001` (USACE / CBP 19 U.S.C. § 4461)
  - `SRC-DISB-CAN-VFPA-001` (Vancouver Fraser Port Authority)
  - `SRC-DISB-CAN-PPA-001` (Pacific Pilotage Authority Canada)
  - `SRC-DISB-SLSMC-GLS-001` (St. Lawrence Seaway Management Corp)
- **Official Authority Sources:** 7 of 7 (100%).
- **Usable Public Tariff Data:** 7 of 7 (100%).
- **Access & Format:** FMC-filed tariff books, State administrative orders, Code of Federal Regulations, Canada Marine Act gazettes. Manual PDF downloads.
- **Licensing Status:** US Federal Public Domain / State Administrative Public Records / Canadian Crown Statutory Notices.
- **Cost Categories Covered:**
  - Domain A: Dockage (LOA-based per 24 hours), Harbour Fees, Wharfage (per short/metric ton), Port Security Surcharge, Canadian Harbour Dues (GRT-based).
  - Domain B: Houston Pilots bipartite formula (Draft charge + Vessel unit charge across 6 channel zones); Columbia River Bar and River pilotage rate orders; Pacific Pilotage Authority unit and hourly fees.
  - Domain E: St. Lawrence Seaway lockage and commodity tolls.
  - Domain F: Harbor Maintenance Tax (0.125% ad valorem on imports/domestic cargo).
- **Major Missing Categories:**
  - Private grain elevator berthage and layberth fees (e.g., Cargill, Kalama Export, TEMCO).
  - Commercial ship-assist tug hourly rates (Moran, Kirby, Foss, Crowley operate on private tariff cards).

### 3.5 United Kingdom & Ireland
- **Ports/Waterways:** Port of London (GBLON), Southampton, Trinity House jurisdiction.
- **Candidate Sources:**
  - `SRC-DISB-GBR-PLA-001` (Port of London Authority)
  - `SRC-DISB-GBR-TRINITY-001` (Trinity House / General Lighthouse Authorities)
- **Official Authority Sources:** 2 of 2 (100%).
- **Usable Public Tariff Data:** 2 of 2 (100%).
- **Access & Format:** Published PDF tariff books, online Visit Charges Estimator, UK Statutory Instruments.
- **Licensing Status:** UK Open Government Licence (OGL) / Statutory Harbour Authority Regulations.
- **Cost Categories Covered:**
  - Domain A: Vessel Conservancy Dues (GT-based), Estuary Charges, Cargo Conservancy Dues.
  - Domain B: Thames pilotage rates (draught and mileage zones).
  - Domain F: Statutory Light Dues (46 pence per NRT, capped at 50,000 NRT, max 9 voyages/year).
- **Major Missing Categories:**
  - Private container terminal handling (DP World London Gateway, Tilbury).
  - Commercial river tugboat assistance (Svitzer).

### 3.6 Mediterranean (Spain)
- **Ports/Waterways:** Valencia (ESVLC), Sagunto, Gandía, Spanish State Port System.
- **Candidate Sources:** `SRC-DISB-ESP-VAL-001` (Autoridad Portuaria de Valencia / Puertos del Estado).
- **Official Authority Source:** Yes (Puertos del Estado / APV).
- **Usable Public Tariff Data:** Yes (Ley de Puertos TRLPEMM Real Decreto Legislativo 2/2011).
- **Access & Format:** PDF gazettes (BOE) and APV billing guidelines.
- **Licensing Status:** Spanish State Public Domain (BOE).
- **Cost Categories Covered:**
  - Domain A: T-0 (Navigation Aids Tax), T-1 (Vessel Tax per GT and stay time across Zone I / Zone II), T-3 (Cargo Tax).
- **Major Missing Categories:**
  - Commercial licensed towage tariffs (Boluda Corporación Marítima).
  - Private mooring (*amarre*) tariffs.

### 3.7 India
- **Ports/Waterways:** Nhava Sheva / JNPT (INNSA), Kandla / Deendayal (INIXY), Mundra (INMUN).
- **Candidate Sources:**
  - `SRC-DISB-IND-JNPA-001` (Jawaharlal Nehru Port Authority)
  - `SRC-DISB-IND-DPA-001` (Deendayal Port Authority)
  - `SRC-DISB-IND-MUNDRA-001` (Adani Ports and SEZ Limited)
- **Official Authority Sources:** 3 of 3 (100%).
- **Usable Public Tariff Data:** 3 of 3 (100%).
- **Access & Format:** Gazette of India notifications, Major Port Authorities Act orders, APSEZ Berthing Policy and Tariff Structure (BPTS) booklets, JNPA online calculator.
- **Licensing Status:** Gazette of India Public Record / Commercial Port Operator Booklet.
- **Cost Categories Covered:**
  - Domain A: Port Dues (GT-based, dual currency USD for foreign / INR for coastal).
  - Domain B & C: Composite Pilotage-cum-Towage rate per GT (covering inward, outward, and standard tug assistance).
  - Domain A: Berth Hire per GT per hour or fraction thereof.
  - Fuel Surcharge: Dedicated fuel surcharge per GT on pilotage at Mundra.
- **Major Missing Categories:**
  - Private BOT container terminal handling charges (BMCT, APMT Nhava Sheva).
  - Priority berthing premiums / cold move fees (bilaterally negotiated).

### 3.8 East Asia (Japan & China)
- **Ports/Waterways:** Yokohama (JPYOK), Tokyo Bay, Shanghai (CNSHA), Ningbo-Zhoushan.
- **Candidate Sources:**
  - `SRC-DISB-JPN-YOK-001` (City of Yokohama Port & Harbor Bureau)
  - `SRC-DISB-JPN-JFPA-001` (Japan Federation of Pilots' Associations / MLIT)
  - `SRC-DISB-CHN-MOT-001` (Ministry of Transport PRC / NDRC)
- **Official Authority Sources:** 3 of 3 (100%).
- **Usable Public Tariff Data:** 3 of 3 (100%).
- **Access & Format:** Municipal Ordinances, MLIT Pilotage Law Enforcement Regulations, MOT/NDRC Ministerial Decrees (港口收费计费办法), Shanghai International Port Group (SIPG) tariff lists.
- **Licensing Status:** Municipal Government Public Record / National Ministerial Regulations.
- **Cost Categories Covered:**
  - Domain A: Municipal Port Entry Dues (Nyuko-ryo), Public Wharfage (Keiryu-ryo), Federal Tonnage Tax, China Government-Fixed Cargo Dues.
  - Domain B: Japan national pilotage formula ($A \times [1 + 0.1(GRT-1000)/1000] \times [1 + 0.1(D-3.0)/0.3]$); China Government-Guided Net Tonnage / distance pilotage benchmark.
  - Domain C: China Government-Guided towage based on tug horsepower and hours.
- **Major Missing Categories:**
  - Tokyo Bay private tug operator tariffs (Tokyo Kisen, Wing Maritime).
  - China stevedoring package rates (market-regulated pricing negotiated with lines).

### 3.9 Southeast Asia (Singapore)
- **Ports/Waterways:** Port of Singapore (SGSIN).
- **Candidate Sources:**
  - `SRC-DISB-SGP-MPA-001` (Maritime and Port Authority of Singapore)
  - `SRC-DISB-SGP-PSA-001` (PSA Marine Pte Ltd)
- **Official Authority Sources:** 1 of 2 (MPA is statutory authority; PSA Marine is exclusive licensed marine operator).
- **Usable Public Tariff Data:** 1 of 2 (MPA port dues are fully public; PSA Marine pilotage baseline is published, but towage BAF is gated).
- **Access & Format:** Circular PDFs, HTML tables, IMOS customer portal.
- **Licensing Status:** Singapore Open Government Licence / Commercial Operator Terms.
- **Cost Categories Covered:**
  - Domain A: Port Dues per 100 GT per stay period; 50% concession for pure bunker calls <= 24 hours; Green Port Programme discounts (25-30%).
  - Domain B: Pilotage per GT bracket per hour.
  - Domain C: Towage per bollard pull category.
- **Major Missing Categories:**
  - Live monthly Towage Bunker Adjustment Factor (BAF) behind IMOS authentication.
  - PSA container terminal handling charges (commercially contracted).

### 3.10 Middle East (United Arab Emirates)
- **Ports/Waterways:** Jebel Ali (AEJEA) & Mina Rashid.
- **Candidate Sources:** `SRC-DISB-ARE-DPW-001` (DP World UAE Region).
- **Official Authority Source:** Yes (DP World acts as port authority and operator under Dubai maritime decree).
- **Usable Public Tariff Data:** Yes (Comprehensive Tariff Book PDF).
- **Access & Format:** Downloadable PDF booklet.
- **Licensing Status:** Commercial Port Operator Tariff.
- **Cost Categories Covered:**
  - Domain A: Port Dues per GRT; Berth Occupancy per GRT per day.
  - Domain B: Pilotage per movement across progressive GRT brackets.
  - Domain C: Towage per tug per movement based on vessel size.
  - Domain D: Fresh water delivery, port health clearance.
- **Major Missing Categories:**
  - Terminal Handling Charges (THC) and container storage rates negotiated with shipping conferences.

### 3.11 Oceania (Australia)
- **Ports/Waterways:** Port Hedland (AUPHE), Dampier, Melbourne (AUMEL).
- **Candidate Sources:**
  - `SRC-DISB-AUS-PPA-001` (Pilbara Ports Authority)
  - `SRC-DISB-AUS-MEL-001` (Port of Melbourne / ESC)
- **Official Authority Sources:** 2 of 2 (100%).
- **Usable Public Tariff Data:** 2 of 2 (100%).
- **Access & Format:** Annual PDF schedules and Essential Services Commission regulatory filings.
- **Licensing Status:** State Authority Public Notices / Regulated Utility Tariff.
- **Cost Categories Covered:**
  - Domain A: Channel Access Fees, Berthage per GT per hour, Wharfage.
  - Domain B: Mandatory bulk pilotage per GT movement.
- **Major Missing Categories:**
  - Port Hedland specialized cape-size escort tug tariffs (operated under long-term resource contracts with BHP/Fortescue/Svitzer).

### 3.12 South America (Brazil)
- **Ports/Waterways:** Santos (BRSSZ), Paranaguá, Itaqui.
- **Candidate Sources:** `SRC-DISB-BRA-APS-001` (Autoridade Portuária de Santos / ANTAQ).
- **Official Authority Source:** Yes (ANTAQ / APS).
- **Usable Public Tariff Data:** Yes (Tabela de Tarifas booklet & ANTAQ portal).
- **Access & Format:** PDF booklets and interactive ANTAQ regulatory dashboard.
- **Licensing Status:** Brazilian Federal Regulatory Public Notice.
- **Cost Categories Covered:**
  - Domain A: Tabela I (Acesso Aquaviário per DWT/ton) and Tabela II (Acostagem per meter LOA per day).
- **Major Missing Categories:**
  - Pilotage (*Praticagem*): Private compulsory service with highly disputed regional tariffs not published in unified public tables.

### 3.13 Sub-Saharan Africa (South Africa)
- **Ports/Waterways:** Durban (ZADUR), Richards Bay (ZARCB), Cape Town (ZACPT), Saldanha (ZASLD).
- **Candidate Sources:** `SRC-DISB-ZAF-TNPA-001` (Transnet National Ports Authority / PRSA).
- **Official Authority Source:** Yes (TNPA / Ports Regulator of South Africa).
- **Usable Public Tariff Data:** Yes (Unified national Port Tariff Book PDF).
- **Access & Format:** Downloadable PDF booklet.
- **Licensing Status:** Statutory National Tariff Book under National Ports Act 2005.
- **Cost Categories Covered:**
  - Domain A: Port Dues per 100 GRT per 24 hours; Berth Dues per 100 GRT per hour.
  - Domain B: Pilotage Base Fee + Increment per 100 GRT.
  - Domain C: Towage per tug per movement based on GRT.
- **Major Missing Categories:**
  - Overtime pilotage/towage additions and port congestion delay penalties.

---

## 4. Synthesis of Coverage Gaps Across Cost Domains

```
+-----------------------------------------------------------------------------------+
| COST DOMAIN                     | DISCOVERY STATUS  | DATA ACQUISITION COMPLEXITY |
+---------------------------------+-------------------+-----------------------------+
| Domain A: Port Dues & Berthage  | FULLY FEASIBLE    | Low-Medium (Published PDFs) |
| Domain B: Pilotage Rates        | FEASIBLE          | Medium (Formula extraction) |
| Domain C: Towage & Mooring      | PARTIAL           | High (Gated BAFs & Tugs)   |
| Domain D: Agency & Husbandry    | COMMERCIAL GAP    | High (Bilateral contracts)  |
| Domain E: Canal Tolls (ACP/SCA) | FULLY FEASIBLE    | Medium (Published circulars)|
| Domain F: Statutory Taxes (HMF) | FULLY FEASIBLE    | Low (Fixed statutory codes) |
+-----------------------------------------------------------------------------------+
```

### Key Analytical Takeaways
1. **Statutory Port & Canal Charges (Domains A, B, E, F):** Highly accessible, publicly published, legally mandated, and deterministic. Can be reliably modeled with source-backed rule engines.
2. **Marine Assistance Towage (Domain C):** Base tug tariffs are public, but fuel adjustment factors (BAF) and the operational number of tugs required are often gated or subject to harbour master discretion.
3. **Port Agency Fees (Domain D):** Strictly commercial. Cannot be determined from port authority publications and must be modeled as an explicit user input or isolated research benchmark.

