# Buyer's / Renter's Agent Enhancement Task Plan

Goal: turn Property Hunter from a polished listing finder into a decision engine that behaves like a serious buyer's agent or renter's agent: find stock, filter hard, expose risk, price the asset, and tell the user exactly what to do next.

## Product Principle

A good agent does not just show properties. They reduce decision risk.

Every shortlisted property should answer five questions:

- Is this worth my attention?
- Is the price or rent fair?
- What risks still need checking?
- What is the next best action?
- How confident are we, and why?

## Phase 1 - Due Diligence Layer

Purpose: make every shortlist feel legally and practically useful, not just attractive.

### Implementation Checklist

- [x] Add a `due_diligence.py` module.
- [x] Define a `DueDiligenceItem` structure with:
  - [x] `category`
  - [x] `status`
  - [x] `severity`
  - [x] `reason`
  - [x] `recommended_action`
  - [x] `source`
- [x] Add buyer-specific checks:
  - [x] Contract review required.
  - [x] Strata report required for apartments.
  - [x] Building and pest required for houses/townhouses.
  - [x] Auction vs private treaty risk.
  - [x] Cooling-off availability.
  - [x] Finance pre-approval fit.
  - [x] Stamp duty and total cash requirement estimate.
  - [ ] Known overlays where data is available.
- [x] Add renter-specific checks:
  - [x] Bond and upfront cash estimate.
  - [x] Lease term suitability.
  - [x] Pet suitability.
  - [x] Aircon/heating check.
  - [x] Parking and storage check.
  - [x] Application readiness.
  - [x] Inspection timing urgency.
- [x] Add missing-data handling:
  - [x] Flag unknown strata fees.
  - [x] Flag missing floorplan.
  - [x] Flag missing inspection time.
  - [x] Flag unclear price/rent guide.
  - [x] Flag missing agent contact details.
- [x] Persist due-diligence output in SQLite or generate deterministically at report time.
- [x] Add due-diligence summary to the Telegram digest.
- [x] Add a "Due Diligence" panel to each folio property spread.

### Acceptance Criteria

- [x] Each shortlisted property has a visible checklist of unresolved checks.
- [x] Critical issues are impossible to miss.
- [x] Unknown data is treated as a risk, not silently ignored.
- [x] Buyer and renter modes produce different checklists.

## Phase 2 - Valuation And Rental Fairness Engine

Purpose: stop relying on asking prices and provide a defensible view of fair value.

### Implementation Checklist

- [x] Add a `valuation.py` module.
- [x] Use stored sold comparables from `sales_report.py` and `PropertyDB`.
- [x] Build comparable selection rules:
  - [x] Same suburb preferred.
  - [x] Nearby suburbs allowed when local evidence is thin.
  - [x] Same property type.
  - [x] Same bedroom count or adjacent bedroom count.
  - [x] Recent sales weighted higher.
  - [x] Exclude obvious non-comparables where possible.
- [x] Calculate buyer valuation signals:
  - [x] Comparable sale range.
  - [x] Median comparable sale.
  - [x] Price per bedroom.
  - [x] Asking price vs comparable range.
  - [x] Confidence score based on evidence depth.
  - [x] Suggested negotiation posture.
- [x] Calculate renter valuation signals:
  - [x] Comparable rental range.
  - [x] Rent per bedroom.
  - [x] Rent vs suburb median.
  - [x] Weekly affordability against budget.
  - [x] Annual rent burden estimate.
  - [x] Application urgency based on rent attractiveness and market tightness.
- [ ] Add price-history handling:
  - [x] Detect price drops.
  - [ ] Detect relists.
  - [ ] Detect underquoting risk where guide is far below comps.
  - [x] Detect stale listings.
- [x] Add valuation fields to the folio payload schema.
- [x] Add valuation summary to digest output.

### Acceptance Criteria

- [x] Every shortlist item says "cheap / fair / stretched / overpriced" with evidence.
- [x] The system cites actual comparable evidence from the local database.
- [x] Confidence is explicit when comps are thin.
- [x] Buyer and renter valuation logic are separate.

## Phase 3 - Risk Scoring And Red-Flag Detection

Purpose: catch the things an excited buyer or renter misses.

### Implementation Checklist

- [x] Add a `risk.py` module.
- [x] Scan description, features, floorplan, title, and known metadata for risk signals.
- [ ] Add apartment risk checks:
  - [x] Ground floor.
  - [x] Main road.
  - [ ] No lift where relevant.
  - [x] No parking where parking is required.
  - [x] Dark/internal outlook wording.
  - [ ] Tiny floorplan or unclear internal area.
  - [ ] High-density oversupply suburb signal.
  - [x] Strata-fee unknown.
- [ ] Add house/townhouse risk checks:
  - [x] Flood/bushfire/heritage/contamination overlay where data is available.
  - [x] Major renovation wording.
  - [x] Structural concern wording.
  - [ ] Easement or access issue wording.
- [ ] Add renter risk checks:
  - [x] No aircon/heating.
  - [x] Shared laundry.
  - [x] No pets.
  - [x] Short lease.
  - [ ] Poor parking/storage.
  - [x] Unclear availability date.
- [ ] Add risk severity levels:
  - [x] `dealbreaker`
  - [x] `major`
  - [x] `minor`
  - [x] `watch`
- [x] Feed risk penalties into `viability.py`.
- [x] Show the top 1-3 risks in the digest.
- [x] Show full risks in the folio.

### Acceptance Criteria

- [x] A pretty property with a major risk no longer scores as a simple top pick.
- [x] Risk output distinguishes known risks from unknowns.
- [x] Deal-breakers can hard-reject a listing.

