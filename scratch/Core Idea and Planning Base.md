
The core idea:

> **Forecast future bulk-freight/charter costs and cargo requirements → combine them with vessel availability, port constraints, and procurement requirements → recommend when/how much cargo to procure and what vessel chartering strategy to use.**

### Recommended high-level architecture

```text
                    ┌──────────────────────────┐
                    │     DATA SOURCES         │
                    ├──────────────────────────┤
                    │ Historical Freight Rates │
                    │ Vessel/Charter Data      │
                    │ Commodity Prices         │
                    │ Port/Shipping Data       │
                    │ Weather / Ocean Data     │
                    │ Fuel / Bunker Prices      │
                    │ FX Rates                 │
                    │ Trade / Import Data      │
                    │ Calendar / Seasonality   │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │     DATA INGESTION       │
                    │   ETL / Data Pipelines    │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │   DATA STORAGE LAYER     │
                    │                          │
                    │ Raw Data                 │
                    │ Cleaned Data             │
                    │ Feature Store            │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │    FEATURE ENGINEERING   │
                    ├──────────────────────────┤
                    │ Lag Features             │
                    │ Moving Averages          │
                    │ Seasonality              │
                    │ Port Congestion          │
                    │ Fuel/FX effects          │
                    │ Commodity-Freight Links  │
                    └────────────┬─────────────┘
                                 │
               ┌─────────────────┼─────────────────┐
               ▼                 ▼                 ▼
      ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
      │ Freight        │ │ Cargo Demand   │ │ Vessel         │
      │ Forecast Model │ │ Forecast Model │ │ Availability   │
      │                │ │                │ │ Model          │
      └───────┬────────┘ └───────┬────────┘ └───────┬────────┘
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │     OPTIMIZATION ENGINE  │
                    ├──────────────────────────┤
                    │ Vessel Selection         │
                    │ Charter Timing           │
                    │ Cargo Procurement        │
                    │ Port Selection            │
                    │ Route Selection           │
                    │ Cost Minimization        │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │   DECISION / RECOMMENDATION │
                    ├──────────────────────────┤
                    │ Recommended Vessel       │
                    │ Recommended Charter Date │
                    │ Cargo Quantity           │
                    │ Expected Freight Cost    │
                    │ Expected Total Cost      │
                    │ Risk Score               │
                    │ Confidence Interval      │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │       WEB DASHBOARD       │
                    ├──────────────────────────┤
                    │ Forecast Charts          │
                    │ Vessel Dashboard         │
                    │ Procurement Planner       │
                    │ What-if Simulation        │
                    │ Alerts                   │
                    │ Recommendation Panel     │
                    └──────────────────────────┘
```

## 1. First, define exactly what your system is predicting

This is probably the **most important architectural decision**.

Your title contains three related problems:

**A. Freight forecasting**

For example:

```text
Route: Australia → Paradip
Commodity: Coal
Vessel: Panamax
Forecast horizon: 30 days

Predicted freight:
$X / tonne
```

or possibly:

```text
Predicted time-charter equivalent / voyage cost
```

**B. Cargo procurement forecasting**

Example:

```text
Next 30 days
Expected requirement = 2.4 million tonnes
```

**C. Vessel chartering optimization**

Example:

```text
Need: 75,000 tonnes
Port: Paradip
Laycan: Oct 15–20

Candidate vessels:
V1 → $X
V2 → $Y
V3 → $Z

Recommendation → V2
```

Your **ML model shouldn't directly output "charter Vessel X."**

Instead:

```text
Forecasting
       ↓
Predicted future market conditions
       ↓
Optimization
       ↓
Best operational decision
```

That separation will make the architecture much stronger.

---

# 2. I would divide the project into 5 major engines

### Engine 1 — [[Freight Forecasting]]


This is the ML component.

Input:

```text
Historical freight rate
Fuel prices
Commodity price
Exchange rate
Port congestion
Vessel supply
Demand indicators
Season
Weather
Trade volumes
```

Output:

```text
Freight rate forecast
+ confidence interval
+ forecast horizon
```

For example:

```text
Current freight       $27.4/t

7-day forecast        $28.1/t
30-day forecast       $30.2/t
60-day forecast       $32.7/t
```

### Engine 2 — [[Cargo Demand Forecasting]]

Predict how much cargo will be required.

```text
Historical imports
Historical consumption
Inventory
Production
Seasonality
Demand trends
```

Output:

