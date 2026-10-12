Yes — **God’s Eye View can be useful for SIH PS-26006**, but mainly as a **data-visualization/reference architecture**, not as the actual forecasting-data source.

I checked PS-26006: it is about building an **intelligent freight forecasting + vessel chartering optimizer** for bulk coking coal moving to East Coast Indian ports. The requirements include freight-rate history, trade-lane dynamics, commodity/economic indicators, port congestion, and vessel/port constraints such as draft and LOA. ([Sihone](https://sihone.pages.dev/ps/26006?utm_source=chatgpt.com "SIH 26006 (SIH26006): Development of an Intelligent Freight Forecasting Model for Optimized Vessel Chartering and Bulk Cargo Procurement from overseas to East Coast of India — Datasets, Tech Stack & Solution Strategy | SIH 2026 | SIH ONE"))

### Where God's Eye View helps

|God's Eye View capability|Use in PS-26006|
|---|---|
|✈️ Vessel/aircraft-style live tracking|**Very useful** → adapt to vessel AIS tracking|
|🌍 3D geographic visualization|**Useful** → show Australia/US/Mozambique → India routes|
|📍 Real-time positions|**Useful** → vessel ETA/route monitoring|
|🗺️ Multiple data layers|**Very useful** → congestion, weather, ports, vessels|
|🛰️ Geospatial visualization|Useful for route intelligence|
|📊 Historical freight forecasting|**Not provided** → you need separate freight datasets|
|⚓ Port draft/LOA constraints|**Not provided** → build your own constraint database|
|🤖 Freight prediction|**Not provided** → your ML model|
|💰 Charter optimization|**Not provided** → your optimization engine|

### The strongest idea for your SIH project

Don't simply copy God's Eye View.

Use its **"single operational map" concept** as your front end:

```text
                 YOUR PS-26006 SYSTEM

                    ┌──────────────┐
                    │  3D MARITIME │
                    │     MAP      │
                    └──────┬───────┘
                           │
       ┌───────────────────┼───────────────────┐
       ↓                   ↓                   ↓
   🚢 Vessels          ⚓ Ports             🌊 Routes
   AIS data            Draft/LOA            Distance
   ETA                 Capacity             Transit
   Speed               Congestion            ETA
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ↓
                 ┌──────────────────┐
                 │ FREIGHT FORECAST │
                 │      MODEL       │
                 └────────┬─────────┘
                          ↓
                 📈 7/30/90-day rate
                     prediction
                          ↓
                 ┌──────────────────┐
                 │ OPTIMIZER        │
                 └────────┬─────────┘
                          ↓
            "CHART NOW / WAIT / CONTRACT"
```

This fits the actual PS particularly well because the problem explicitly asks for **optimal market-entry timing, vessel-type optimization, idle-time management and risk warnings**. ([Sihone](https://sihone.pages.dev/ps/26006?utm_source=chatgpt.com "SIH 26006 (SIH26006): Development of an Intelligent Freight Forecasting Model for Optimized Vessel Chartering and Bulk Cargo Procurement from overseas to East Coast of India — Datasets, Tech Stack & Solution Strategy | SIH 2026 | SIH ONE"))

### One important correction

For **PS-26006**, don't focus too much on aircraft/satellite data from God's Eye View.

Your valuable data is **maritime data**:

**AIS → vessel position → vessel type → speed → destination → ETA → port arrival/departure → congestion → freight rate → charter decision.**

For example:

> "Panamax vessel, 75,000 DWT, Australia → Paradip. Paradip draft constraint = X. Current freight = Y. Forecast 30-day freight = Z. Expected congestion = high. **Recommendation: don't book spot today; enter a 3-voyage contract within the next 5–8 days.**"

That is the kind of output that directly answers PS-26006.

### I'd build your project in 5 layers

**1. Data layer**  
AIS + Baltic freight indices + commodity prices + port data + weather + historical charter rates.

**2. Maritime digital twin**  
God's-Eye-View-style map showing vessels, ports and routes.

**3. Forecasting engine**  
Start simple with XGBoost/LightGBM or Prophet/ARIMA as baselines, then potentially use LSTM/TFT.

**4. Constraint engine**

```text
Cargo volume
     +
Origin
     +
Destination
     +
Vessel type
     ↓
Draft ✓
LOA ✓
Beam ✓
Port capacity ✓
Cargo handling ✓
Congestion ✓
     ↓
Feasible / Not feasible
```

**5. Decision engine**

Instead of merely saying:

> "Freight rate next month = $XX"

your system should say:

> **RECOMMENDATION: WAIT 6 DAYS → BOOK SUPRAMAX → 3-VOYAGE CONTRACT**

with an explanation of _why_.

That last layer is what can make your project much stronger than a generic "AI freight prediction dashboard."

If you want, I can also **map the God's Eye View GitHub repository file-by-file and tell you exactly which parts you could reuse/adapt for PS-26006, including the APIs and data sources you should use.**




Yes. For **SIH 26006**, I found a particularly strong historical-data target: **East Coast Australia → East Coast India Panamax freight**, which is almost exactly aligned with the coking-coal use case.

### Best data sources

1. **S&P Global Platts — Australia → India Panamax freight**
    
    This is the **best-fit dataset** for your project. Platts has a dedicated daily assessment called **CDBFAI0**, covering Panamax dry-bulk freight from East Coast Australia to India, normalized to **Hay Point → Paradip**. It has been assessed since **July 2010**. ([S&P Global](https://www.spglobal.com/energy/en/pricing-benchmarks/our-methodology/subscriber-notes/070610-platts-to-assess-australia-india-bulk-freight?utm_source=chatgpt.com "Platts to assess Australia-India bulk freight | S&P Global"))
    
    [S&P Global Platts freight methodology](https://www.spglobal.com/commodityinsights/PlattsContent/_assets/_files/en/our-methodology/methodology-specifications/global_freight.pdf?utm_source=chatgpt.com)
    
    **Problem:** the historical dataset is commercial/paid, so you generally can't just download the full history for free.
    
2. **Baltic Exchange historical freight data**
    
    Baltic provides dry-bulk assessments for **Capesize, Panamax and Supramax**, including route-level benchmarks. Its API exposes historical market data, and the API documentation is publicly visible. ([Baltic Market Data API](https://api.balticexchange.com/api/swagger/index.html?utm_source=chatgpt.com "Swagger UI"))
    
    [Baltic Exchange API documentation](https://api.balticexchange.com/api/swagger/index.html?utm_source=chatgpt.com)
    
    **Problem:** full historical data access is also generally a paid/licensed service.
    
3. **S&P Global Platts coking-coal + freight relationship**
    
    This is extremely useful for your ML features. Platts explicitly calculates CFR India coking-coal prices using:
    
    **FOB Australia coking coal + Australia→India Panamax freight**
    
    For example:
    
    `CFR India HCC = FOB Australia HCC + CDBFAI0 freight`
    
    ([S&P Global](https://www.spglobal.com/commodity-insights/en/pricing-benchmarks/our-methodology/subscriber-notes/100419-platts-clarifies-methodology-for-metallurgical-coal-cfr-india?utm_source=chatgpt.com "SUBSCRIBER NOTE: Platts clarifies methodology for metallurgical coal CFR India | S&P Global"))
    
    That means you can construct a very meaningful forecasting dataset around **coal price + freight + macro variables**.
    
4. **IEA coal data**
    
    The IEA has historical/annual coal freight information and explains the relationship between freight rates, vessel type, route, fuel costs and supply/demand. It reports, for example, historical Panamax coal freight averages. ([IEA](https://www.iea.org/reports/coal-2025/prices-and-costs?utm_source=chatgpt.com "Prices and costs – Coal 2025 – Analysis - IEA"))
    
    [IEA Coal 2025 — Prices and costs](https://www.iea.org/reports/coal-2025/prices-and-costs?utm_source=chatgpt.com)
    

---

## What I recommend for your SIH dataset

Don't search only for a dataset literally called **"freight forecasting dataset."**

Build a combined dataset like this:

|Date|Freight|Coal price|Bunker|BDI|BPI|Vessel|Route|Congestion|Weather|
|---|--:|--:|--:|--:|--:|---|---|---|---|
|2021-01-01|$/t|$/t|$/t|index|index|Panamax|AUS→IND|%|value|
|2021-01-02|...|...|...|...|...|Panamax|AUS→IND|...|...|
|...||||||||||

Then your model predicts:

**Target**

```text
Freight rate t+7
Freight rate t+14
Freight rate t+30
```

and eventually:

```text
Expected freight
       ↓
Expected charter cost
       ↓
Optimal vessel
       ↓
Optimal booking date
       ↓
BUY / WAIT / CONTRACT
```

### The most valuable variable

For PS-26006 specifically, I would make **CDBFAI0 — Australia → India Panamax freight** your primary target if you can obtain the historical series. Platts explicitly designed this assessment around the coking-coal trade from East Australia to Indian ports including **Paradip, Visakhapatnam and Haldia**. ([S&P Global](https://www.spglobal.com/energy/en/pricing-benchmarks/our-methodology/subscriber-notes/070610-platts-to-assess-australia-india-bulk-freight?utm_source=chatgpt.com "Platts to assess Australia-India bulk freight | S&P Global"))

And there's evidence that this series is still being published: S&P reported the Australia–East Coast India Panamax rate averaging **$25.18/t in March 2026**, compared with $14.54/t in March 2025. ([S&P Global](https://www.spglobal.com/energy/en/news-research/latest-news/coal/042426-trade-review-asian-met-coal-market-sees-pricing-support-in-q2-from-middle-east-war?utm_source=chatgpt.com "TRADE REVIEW: Asian met coal market sees pricing support in Q2 from Middle East war | S&P Global"))

### If you need **free downloadable data**

I can next search specifically for **CSV/Excel/API datasets that are free**, including GitHub, Kaggle, FRED, World Bank, UN Comtrade, AIS datasets, and historical Baltic/coal-rate mirrors, and assemble a **ready-to-use SIH 26006 dataset plan with download links + columns + date coverage**.







Bilkul. **Deep learning introduce mat karo** agar SIH 26006 ke liye basic + reliably working solution banana hai. Hackathon mein **working end-to-end prototype > unnecessarily complex ML**.

Main tumhare project ko **MVP-first** rakhoonga:

### 1. God's Eye View → sirf frontend + ship tracking

Use it as inspiration/base for:

- 🌍 Interactive map / 3D globe
    
- 🚢 Ships display
    
- 📍 Live/current position
    
- 🧭 Heading + speed
    
- ⚓ Ports
    
- 🛣️ Route visualization
    
- 📋 Ship details panel
    

**Prediction God’s Eye View se nahi karni.**

### 2. Backend

Simple **FastAPI/Node.js** backend:

```text
AIS / historical ship data
          ↓
       Backend
          ↓
       REST API
          ↓
      Frontend map
```

Initially live AIS na mile toh **historical/simulated AIS data replay** karo. Demo ke liye ye safer hai.

### 3. Freight forecasting — Deep Learning nahi

Start with:

**Linear Regression / Random Forest / XGBoost**

Mera preference:

> **XGBoost**

because tabular forecasting features ke liye simple, fast aur explainable enough hai.

Features:

```text
Previous freight rate
7-day average
14-day average
30-day average
BDI/BPI
Fuel/bunker price
Coal price
Season/month
Port congestion
Vessel availability
Route
```

Output:

```text
7-day forecast
30-day forecast
```

Example:

```text
Current freight:     $18.4/t

7-day forecast:      $19.1/t ↑
30-day forecast:     $21.3/t ↑

Confidence:          78%
```

### 4. Optimization bhi simple rakho

Deep RL ki **bilkul zarurat nahi**.

Rule-based + scoring approach:

```text
                    ┌── Freight forecast
                    ├── Vessel cost
                    ├── Vessel availability
                    ├── Port constraints
                    ├── ETA
                    └── Congestion
                              ↓
                         Score vessels
                              ↓
                     Best vessel + timing
```

For example:

```text
              Vessel A     Vessel B     Vessel C
Freight          8            9            7
ETA              9            7            8
Draft             9            8            6
Cost              8            7            9
Congestion        8            6            8
                 ─────────────────────────────
Total            42           37           38

             ★ RECOMMEND VESSEL A
```

### 5. Final dashboard

Tumhara actual SIH demo kuch aisa hona chahiye:

```text
┌─────────────────────────────────────────────────┐
│              COKING COAL COMMAND CENTER         │
├───────────────────────────┬─────────────────────┤
│                           │ FREIGHT FORECAST     │
│                           │                     │
│       🌍 MAP              │ Current  $18.4/t    │
│                           │ 7D       $19.1/t ↑  │
│   🚢  🚢                  │ 30D      $21.3/t ↑  │
│        🚢                 │                     │
│             ⚓ Paradip    │ Recommendation      │
│                           │ WAIT / BOOK         │
│                           │                     │
├───────────────────────────┼─────────────────────┤
│ SELECTED VESSEL           │ OPTIMAL CHARTER     │
│                           │                     │
│ Panamax                   │ Vessel: MV ABC      │
│ 76,000 DWT                │ Cost: $X             │
│ Australia → Paradip       │ ETA: XX Sep         │
│ ETA: XX Sep               │ Score: 92/100       │
└───────────────────────────┴─────────────────────┘
```

## Mere hisaab se tumhara tech stack

**Frontend**

- React
    
- CesiumJS
    
- God's Eye View se relevant visualization ideas/code patterns
    

**Backend**

- Python
    
- FastAPI
    
- PostgreSQL/PostGIS if needed
    

**ML**

- Pandas
    
- Scikit-learn
    
- XGBoost
    

**Data**

- AIS
    
- Historical freight
    
- Port information
    
- BDI/BPI
    
- Fuel/coal prices
    

**No:**

- ❌ LSTM
    
- ❌ Transformer
    
- ❌ Reinforcement Learning
    
- ❌ Computer Vision
    
- ❌ complicated multi-agent AI
    

### Sabse important

Tumhara **core innovation AI model nahi**, balki:

> **Vessel tracking + freight forecasting + vessel/route constraints + charter recommendation ko ek single operational dashboard mein combine karna.**

Ye SIH judges ko demo mein **directly visible value** dega.

Agar tum chaho, main ab **PS-26006 ka complete MVP architecture** bana deta hoon — **exact folders, APIs, database tables, ML model, God’s Eye View/Cesium integration, aur 7-day implementation plan** ke saath.


Detailed and Systemic Explanation From Scratch for the core Engines without stack heavy implementation [[Core Idea and Planning Base]]

[[Initial Frontend Discussion and Devops Flow]]