---
title: "ADR-0005: Frontend Integration and Basemap Decoupling"
document_id: ADR-0005
type: adr
status: drafting
version: 1.0.0
created: 2026-10-14
updated: 2026-10-14
tags:
  - architecture
  - adr
  - frontend
  - visualization
  - cesiumjs
  - basemap-decoupling
  - gods-eye-view
---

# ADR-0005: Frontend Integration and Basemap Decoupling

## 1. Purpose
This Architecture Decision Record (ADR) establishes the integration strategy for repurposing the God's Eye View repository as the Pelagos 3D command center, and defines the basemap decoupling policy that eliminates mandatory commercial Cesium Ion asset tokens.

## 2. Scope
This decision governs the presentation layer architecture, 3D geospatial runtime configuration, tile provider abstractions, and frontend-to-backend communication boundaries.

## 3. Background and Context
The open-source God's Eye View (GEV) project provides an advanced 3D geospatial viewer built with CesiumJS, Vite, and vanilla JavaScript. It includes sophisticated camera controllers, HUD components, layer management, and entity rendering pipelines.

However, GEV's default configuration relied on commercial Cesium Ion access tokens to stream global imagery and 3D terrain (such as Google Photorealistic 3D Tiles and Cesium World Terrain). This creates two critical challenges for Pelagos:
1. **Commercial Token Dependency:** Mandatory Cesium Ion billing tokens create immediate deployment failure if an operator lacks a commercial Cesium account ([PROJECT_OVERVIEW.md Section 5](../../PROJECT_OVERVIEW.md)).
2. **Maritime Domain Misalignment:** GEV was originally developed as an intelligence and aerial tracking viewer, requiring customization for commercial shipping operations (vessel classification symbology, fairway trajectories, chokepoints, charter analytics panels).

[REQ-NFR-DCT-002](../../requirements/nonfunctional-requirements.md) mandates decoupling the visual command center from proprietary Cesium Ion asset tokens, supporting open raster tile services and offline local caching.

## 4. Requirements and Constraints
* [REQ-FR-SAV-001](../../requirements/functional-requirements.md) mandates an interactive 3D maritime command viewport based on the God's Eye View baseline.
* [REQ-NFR-DCT-002](../../requirements/nonfunctional-requirements.md) mandates imagery and basemap decoupling from proprietary tokens.
* [REQ-NFR-PER-001](../../requirements/nonfunctional-requirements.md) mandates a minimum 30 FPS rendering budget while displaying up to 5,000 active vessel entities.
* [REQ-NFR-SBL-001](../../requirements/nonfunctional-requirements.md) mandates a persistent non-navigation-grade disclaimer overlay.

## 5. Design or Specification (The Decision)

### Decision
We decide to **Retain and Repurpose the God's Eye View Codebase** while implementing an open **Basemap Decoupling Layer**:

1. **Repository Structure:**
   * The existing `gods-eye-view-main/` directory is retained as the presentation layer foundation, served as the `web` container service under Docker Compose ([ADR-0003](./adr-0003-container-orchestration-docker-compose.md)).
2. **Basemap Provider Decoupling:**
   * Configure CesiumJS to use an open, tokenless default imagery provider:
     * Primary Default: OpenStreetMap standard raster tiles (`UrlTemplateImageryProvider` pointing to open tile endpoints) or CartoDB Dark Matter / Positron tiles.
     * Offline Fallback: Local pre-cached Natural Earth low-resolution global raster tiles bundled directly within `public/assets/tiles/` for completely offline environments.
     * Optional Commercial Enhancements: If a user supplies a valid Cesium Ion or Google Photorealistic 3D Tiles key in their local `.env`, the viewer dynamically enables 3D terrain and satellite imagery without hardcoding tokens in source control.
3. **Maritime Adaptation of GEV Components:**
   * **Vessel Ingestion Integration:** Integrate GEV's existing `vesselSnapshot` pipeline (`src/sources/live/vessels.js`) with the Pelagos FastAPI backend endpoints (`/api/v1/vessels/snapshot` and `/api/v1/telemetry/live`).
   * **Symbology & Categorization:** Map vessel deadweight and type codes to distinct maritime class symbology (Capesize, Panamax, Supramax, Handysize, Tanker, Container) ([REQ-FR-SAV-002](../../requirements/functional-requirements.md)).
   * **Trajectory Visualization:** Repurpose GEV's route cinematics to render dual-path trajectories: dashed historical breadcrumbs vs. solid operational fairway routes ([REQ-FR-SAV-003](../../requirements/functional-requirements.md)).
   * **HUD Overhaul:** Repurpose the GEV HUD right-rail and bottom panels into the Pelagos Maritime Command Deck (displaying charter recommendations, freight forecasting distributions, and disruption alerts).
   * **Persistent Safety Banner:** Inject a non-dismissible UI badge declaring: *"FOR STRATEGIC COMMERCIAL PLANNING ONLY — NOT FOR NAVIGATION"* ([REQ-NFR-SBL-001](../../requirements/nonfunctional-requirements.md)).

## 6. Dependencies and Interfaces
* **Frontend Runtime:** CesiumJS 1.114+, Vite 5+, Vanilla ES Modules.
* **Backend Communication:** REST API (JSON) and WebSockets over standard HTTP/HTTPS.

## 7. Failure Modes and Limitations
* **Internet Tile Loading Failure:** If public OSM/CartoDB tile servers rate-limit or fail, the globe could show blank terrain. Mitigated by bundling a lightweight local Natural Earth raster basemap in the local assets directory.
* **GPU Memory Overhead:** High entity counts (5,000+ vessels) can overwhelm low-end integrated GPUs. Mitigated by Cesium `BillboardCollection` entity batching and viewport Level-of-Detail (LOD) distance culling ([REQ-NFR-PER-001](../../requirements/nonfunctional-requirements.md)).

## 8. Security, Privacy and Licensing
* God's Eye View carries an open-source license.
* OpenStreetMap data is licensed under ODbL; CartoDB tiles are CC-BY 4.0; CesiumJS is Apache 2.0. Attribution is displayed in the bottom-right credit bar.

## 9. Testing and Verification
* Automated Puppeteer / headless browser tests verify that the 3D globe initializes successfully and achieves &ge; 30 FPS without supplying a Cesium Ion token.

## 10. Traceability
* **Upstream:** [PROJECT_OVERVIEW.md Section 3, 5](../../PROJECT_OVERVIEW.md).
* **Requirements:** Realizes [REQ-FR-SAV-001](../../requirements/functional-requirements.md) through [SAV-004](../../requirements/functional-requirements.md), [REQ-NFR-DCT-002](../../requirements/nonfunctional-requirements.md), and [REQ-NFR-SBL-001](../../requirements/nonfunctional-requirements.md).
* **Architecture:** Informs [ARCH-001 Section 5.1 and 5.2](../system-architecture.md).

## 11. Alternatives Considered
1. **Replacing GEV with a Custom 2D Leaflet / OpenLayers Interface:**
   * *Rationale for Rejection:* Discards significant existing engineering investment in God's Eye View (camera controls, UI layout, HUD components); 2D maps fail to provide the global situational awareness and orbital perspective required by maritime commercial operators.
2. **Migrating to Mapbox GL JS / Deck.gl:**
   * *Rationale for Rejection:* Mapbox GL requires proprietary billing accounts and API keys, reintroducing the vendor lock-in problem that this decision specifically eliminates.

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-14 | Project Agent | Initial draft of ADR-0005: Frontend Integration and Basemap Decoupling under Milestone D3. Establishes GEV repurposing and Cesium Ion decoupling strategy. Status: drafting. |