```text
Expected requirement
--------------------
Week 1       420 kt
Week 2       510 kt
Week 3       470 kt
Week 4       530 kt
```

This becomes an input to procurement optimization.

---

# 3. Engine 3 — [[Vessel Availability]]

You need a representation of the vessel market.

For every vessel:

```text
Vessel ID
DWT
Vessel type
Open date
Open location
Current location
Destination
Fuel consumption
Draft
Speed
Charter rate
Estimated freight
```

Then calculate things like:

```text
Distance to loading port
ETA
Available laycan
Deadweight utilization
Estimated bunker consumption
Port compatibility
```

This lets the system answer:

> “Which vessels can actually perform this shipment?”

rather than merely:

> “Which vessel is cheapest?”

---

# 4. Engine 4 — [[Optimization Engine]]

This is where your project becomes **much more interesting**.

Suppose:

```text
Cargo requirement = 200,000 tonnes

Vessel A = 75k DWT
Vessel B = 80k DWT
Vessel C = 100k DWT
```

The system shouldn't simply select the cheapest vessel.

Your objective function could be something like:

min⁡TotalCost\min TotalCost

where

TotalCost=FreightCost+BunkerCost+PortCost+DemurrageRisk+DelayCost+ProcurementCostTotalCost = FreightCost + BunkerCost + PortCost + DemurrageRisk + DelayCost + ProcurementCost

Subject to:

```text
Cargo requirement satisfied
Vessel capacity constraint
Port draft limitation
Laycan constraint
Vessel availability
Maximum number of charters
Delivery deadline
```

This can be formulated as:

### Mixed Integer Linear Programming

or

### Constraint Optimization

For the prototype, **OR-Tools** would be a very good choice.

---

# 5. The really interesting part: "What should we do?" [[Combined Engine Work]]

Your dashboard should not merely show:

> Freight price predicted = ₹X

Instead show:

### Recommendation

```text
BUY / CHARTER NOW

Expected freight in 30 days:
₹2,480/t

Current:
₹2,290/t

Expected increase:
8.3%

Recommended procurement:
150,000 tonnes

Recommended vessel:
Panamax 82,000 DWT

Recommended laycan:
14–18 October

Estimated savings:
₹28.5 lakh

Risk:
Medium
```

That is a much better **SIH demo**.

---

# 6. Architecture I'd actually recommend for your team

Don't build a giant microservices architecture.

For SIH, I'd use a **modular monolith** initially:

```text
                 FRONTEND
                    │
                    ▼
              FastAPI Backend
                    │
       ┌────────────┼────────────┐
       │            │            │
       ▼            ▼            ▼
 Forecasting   Optimization   Simulation
   Module         Module        Module
       │            │            │
       └────────────┼────────────┘
                    ▼
              PostgreSQL
                    │
              Object Storage
```

And internally:

```text
backend/
│
├── api/
│   ├── forecast.py
│   ├── vessel.py
│   ├── procurement.py
│   └── optimization.py
│
├── models/
│   ├── freight_model.py
│   ├── demand_model.py
│   └── vessel_model.py
│
├── optimization/
│   ├── charter_optimizer.py
│   ├── procurement_optimizer.py
│   └── constraints.py
│
├── features/
│   ├── freight_features.py
│   ├── demand_features.py
│   └── vessel_features.py
│
├── data/
│   ├── ingestion/
│   ├── cleaning/
│   └── validation/
│
└── simulation/
    └── what_if.py
```

Frontend:

```text
React / Next.js
        │
        ├── Market Overview
        ├── Freight Forecast
        ├── Cargo Forecast
        ├── Vessel Search
        ├── Procurement Planner
        ├── Charter Optimizer
        └── What-if Simulator
```

---

# 7. ML architecture

I **wouldn't jump directly into LSTM/Transformer**.

Start with a baseline pipeline:

```text
                 Historical Data
                       │
                       ▼
                Feature Engineering
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
         Linear      XGBoost    Random Forest
         Baseline
            │          │          │
            └──────────┼──────────┘
                       ▼
                  Model Selection
                       │
                       ▼
                Production Model
```

For a tabular/time-series-heavy problem, models like **XGBoost/LightGBM/CatBoost** can be extremely competitive and much easier to explain than an LSTM.

Then, if your data actually justifies it, introduce:

```text
Temporal Fusion Transformer
        /
LSTM
        /
N-BEATS
```

as advanced models.

The key is that your presentation should demonstrate:

> **Baseline → Advanced model → measurable improvement**

rather than:

