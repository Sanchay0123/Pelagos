**We should design the project so OpenAI is not a dependency at all.** The God's Eye repo has an OpenAI Realtime voice feature, but that is optional; the core globe/data visualization does not require OpenAI. Its own README describes it as an open-source 3D visualization client, and its package is MIT-licensed. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/README.md?utm_source=chatgpt.com "gods-eye-view/README.md at main · bilawalsidhu/gods-eye-view · GitHub"))

### My recommendation

**Fork it → strip unnecessary features → add your maritime intelligence layer.**

Not:

> Rewrite frontend from scratch ❌

Not:

> Keep every God's Eye feature and somehow bend it into SIH ❌

But:

> **God's Eye = rendering/visualization foundation + your own application logic = SIH product** ✅

---

# Why I think this is feasible

I looked at the actual repository rather than just the screenshots.

The important thing is that it is **not some gigantic React application where we'd have to understand an enormous framework before touching it.**

The repository itself says:

> Vanilla JS + CesiumJS + Vite

and explicitly separates:

```text
src/ui.js
     ↓
UI / HUD

src/data/
     ↓
individual data layers
```

The maintainers actually encourage extending the data-layer architecture. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/CONTRIBUTING.md?utm_source=chatgpt.com "gods-eye-view/CONTRIBUTING.md at main · bilawalsidhu/gods-eye-view · GitHub"))

That's **excellent for us**.

We can essentially treat it as a prebuilt **3D command-center framework**.

---

# What we'd keep

I'd keep the things that would take us ages to reproduce:

### 1. Cesium globe

Absolutely keep.

### 2. Camera/navigation

Keep.

### 3. 3D rendering pipeline

Keep.

### 4. HUD / visual language

Keep and modify.

### 5. Layer system

**Very important.**

We'll use the existing concept to create:

```text
MARITIME LAYERS

☑ Vessels
☑ Routes
☑ Ports
☑ Weather
☑ Freight corridors
☑ Cargo origins
☑ Risk zones
☑ Congestion
☑ Forecast overlays
```

### 6. Entity selection

Keep.

Clicking a vessel should open our own vessel intelligence panel.

---

# What we'd remove

This is where we save time.

We don't need:

```text
Aircraft tracking
Satellites
Earthquakes
CCTV
Traffic
Bikeshare
```

unless one of them somehow becomes relevant.

The repo currently supports a lot of these layers, including aircraft, ships, satellites, earthquakes, CCTV, traffic, etc. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/README.md?utm_source=chatgpt.com "gods-eye-view/README.md at main · bilawalsidhu/gods-eye-view · GitHub"))

For SIH:

**delete/hide 70% of that stuff.**

The application becomes much more focused.

---

# What we'd add

This is where the project becomes ours.

## Maritime layer

```text
                   GLOBAL VIEW

                     🚢
                    /
                   /
          🚢──────/
         /
        /
Australia
        \
         \
          \───────► 🇮🇳 Paradip
                    │
                    ├── Cargo
                    ├── Port
                    ├── Demand
                    └── Forecast
```

---

# Then we make the UI about decisions

Instead of God's Eye's generic intelligence HUD:

```text
VESSEL
POSITION
SPEED
HEADING
```

we create:

```text
VESSEL INTELLIGENCE

MV OCEAN STAR
─────────────────────

82,400 DWT
Panamax

Current Position
12.43°N 82.31°E

ETA Paradip
14 Oct 2026

Cargo Capacity
74,000 t

Estimated Voyage Cost
$2.18M

─────────────────────

CHARTER ANALYSIS

Current market
$17,800/day

30D forecast
$19,240/day ↑ 8.1%

Recommendation
🟢 CHARTER NOW

Expected saving
₹31.4 Lakh
```

**That is the product.**

---

# And here's the trick that saves us enormous time

We don't need to make every screen a fancy frontend.

We can make the **main screen ridiculously good**, and let conventional dashboards handle the analytical parts.

Something like:

```text
                 SIH FREIGHT INTELLIGENCE
┌─────────────────────────────────────────────────────┐
│                                                     │
│  COMMAND      3D MARITIME WORLD          MARKET     │
│  ───────      ─────────────────          ──────     │
│                                                     │
│  OVERVIEW           🚢                    FREIGHT   │
│  VESSELS          🚢   🚢                 $28.42/t  │
│  ROUTES             ╲                      ▲ 4.2%   │
│  CARGO               ╲                             │
│  FORECAST        INDIA EAST COAST        DEMAND     │
│  PROCUREMENT                            2.4 MT     │
│  OPTIMIZER                               ▲ 8.7%    │
│                                                     │
│  ALERTS                                              │
│  ⚠ Paradip congestion                               │
│  ⚠ Freight expected ↑                              │
│                                                     │
└─────────────────────────────────────────────────────┘
```

Then clicking **Forecast** can open a beautiful chart panel.

Clicking **Optimizer** opens:

```text
┌──────────────────────────────────────────────┐
│ CHARTER OPTIMIZER                            │
├──────────────────────────────────────────────┤
│                                              │
│ Cargo requirement       180,000 tonnes       │
│ Destination             Paradip              │
│ Required by             15 Nov                │
│                                              │
│ ──────────────────────────────────────────── │
│                                              │
│ RECOMMENDED STRATEGY                         │
│                                              │
│ Charter 2 × Panamax                         │
│                                              │
│ Expected total cost     ₹XX Cr               │
│ Risk                    LOW                  │
│ Expected saving         ₹XX Lakh             │
│                                              │
│ [ VIEW VESSELS ]   [ RUN SIMULATION ]        │
└──────────────────────────────────────────────┘
```

We can make these panels ourselves **much faster** than creating a 3D globe from scratch.

---

# Now about OpenAI

This is actually a non-issue if we architect correctly.

The repository currently has OpenAI Realtime voice functionality. Its security documentation says the OpenAI API key stays server-side and the browser receives an ephemeral token. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/SECURITY.md?utm_source=chatgpt.com "gods-eye-view/SECURITY.md at main · bilawalsidhu/gods-eye-view · GitHub"))

**We simply don't use that feature.**

Remove:

```text
OpenAI Realtime
Voice agent
AI HUD summary
```

And your project becomes:

```text
                 NO PAID AI REQUIRED

                         │
                         ▼
              ┌────────────────────┐
              │ God's Eye / Cesium │
              └─────────┬──────────┘
                        │
                        ▼
                   Your Backend
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
        Forecast     Optimize     Vessel DB
           │            │            │
           └────────────┼────────────┘
                        ▼
                    Dashboard
```

No OpenAI API.

No monthly AI bill.

---

# In fact, I would go one step further

**Don't use an LLM anywhere in the MVP.**

You don't need one.

Your "intelligence" comes from:

```text
ML forecasting
      +
optimization
      +
rules
      +
simulation
```

That's arguably **more appropriate** for this particular problem.

For example:

```text
IF

forecasted freight ↑ > 7%

AND

current freight < predicted freight

AND

cargo requirement within 45 days

THEN

recommend early charter
```

That's completely deterministic and costs **₹0**.

Later, if you want conversational interaction, we can investigate free/local models such as Ollama, but I wouldn't put that anywhere near the critical path.

---

# There is one cost we DO need to pay attention to

This is actually more important than OpenAI.

**God's Eye View's beautiful photorealistic globe uses Google Photorealistic 3D Tiles.** 
(10k monthly Requests)
The project documentation says the Google Maps API key is a deliberate client-side key and warns that an unrestricted key can result in billing. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/SECURITY.md?utm_source=chatgpt.com "gods-eye-view/SECURITY.md at main · bilawalsidhu/gods-eye-view · GitHub"))

The repo also says you need a Google Maps API key for the Photorealistic 3D Tiles setup. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/CONTRIBUTING.md?utm_source=chatgpt.com "gods-eye-view/CONTRIBUTING.md at main · bilawalsidhu/gods-eye-view · GitHub"))

So:

### OpenAI

**We can completely eliminate it.**

### Google Maps

**We need to investigate the free-tier/quota situation carefully.**

I would **not let the team put someone's personal credit card/API key into the project blindly.**

We can instead decide between:

