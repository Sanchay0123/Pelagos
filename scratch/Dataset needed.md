
### What you actually need

Your vessel data isn't just a list of Indian ships. You need data that can feed this chain:

**Vessel → route → ETA → availability → port constraints → freight cost → optimization decision**

For example:

> Australia → Paradip  
> Coal cargo: 75,000 tonnes  
> Suitable vessel: Panamax  
> Available veessels: X  
> ETA: X  
> Port draft: X  
> Current freight: X  
> 30-day forecast: X  
> **Recommendation: charter now / wait**

So I'd divide your vessel dataset into **4 levels**:

|Data|What you need|Priority|
|---|---|---|
|Vessel identity|IMO, MMSI, name, flag, type|🔴 Essential|
|Vessel characteristics|DWT, GT, LOA, beam, draft, build year|🔴 Essential|
|Movement|lat/lon, speed, heading, destination, ETA|🔴 Essential|
|Historical movement|voyages, port calls, routes|🟠 Very useful|
|Commercial|charter rate, TCE, owner, broker|🔴 Essential for optimizer|
|Availability|open date, location, next port|🔴 Essential|
|Port compatibility|max draft, LOA, berth constraints|🔴 Essential|

### One important thing

**DG Shipping alone won't give you everything your model needs.**

I'd use a combination:

**DGMA/DG Shipping** → Indian vessel/registration information  
**AIS provider** → live + historical vessel movements  
**Port data** → Paradip / Vizag / other East Coast constraints  
**Freight/charter datasets** → market-price component  
**Commodity/import data** → cargo-demand component

Then your own database joins them using **IMO number** wherever possible.

```text
                 IMO NUMBER
                     │
       ┌─────────────┼─────────────┐
       ↓             ↓             ↓
  DG Shipping       AIS        Commercial
  vessel data      data          data
       │             │             │
       └─────────────┼─────────────┘
                     ↓
              VESSEL MASTER
                     │
          ┌──────────┴──────────┐
          ↓                     ↓
     Availability          Route/ETA
          │                     │
          └──────────┬──────────┘
                     ↓
              OPTIMIZATION ENGINE
```

And **this is where your project gets interesting**: you don't necessarily need perfect proprietary data for the SIH prototype. We can build a realistic historical/synthetic commercial layer while using genuine vessel/AIS/port data where it's accessible.

If you want, I can now **inspect the whole uploaded project and design the exact data architecture + tell you specifically where to get each dataset (free vs paid), including AIS, Indian vessels, freight rates, cargo demand, ports, and weather.**