## Phase 4 - Action Plan And Workflow

Purpose: make the agent operational. The user should know what to do next.

### Implementation Checklist

- [x] Add an `action_plan.py` module.
- [x] Define action types:
  - [x] Inspect.
  - [x] Contact agent.
  - [x] Request contract.
  - [x] Request strata report.
  - [x] Run comparable review.
  - [x] Ask property manager a renter question.
  - [x] Prepare application.
  - [x] Pass.
- [x] Generate per-property next steps:
  - [x] Best next action.
  - [x] Deadline or urgency.
  - [x] Message template for agent/property manager.
  - [x] Questions to ask at inspection.
  - [x] Documents to prepare.
- [x] Add inspection planning:
  - [x] Sort inspections by time.
  - [x] Group by suburb/route where possible.
  - [x] Flag clashes.
  - [x] Build Saturday inspection run.
- [x] Add digest section: "What I would do next".
- [x] Add folio section: "Action Plan".

### Acceptance Criteria

- [x] The report ends with clear actions, not just commentary.
- [x] Each top property has a suggested next step.
- [x] The system can produce an inspection-ready Saturday shortlist.

## Phase 5 - Agent Intelligence And Market Memory

Purpose: make the system smarter over time and harder to fool.

### Implementation Checklist

- [x] Extend `agents` storage with observable performance fields:
  - [x] Listings seen.
  - [x] Listings sold/leased where known.
  - [x] Average guide vs sold result where available.
  - [x] Price drops observed.
  - [x] Repeated underquote signals.
- [x] Add `agent_report.py`.
- [ ] Add agency-level notes where enough evidence exists.
- [x] Track property lifecycle:
  - [x] First seen date.
  - [x] Price guide changes.
  - [x] Inspection changes.
  - [x] Under offer.
  - [x] Sold/leased.
  - [x] Withdrawn or stale.
- [x] Add "changed since last seen" to the daily digest.
- [x] Add "why now" reasoning for resurfaced listings.

### Acceptance Criteria

- [x] The agent can explain why a listing is newly interesting.
- [x] Price changes and status changes are surfaced before new-stock noise.
- [x] Agent behaviour is evidence-based, not vibes.

## Phase 6 - Data Source Expansion

Purpose: improve validity by reducing single-source dependence.

### Implementation Checklist

- [x] Add source abstraction around listing providers.
- [x] Keep Domain as the first provider.
- [ ] Add optional provider candidates:
  - [ ] realestate.com.au search/listing parser if feasible.
  - [ ] NSW planning/property constraints sources where feasible.
  - [ ] ABS / suburb demographic indicators where useful.
  - [x] RBA and inflation source fetch for market section.
  - [ ] Major bank / Domain / CoreLogic-Cotality forecast fetches.
- [x] Store source attribution for external facts.
- [x] Add freshness dates to market claims.
- [x] Fail closed when market data is stale.

### Acceptance Criteria

- [x] Market claims cite their source and date.
- [x] Valuation is not solely based on current asking prices.
- [x] The folio makes stale data visible.

## Phase 7 - Report And UX Upgrade

Purpose: make the output feel like advice, not scraped content.

### Implementation Checklist

- [x] Update the daily Telegram digest format:
  - [x] Standout pick first.
  - [x] New/changed/stale sections.
  - [x] Top 3 properties only unless there is a strong reason.
  - [x] Price/rent fairness label.
  - [x] Top risk.
  - [x] Next action.
- [x] Update the Saturday folio:
  - [x] Executive recommendation page.
  - [x] Due diligence panel per property.
  - [x] Valuation panel per property.
  - [x] Risk panel per property.
  - [x] Action plan panel per property.
  - [x] Inspection route page.
  - [x] Final ranked shortlist.
- [x] Add confidence labels:
  - [x] High confidence.
  - [x] Medium confidence.
  - [x] Low confidence.
- [x] Add "why this might be wrong" note for uncertain recommendations.

### Acceptance Criteria

- [x] A user can decide what to inspect from the Telegram digest alone.
- [x] A user can make a weekend plan from the folio.
- [x] The folio reads like a buyer's-agent dossier, not a listing brochure.

## Phase 8 - Tests And Validation

Purpose: keep the agent credible as logic gets more opinionated.

### Implementation Checklist

- [x] Add unit tests for:
  - [x] Due-diligence generation.
  - [x] Comparable selection.
  - [x] Valuation labels.
  - [x] Risk extraction.
  - [x] Action-plan generation.
  - [x] Buyer vs renter mode differences.
- [x] Add fixture listings:
  - [x] Strong buyer candidate.
  - [x] Strong renter candidate.
  - [x] Attractive but risky apartment.
  - [x] Overpriced property.
  - [x] Underquoted property.
  - [x] Missing-data property.
- [x] Add golden-output tests for digest snippets.
- [x] Add smoke test for folio payload generation.
- [x] Add DB migration tests for any new tables.

### Acceptance Criteria

- [x] New scoring logic is testable without hitting Domain.
- [x] Missing data does not crash report generation.
- [x] Buyer and renter advice remain separated.

## Suggested Build Order

1. Due diligence layer.
2. Valuation/rental fairness engine.
3. Risk scoring.
4. Action plan output.
5. Digest and folio integration.
6. Agent intelligence and lifecycle tracking.
7. Data source expansion.
8. Test hardening.

This order is deliberate: due diligence plus valuation is the biggest jump in trust and commercial value. The folio already looks premium; the next upgrade is making it feel safe to use for a million-dollar decision.
