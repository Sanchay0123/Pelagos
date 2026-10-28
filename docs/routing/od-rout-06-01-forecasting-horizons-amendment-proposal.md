---
title: "OD-ROUT-06-01 — Forecast Cadence Reconciliation"
document_id: OD-ROUT-06-01
type: operational_routing
status: approved
version: 1.0.0
created: 2026-10-24
updated: 2026-10-24
tags:
  - routing
  - freight
  - forecasting
  - amendment-proposal
  - open-decision
  - od-rout-06-01
  - d9.5
---

# OD-ROUT-06-01 — Forecast Cadence Reconciliation

**Project:** Pelagos  
**Status:** Approved by Project Owner  
**Decision:** Approved  
**Affected artifacts:** ROUT-006 and `REQ-FR-FRF-001`  
**Implementation authorization:** Not granted

## 1. Decision requested

Approve monthly forecasting as the intended cadence for Pelagos's current monthly freight-rate data scope, using the following candidate horizons:

| Horizon | Intended meaning |
|---|---|
| T+1 | One calendar month ahead |
| T+3 | Three calendar months ahead |
| T+6 | Six calendar months ahead |

This proposal formally reconciles the existing daily-horizon requirement with the temporal resolution of the identified monthly dataset.

The proposed horizons describe intended forecasting targets. They do not guarantee that a forecast can be generated, that sufficient evaluation data exists, or that any model has achieved acceptable predictive performance.

## 2. Rationale

The existing `REQ-FR-FRF-001` requirement identifies daily evaluation horizons of 7, 15, 30 and 60 days. The currently identified USDA AMS AgTransport freight-rate dataset is monthly.

These temporal resolutions are not interchangeable. Monthly observations do not establish the behavior of rates on intervening days, and daily predictions must not be fabricated by treating monthly observations as daily measurements.

The forecasting contract should therefore align with the available observation cadence while preserving the possibility of a future, separately approved daily-data scope.

## 3. Proposed requirements amendment

The following wording is proposed for review as a replacement for the relevant horizon language in `REQ-FR-FRF-001`:

> For freight-rate datasets sampled at monthly cadence, the system SHALL define forecast targets using discrete calendar-month horizons of T+1, T+3 and T+6 as candidate evaluation horizons. The system SHALL NOT represent these candidate horizons as validated or production-available forecasts unless the applicable data-sufficiency and model-validation requirements are satisfied.
>
> Daily forecast horizons SHALL remain outside the monthly-data scope. Daily forecasting may be considered under a separate requirements change when suitable genuine daily observations are available and the corresponding forecasting approach can be evaluated.

The final amendment must preserve all other applicable requirements and acceptance criteria unless a separate change is explicitly approved.

## 4. Temporal semantics requiring specification

Before implementation, the revised specification must define the following terms without conflating them:

- **Observation period:** The calendar month represented by a historical rate observation.
- **Data availability time:** When that observation was actually available to the forecasting system.
- **Forecast execution time:** When the system calculated the prediction.
- **Forecast origin:** The information cutoff used to calculate the prediction, based only on data available at that time.
- **Target period:** The future calendar month for which the rate is predicted.

The specification must prevent future information from entering the forecast and must make the observation period and target period unambiguous.

Publication-lag rules and the handling of forecasts executed during an incompletely observed month remain subject to verification and explicit definition. No publication schedule should be assumed without supporting evidence.

## 5. Forecast validation and availability

Each candidate horizon must be evaluated independently using genuine historical observations and chronological out-of-sample testing.

The revised requirements must preserve the existing forecast lifecycle:

- `unavailable`
- `insufficient-data`
- `eligible`
- `available`

The meaning of each state must remain consistent with the accepted requirements.

A horizon must not be declared `available` solely because the system can calculate a numerical result or because another horizon passed validation. Evidence must support the requested horizon's predictive performance and any required prediction-interval calibration.

The current dataset's length does not, by itself, establish adequate statistical power or reliable interval calibration. Evaluation design must account for the limited number of observations and dependence between overlapping forecast windows.

Exact minimum sample thresholds and validation criteria must follow the accepted requirements or be proposed separately with appropriate justification.

## 6. Compatibility and implementation boundaries

This requirements decision does not authorize:

- New API query parameters or changes to response schemas.
- New HTTP status codes or error-handling behavior.
- Changes to forecasting algorithms or runtime model selection.
- Database migrations or changes to stored data.
- Changes to the user interface.
- Claims that T+3 or T+6 forecasts are currently implemented or validated.

API and client compatibility must be assessed as a separate implementation-planning activity after the requirements amendment is approved.

## 7. Acceptance criteria

The amendment will be ready for acceptance when:

1. The conflict between the existing daily-horizon requirement and the monthly-data scope is explicitly resolved.
2. T+1, T+3 and T+6 are defined as calendar-month horizons.
3. Observation period, data availability, forecast origin, execution time and target period are clearly distinguished.
4. The prohibition on fabricated observations and unsupported daily forecasts is preserved.
5. Existing lifecycle states and model-validation safeguards remain intact.
6. Evaluation criteria do not overstate what the available historical sample can demonstrate.
7. API, UI, data-schema and implementation consequences are documented without prematurely deciding them.
8. The amendment is reviewed and approved through the project's change-control process.

## 8. Deferred questions

The following questions remain open for subsequent design work:

- How should forecast requests be represented in the future API?
- How should the system handle queries when the latest published observation represents the current calendar month?
- What minimum evaluation evidence is sufficient for each horizon?
- How should the API represent different lifecycle states for different horizons, if required?
- What evidence would justify reintroducing daily forecasting in a future scope?

These questions must not silently change the approved cadence decision.

## 9. Requested owner decision

> Approve monthly forecast horizons of T+1, T+3 and T+6 as the intended candidate horizons for Pelagos's current monthly-data scope. Formally reconcile the existing daily-horizon requirement through a controlled specification amendment. Preserve the existing lifecycle and validation safeguards. Treat forecast availability, API behavior, and implementation details as separate matters requiring evidence and appropriate authorization.

**Current disposition:** Formally approved by Project Owner on 2026-10-24. Requirements amendment incorporated into `REQ-FR-FRF-001`, `ARCH-001`, and `ROUT-006`. Implementation authorization for API schemas, database migrations, or production runtime changes remains not granted.

No production code, tests, API schemas or database structures are changed or authorized for change by this decision record.
