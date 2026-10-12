Good morning everyone.

Our problem statement is **SIH 26006 — Development of an Intelligent Freight Forecasting Model for Optimized Vessel Chartering and Bulk Cargo Procurement from Overseas to the East Coast of India.**

Let me first explain the problem in simple terms.

Industries on the east coast of India depend heavily on bulk commodities such as coal, iron ore, and other raw materials that are transported from overseas.

For these companies, two decisions are extremely important:

**First, when should they procure the cargo?**

And second, **when and how should they charter a vessel to transport that cargo?**

These decisions are not as simple as choosing the cheapest vessel available today.

Freight rates can change significantly because of factors such as fuel prices, commodity demand, vessel availability, port congestion, weather conditions, exchange rates, seasonal patterns, and global shipping activity.

So, if a company charters a vessel too early, it may lock itself into an unnecessarily high cost.

But if it waits too long, freight rates may increase, suitable vessels may no longer be available, and the company may face delays or even shortages.

This creates a very important question:

**Can we predict how freight and cargo requirements are going to change, and use those predictions to make better chartering and procurement decisions?**

That is what our project aims to solve.

---

Our proposed system is not simply a machine-learning model that predicts a number.

We are building an **intelligent maritime decision-support system.**

The system has three major stages.

### First — Forecasting.

We collect historical and relevant external data such as freight rates, fuel prices, commodity prices, vessel availability, port conditions, trade volumes, and seasonal patterns.

We process this data and generate features that can help us understand the behavior of the freight market.

Our forecasting model then predicts future freight rates over different time horizons.

For example, instead of simply saying:

“Today's freight rate is 25 dollars per tonne,”

our system could say:

“Based on current market conditions, the expected freight rate after 30 days is 29 dollars per tonne, with an associated confidence range.”

Similarly, we can forecast the expected cargo requirement.

### Second — Vessel intelligence.

Forecasting the freight market alone is not enough.

A company still needs an actual vessel capable of transporting the required cargo.

So our system maintains information about vessels such as their capacity, vessel type, availability, location, estimated arrival time, fuel consumption, and compatibility with the selected ports.

This allows us to answer questions such as:

**Which vessels are actually capable of fulfilling this requirement?**

and

**What would the expected cost be for each option?**


### Third — Optimization.

This is where our forecasting becomes an actual business decision.

Suppose the company needs 180,000 tonnes of cargo at Paradip within a particular time window.

Our system can evaluate different possibilities.

For example:

Should we charter one large vessel?

Should we split the cargo across two vessels?

Should we charter immediately?

Or should we wait for a better expected freight rate?

The optimization engine considers factors such as freight cost, bunker cost, vessel capacity, port constraints, availability, delivery deadlines, and potential delay or demurrage risk.

It then produces a recommended strategy.

So instead of simply showing:

**“Freight rate will increase.”**

the system can say:

**“Based on the forecast and current vessel availability, chartering within the next seven days is expected to minimize total procurement and transportation cost.”**

That is the key difference between a forecasting system and our proposed decision-support system.

---

Now, one of the major aspects we are focusing on is the **user interface.**

Because this is a maritime problem, we don't want to present everything as a collection of ordinary tables and charts.

We are planning to use a 3D geospatial interface based on the open-source **God's Eye View** project and Cesium.

The idea is to create a **Maritime Intelligence Command Center.**

The user will be able to see the major shipping routes, vessels, ports, and cargo origins on a global map.

For example, a user could select a vessel and immediately see:

its current location, deadweight capacity, estimated arrival, expected voyage cost, and its compatibility with a particular procurement requirement.

We can then overlay our own forecasting and optimization information on top of this geographical interface.

So the globe isn't just visual decoration.

It becomes the interface through which the user understands the entire supply chain.

---

Another important feature we plan to include is **what-if analysis.**

For example, a user could change assumptions such as:

fuel prices increasing by 10 percent,

freight rates increasing by 15 percent,

higher port congestion,

or a change in cargo requirement.

The system can then simulate different strategies and show how the expected cost and risk change.

This allows a procurement manager to evaluate decisions before actually committing to a vessel or cargo purchase.

---

Finally, we also want the project to be deployable as an actual software system rather than just a notebook containing an ML model.

Our planned architecture will therefore include a backend API, database, forecasting and optimization services, a containerized deployment, automated CI/CD, logging, monitoring, and health checks.

We are also deliberately avoiding a dependency on paid AI APIs.

Our core intelligence comes from our own forecasting models, optimization algorithms, and deterministic business rules.

---

So, in one sentence, our project can be summarized as:

**We are building an intelligent maritime decision-support platform that forecasts freight and cargo demand, analyzes vessel availability, and recommends when and how to procure cargo and charter vessels at minimum expected cost and risk.**

The ultimate goal is to move from:

**“What is the freight rate today?”**

to:

**“Given what we know about the market today, what is the best decision we should make?”**

Thank you.