> "We used a Transformer because AI."

---

# 8. Add uncertainty forecasting

This would be a **very good differentiator**.

Don't output:

```text
Freight = ₹2,500
```

Output:

```text
Expected freight      ₹2,500/t

80% interval:
₹2,380 – ₹2,640

Risk:
Medium
```

Then the optimization engine can consider the uncertainty.

For example:

```text
Expected Case
Optimistic Case
Pessimistic Case
```

This gives you a proper **risk-aware chartering system**.

---

# 9. Build a digital "shipping twin"

A very cool SIH feature would be a simplified simulation layer.

User enters:

```text
Commodity: Coal
Quantity: 180,000 t
Origin: Australia
Destination: Paradip
Required by: Nov 15

Fuel price: +10%
Freight market: +15%
Port congestion: High
```

System simulates:

```text
Scenario A
Charter now
→ ₹X

Scenario B
Charter after 15 days
→ ₹Y

Scenario C
Split cargo across two vessels
→ ₹Z
```

Then:

```text
BEST OPTION
Scenario B

Expected savings: ₹XX crore
Risk: Low
```

That makes the demo extremely visual.

---

# 10. Data architecture

I'd structure your data into four layers.

### Raw

```text
raw_freight.csv
raw_vessel.csv
raw_port.csv
raw_weather.csv
raw_fuel.csv
```

### Clean

```text
freight_daily
vessel_positions
port_conditions
commodity_prices
```

### Feature

```text
freight_features
demand_features
vessel_features
```

### Prediction

```text
freight_forecasts
demand_forecasts
optimization_results
```

For an SIH prototype:

**PostgreSQL + Parquet** is plenty.

You don't need Kafka, Spark, Kubernetes, etc. unless your requirements genuinely demand them.

---

# 11. One important architecture decision: routes

Don't initially try to model **all international shipping routes**.

Pick perhaps **2–4 important East Coast corridors**.

For example:

```text
Australia → Paradip
Indonesia → Paradip
Australia → Visakhapatnam
Indonesia → Krishnapatnam
```

Then build the architecture so additional routes can be added later.

That makes your demo realistic while keeping the scope manageable.

---

# 12. Recommended technology stack

I'd use:

|Layer|Technology|
|---|---|
|Frontend|React / Next.js|
|Backend|Python + FastAPI|
|ML|Pandas + XGBoost/LightGBM + PyTorch later|
|Optimization|OR-Tools|
|Database|PostgreSQL|
|Data files|Parquet|
|Visualization|Plotly|
|Maps|Mapbox / Leaflet|
|Model tracking|MLflow|
|Deployment|Docker|
|API communication|REST|

For SIH, this is more than sufficient.

---

# 13. Most importantly: build it in phases

Don't let the team start by building everything simultaneously.

### Phase 1 — MVP

Build:

```text
Historical freight data
       ↓
Freight forecasting
       ↓
Dashboard
```

Get one reliable prediction pipeline working.

### Phase 2

Add:

```text
Cargo demand forecasting
```

### Phase 3

Add:

```text
Vessel database
+
vessel filtering
```

### Phase 4

Add:

```text
Optimization
```

Now:

```text
Forecast
   ↓
Optimize
   ↓
Recommendation
```

### Phase 5

Add:

```text
What-if simulation
+
uncertainty
+
risk scoring
```

### Phase 6

Polish:

```text
Dashboard
Maps
Alerts
Explainability
Deployment
```

---

# 14. Your final system should ultimately look like this

```text
                 ┌─────────────────────┐
                 │   External Data      │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │ Data Ingestion      │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │ Data Lake / DB      │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │ Feature Engineering │
                 └──────────┬──────────┘
                            ↓
       ┌────────────────────┼────────────────────┐
       ↓                    ↓                    ↓
Freight Forecast      Cargo Forecast      Vessel Analytics
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ↓
                 ┌─────────────────────┐
                 │ Optimization Engine │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │ Risk / What-if      │
                 │ Simulation          │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │ Recommendation      │
                 │ Engine              │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │ SIH Dashboard       │
                 └─────────────────────┘
```


I'd pitch the project as:

**Not:**

> “An ML model that predicts freight rates.”

**But:**

> **“An intelligent maritime procurement and chartering decision-support platform that uses freight and cargo demand forecasts, vessel availability, operational constraints, and risk-aware optimization to recommend the most economical chartering and procurement strategy.”**

That distinction is important because it maps directly from **forecasting → decision → business value**.