```text
Option A
Google Photorealistic 3D Tiles
↓
Best visual quality
↓
Potential billing

Option B
Cesium world imagery / terrain
↓
Cheaper/free-oriented setup
↓
Still excellent

Option C
Open/free basemap + Cesium
↓
No Google dependency
↓
Less cinematic
```

The God's Eye documentation itself notes that Cesium ion has a Community plan with eligibility/usage limits, while Google Maps needs restrictions. ([GitHub](https://download.plaud.ai/bilawalsidhu/gods-eye-view/security?utm_source=chatgpt.com "Overview · bilawalsidhu/gods-eye-view · GitHub"))

For an SIH demo, **we should choose based on the actual demo requirements rather than blindly chasing the exact God's Eye screenshot.**

---

# Licensing is also favorable

The source code is **MIT licensed**, so modification/forking is permitted subject to the license conditions. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/LICENSE?utm_source=chatgpt.com "gods-eye-view/LICENSE at main · bilawalsidhu/gods-eye-view · GitHub"))

But there is an important caveat:

**MIT applies to the code, not automatically to all the data/assets bundled with the repo.**

The project explicitly separates third-party datasets/assets from its MIT source license. ([GitHub](https://gh.zkt.ccwu.cc/bilawalsidhu/gods-eye-view/blob/main/DATA_SOURCES.md?utm_source=chatgpt.com "gods-eye-view/DATA_SOURCES.md at main · bilawalsidhu/gods-eye-view · GitHub"))

So for our fork:

```text
God's Eye source code
       ↓
MIT
       ↓
We can modify it

BUT

Third-party data/assets
       ↓
Check their individual licenses
```

This is something we'll document properly in our repo.

---

# So my answer is:

### Can we modify God's Eye enough in the available time?

**Yes — if we're disciplined about what we modify.**

I'd estimate the strategy as:

```text
Don't touch                     Modify heavily

Cesium core                     UI/HUD
Globe rendering                 Data layers
Camera                          Vessel entities
3D infrastructure               Maritime routes
Performance system              Forecast panels
                                 Procurement panels
                                 Optimizer
```

We're basically **borrowing the hard 3D engineering** and spending our time on the actual SIH problem.

That's exactly what you should do when time is constrained.

---

# And this gives us a very clean project philosophy

I would call the frontend something along the lines of:

> **Maritime Intelligence Command Center**

rather than presenting it as "God's Eye View modified for shipping."

God's Eye is the **foundation**.

Your project is the **product**.

---

## Then DevOps

And I agree with your second point: **once we freeze this frontend direction, we should move to the DevOps pipeline before getting deep into implementation.**

I want us to design:

```text
                    GITHUB
                       │
                       ▼
                GitHub Actions
                       │
          ┌────────────┴────────────┐
          │                         │
       Backend                    Frontend
          │                         │
       Testing                    Testing
       Linting                    Build
       Security                   Build
          │                         │
          └────────────┬────────────┘
                       ▼
                 Docker Images
                       │
                       ▼
                     GHCR
                       │
                       ▼
                Docker Compose
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
   Frontend         Backend        PostgreSQL
       │               │               │
       └───────────────┼───────────────┘
                       │
                 Observability
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          Grafana   Prometheus   Loki
                       │
                       ▼
                  Alerts/Logs
```

And we'll make sure **the DevOps architecture accommodates the God's Eye frontend**, rather than treating it as some separate throwaway demo.

One thing I particularly like about the repo for this purpose is that it already has `npm run build`, tests, and a defined Node version requirement, so we have something concrete to integrate into CI rather than starting from zero. ([GitHub](https://github.com/bilawalsidhu/gods-eye-view/blob/main/package.json?utm_source=chatgpt.com "gods-eye-view/package.json at main · bilawalsidhu/gods-eye-view · GitHub"))

**So I would freeze the decision as:**

> **Fork `bilawalsidhu/gods-eye-view` → retain Cesium + visual infrastructure → remove irrelevant intelligence layers → build a maritime command-center UI → no OpenAI dependency → carefully control/replace paid map dependencies → then design the complete Docker + CI/CD + observability pipeline.**
