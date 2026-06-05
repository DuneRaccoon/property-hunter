#!/usr/bin/env python3
"""Editorial PDF report builder for Property Hunter.

This is the *deliverable*. The scraping/DB stack finds and stores listings; this
module turns a curated shortlist into a premium, magazine-style PDF dossier that
Ben actually wants to read — the kind of thing a high-end buyer's agent would
hand a client, not a spreadsheet dump.

Division of labour (the same deterministic-vs-judgement split as the rest of the
project):

- **Deterministic (here):** layout, typography, image placement, financial
  arithmetic helpers, rendering HTML -> PDF via the Chromium that Playwright
  already ships. No new dependencies.
- **Judgement (the buyer's agent / LLM, at runtime):** the prose. ``why_it_fits``,
  the headline verdict, the Section-02 market analysis, the fit score, and the
  yield/financial commentary are written by the agent and passed in as a payload.
  This module never invents narrative — it only typesets it beautifully.

  SECTION 02 MUST BE RESEARCHED, NOT GUESSED. Before writing any of the ``market``
  prose (standfirst / overview / forces / per-suburb commentary / outlook), the
  agent MUST pull the *current* macro + local picture with a live web search —
  never rely on stale priors or what a previous report said. At minimum confirm:
  (1) the RBA cash rate + latest decision + the near-term hike/hold/cut bias;
  (2) latest CPI/inflation print; (3) a current Sydney dwelling-price forecast
  from a named house (e.g. ANZ / CoreLogic-Cotality / Domain); (4) any
  suburb/precinct-level signal for the searched suburbs. Cite the real numbers
  and lean on the project's own sold-comp data (``sales_report.py``) for medians.
  Trajectory/trend/force tone calls must follow the evidence, not optimism.

Payload shape (see ``sample_payload()`` for a full real example):

    {
      "meta":   {title, issue, date, prepared_for, prepared_by, standfirst},
      "brief":  {objective, budget_buy, budget_rent, beds, baths, cars,
                 region, prose, must_haves[], deal_breakers[]},
      "market": {standfirst, overview,
                 forces:[{label, signal, tone, body}],
                 suburbs:[{name, median, range, growth_12m, rental_yield,
                           days_on_market, trend, trajectory, commentary}],
                 outlook},
      "properties": [{
          headline, address, suburb, price, beds, baths, cars, property_type,
          url, description, features[],
          images:{hero, gallery[], floorplan},
          agency:{name, logo}, agents:[{name, mobile, email}],
          inspections:[{start,end}],
          # --- agent-authored narrative ---
          fit_score (0-10), verdict, why_it_fits (prose),
          highlights[], caveat,
          financials:{est_rent_weekly, gross_yield_pct, strata_quarterly,
                      council_annual, notes}
      }]
    }

Render:  python report_builder.py --sample            # writes a real sample PDF
         build_report(payload, "out/report.pdf")      # programmatic
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from inspection_plan import build_inspection_plan
from market_sources import validate_market_sources
from report_ux import executive_recommendation, why_this_might_be_wrong

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
def _esc(text: Any) -> str:
    return html.escape(str(text)) if text is not None else ""


def _money(value: Optional[float], *, dp: int = 0) -> str:
    if value is None:
        return "—"
    return f"${value:,.{dp}f}"


def _fit_arc(score: Optional[float]) -> str:
    """A small SVG ring showing the fit score out of 10."""
    if score is None:
        return ""
    pct = max(0.0, min(1.0, float(score) / 10.0))
    r = 26
    circ = 2 * 3.14159 * r
    dash = pct * circ
    return f"""
    <svg class="fit-ring" viewBox="0 0 64 64" width="64" height="64">
      <circle cx="32" cy="32" r="{r}" class="fit-track"/>
      <circle cx="32" cy="32" r="{r}" class="fit-fill"
              stroke-dasharray="{dash:.2f} {circ:.2f}"
              transform="rotate(-90 32 32)"/>
      <text x="32" y="35" class="fit-num">{score:g}</text>
    </svg>"""


def _paragraphs(text: Optional[str]) -> str:
    """Turn a prose blob (with blank-line / single-line breaks) into <p>s,
    promoting leading-dash lines into a styled list."""
    if not text:
        return ""
    blocks = [b.strip() for b in str(text).split("\n\n") if b.strip()]
    out: List[str] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and all(ln.startswith(("-", "•")) for ln in lines):
            items = "".join(f"<li>{_esc(ln.lstrip('-• ').strip())}</li>" for ln in lines)
            out.append(f'<ul class="prose-list">{items}</ul>')
        else:
            out.append(f"<p>{_esc(' '.join(lines))}</p>")
    return "".join(out)


def _stat(value: Any, label: str) -> str:
    return (f'<div class="stat"><span class="stat-val">{_esc(value)}</span>'
            f'<span class="stat-lab">{_esc(label)}</span></div>')


def _chips(items: Optional[List[str]], limit: int = 10) -> str:
    if not items:
        return ""
    chips = "".join(f'<span class="chip">{_esc(c)}</span>' for c in items[:limit])
    return f'<div class="chips">{chips}</div>'


# --------------------------------------------------------------------------- #
# Section renderers
# --------------------------------------------------------------------------- #
def _cover(meta: Dict[str, Any], hero: Optional[str]) -> str:
    hero_style = f'style="background-image:url(\'{_esc(hero)}\')"' if hero else ""
    return f"""
    <section class="page cover">
      <div class="cover-photo" {hero_style}></div>
      <div class="cover-scrim"></div>
      <div class="cover-grain"></div>
      <div class="cover-inner">
        <div class="masthead">
          <span class="mast-mark">◆</span>
          <span class="mast-name">PROPERTY&nbsp;FOLIO</span>
          <span class="mast-issue">{_esc(meta.get('issue', ''))}</span>
        </div>
        <div class="cover-mid">
          <p class="cover-eyebrow">{_esc(meta.get('eyebrow', 'A CURATED BUYING DOSSIER'))}</p>
          <h1 class="cover-title">{_esc(meta.get('title', ''))}</h1>
          <p class="cover-standfirst">{_esc(meta.get('standfirst', ''))}</p>
        </div>
        <div class="cover-foot">
          <div class="cf-block">
            <span class="cf-lab">Prepared&nbsp;for</span>
            <span class="cf-val">{_esc(meta.get('prepared_for', ''))}</span>
          </div>
          <div class="cf-block">
            <span class="cf-lab">Compiled</span>
            <span class="cf-val">{_esc(meta.get('date', ''))}</span>
          </div>
          <div class="cf-block">
            <span class="cf-lab">By</span>
            <span class="cf-val">{_esc(meta.get('prepared_by', ''))}</span>
          </div>
        </div>
      </div>
    </section>"""


def _brief(brief: Dict[str, Any]) -> str:
    def row(label: str, value: str) -> str:
        return (f'<div class="brief-row"><span class="brief-lab">{_esc(label)}</span>'
                f'<span class="brief-val">{_esc(value)}</span></div>')

    budget_bits = []
    if brief.get("budget_buy"):
        budget_bits.append(f"Buy to {_money(brief['budget_buy'])}")
    if brief.get("budget_rent"):
        budget_bits.append(f"Rent to {_money(brief['budget_rent'])}/wk")
    budget = "  ·  ".join(budget_bits) or "—"

    must = "".join(f"<li>{_esc(x)}</li>" for x in (brief.get("must_haves") or []))
    breakers = "".join(f"<li>{_esc(x)}</li>" for x in (brief.get("deal_breakers") or []))

    return f"""
    <section class="page brief-page">
      <header class="sec-head">
        <span class="sec-kicker">Section 01</span>
        <h2 class="sec-title">The Brief</h2>
      </header>
      <div class="brief-grid">
        <div class="brief-card">
          <h3 class="card-h">Mandate</h3>
          {row('Objective', str(brief.get('objective','')).title())}
          {row('Budget', budget)}
          {row('Bedrooms', f"{brief.get('beds','—')}+")}
          {row('Bathrooms', f"{brief.get('baths','—')}+")}
          {row('Parking', f"{brief.get('cars','—')}+")}
          {row('Region', brief.get('region',''))}
        </div>
        <div class="brief-prose">
          {_paragraphs(brief.get('prose'))}
          <div class="brief-cols">
            <div>
              <h4 class="mini-h">Strong preferences</h4>
              <ul class="want">{must}</ul>
            </div>
            <div>
              <h4 class="mini-h breaker">Deal-breakers</h4>
              <ul class="want breaker-list">{breakers}</ul>
            </div>
          </div>
        </div>
      </div>
    </section>"""


# Trend / trajectory badges map a one-word call to a tone class.
_TRAJ_TONE = {
    "upward": "up", "rising": "up", "firming": "up", "strong": "up", "hot": "up",
    "flat": "flat", "steady": "flat", "stable": "flat", "holding": "flat",
    "softening": "down", "cooling": "down", "downward": "down", "easing": "down",
}


def _traj_class(word: str) -> str:
    return _TRAJ_TONE.get(str(word).strip().lower(), "flat")


def _badge(word: str, arrow: bool = False, tone: Optional[str] = None) -> str:
    if not word:
        return ""
    tone = tone or _traj_class(word)
    suffix = ""
    if arrow:
        suffix = " ↗" if tone == "up" else (" ↘" if tone == "down" else "")
    return f'<span class="mbadge tone-{tone}">{_esc(word)}{suffix}</span>'


def _glance_table(suburbs: List[Dict[str, Any]]) -> str:
    rows = ""
    for s in suburbs:
        rows += f"""
        <tr>
          <td class="g-name">{_esc(s.get('name',''))}</td>
          <td class="g-med">{_esc(s.get('median','—'))}</td>
          <td>{_esc(s.get('growth_12m','—'))}</td>
          <td>{_esc(s.get('rental_yield','—'))}</td>
          <td>{_esc(str(s.get('days_on_market','—')) + (' days' if s.get('days_on_market') else ''))}</td>
          <td class="g-traj">{_badge(s.get('trajectory',''), arrow=True)}</td>
        </tr>"""
    return f"""
      <table class="glance">
        <thead><tr>
          <th>Suburb</th><th>Median</th><th>12-mo growth</th>
          <th>Gross yield</th><th>Avg. on market</th><th>Trajectory</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>"""


def _suburb_brief(s: Dict[str, Any]) -> str:
    badges = _badge(s.get("trend", "")) + _badge(s.get("trajectory", ""), arrow=True)
    return f"""
      <article class="sub-brief">
        <header class="sub-brief-head">
          <h3 class="sub-brief-name">{_esc(s.get('name',''))}</h3>
          <span class="sub-brief-med">{_esc(s.get('median','—'))} median
            <i>· {_esc(s.get('range',''))}</i></span>
          <div class="sub-badges">{badges}</div>
        </header>
        <div class="sub-commentary">{_paragraphs(s.get('commentary'))}</div>
      </article>"""


def _forces(forces: List[Dict[str, Any]]) -> str:
    if not forces:
        return ""
    cards = ""
    for f in forces:
        cards += f"""
        <article class="force">
          <div class="force-top">
            <span class="force-lab">{_esc(f.get('label',''))}</span>
            {_badge(f.get('signal',''), arrow=True, tone=f.get('tone'))}
          </div>
          <p class="force-body">{_esc(f.get('body',''))}</p>
        </article>"""
    return f"""
      <h4 class="market-subhead">The forces at work</h4>
      <div class="forces">{cards}</div>"""


def _market(market: Dict[str, Any]) -> str:
    suburbs = market.get("suburbs") or []
    source_state = validate_market_sources(market)
    overview = (f'<div class="market-overview">{_paragraphs(market.get("overview"))}</div>'
                if market.get("overview") else "")
    briefs = "".join(_suburb_brief(s) for s in suburbs)
    outlook = ""
    if market.get("outlook"):
        outlook = f"""
        <div class="market-outlook">
          <span class="outlook-lab">The trajectory</span>
          <div class="outlook-body">{_paragraphs(market.get('outlook'))}</div>
        </div>"""

    # Page 1 — the narrative + an at-a-glance comparison.
    page1 = f"""
    <section class="page market-page">
      <header class="sec-head">
        <span class="sec-kicker">Section 02</span>
        <h2 class="sec-title">The Market</h2>
      </header>
      <p class="market-standfirst">{_esc(market.get('standfirst',''))}</p>
      {_source_panel(source_state)}
      {overview}
      <h4 class="market-subhead">At a glance</h4>
      {_glance_table(suburbs)}
      {_forces(market.get('forces') or [])}
    </section>"""

    # Page 2 — the suburb-by-suburb deep dive + forward call.
    page2 = f"""
    <section class="page market-page">
      <header class="sec-head">
        <span class="sec-kicker">Section 02 · continued</span>
        <h2 class="sec-title">Suburb by Suburb</h2>
      </header>
      <div class="sub-briefs">{briefs}</div>
      {outlook}
    </section>"""

    return page1 + page2


def _source_panel(source_state: Dict[str, Any]) -> str:
    facts = source_state.get("facts") or []
    warnings = source_state.get("warnings") or []
    if not facts and not warnings:
        return ""
    warning_html = "".join(f"<li>{_esc(w)}</li>" for w in warnings)
    fact_html = ""
    for fact in facts[:6]:
        status = fact.get("status") or "unknown"
        date = fact.get("published_at") or fact.get("observed_at") or "undated"
        source = fact.get("source_name") or "source"
        url = fact.get("source_url") or ""
        fact_html += f"""
        <li class="source-fact source-{_esc(status)}">
          <span><b>{_esc(fact.get('label') or fact.get('key'))}</b> {_esc(fact.get('value') or 'unavailable')}</span>
          <i>{_esc(source)} · {_esc(date)}</i>
          <em>{_esc(url)}</em>
        </li>"""
    return f"""
      <div class="source-panel source-status-{_esc(source_state.get('status','unknown'))}">
        <div class="source-head">
          <span>Market evidence</span>
          <b>{_esc(str(source_state.get('status','unknown')).replace('_', ' '))}</b>
        </div>
        {f'<ul class="source-warnings">{warning_html}</ul>' if warning_html else ''}
        {f'<ul class="source-facts">{fact_html}</ul>' if fact_html else ''}
      </div>"""


def _contents(props: List[Dict[str, Any]], start_page: int) -> str:
    """A one-page shortlist index: every property with headline stats, the
    fit + viability scores, and the page it lives on. start_page = page number
    of the first property's plate."""
    rows = ""
    for i, p in enumerate(props):
        page_no = start_page + i * 2
        fit = p.get("fit_score")
        fit_s = f"{float(fit):.1f}" if isinstance(fit, (int, float)) else "—"
        v = p.get("viability") or {}
        via = v.get("score")
        via_s = f"{float(via):.1f}" if isinstance(via, (int, float)) else "—"
        config = " · ".join(filter(None, [
            f"{p.get('beds')} bed" if p.get("beds") not in (None, "—") else "",
            f"{p.get('baths')} bath" if p.get("baths") not in (None, "—") else "",
            f"{p.get('cars')} car" if p.get("cars") not in (None, "—") else "",
            str(p.get("property_type")) if p.get("property_type") not in (None, "—") else "",
        ]))
        rows += f"""
        <tr>
          <td class="sl-no">{i + 1:02d}</td>
          <td class="sl-prop">
            <span class="sl-addr">{_esc(p.get('address',''))}</span>
            <span class="sl-config">{_esc(config)}</span>
          </td>
          <td class="sl-price">{_esc(p.get('price','—'))}</td>
          <td class="sl-score"><b>{fit_s}</b><i>fit</i></td>
          <td class="sl-score"><b>{via_s}</b><i>via</i></td>
          <td class="sl-page">p.&thinsp;{page_no:02d}</td>
        </tr>"""
    return f"""
    <section class="page contents-page">
      <header class="sec-head">
        <span class="sec-kicker">The Shortlist</span>
        <h2 class="sec-title">At a Glance</h2>
      </header>
      <p class="contents-intro">{len(props)} properties, ranked and ready. Fit scores
        the brief; viability weighs price, yield and risk. Turn to the page noted for
        the full dossier on each.</p>
      <table class="shortlist">
        <thead><tr>
          <th></th><th>Property</th><th>Guide</th>
          <th class="sl-th-score">Fit</th><th class="sl-th-score">Viability</th><th>Page</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>"""


def _executive_recommendation(props: List[Dict[str, Any]]) -> str:
    rec = executive_recommendation(props)
    standout = rec.get("standout") or {}
    reasons = "".join(f"<li>{_esc(item)}</li>" for item in rec.get("reasons", []))
    watch = "".join(f"<li>{_esc(item)}</li>" for item in rec.get("watch", []))
    actions = "".join(f"<li>{_esc(item)}</li>" for item in rec.get("next_actions", []))
    why_wrong = "".join(f"<li>{_esc(item)}</li>" for item in rec.get("why_wrong", []))
    price = standout.get("price") or "—"
    addr = standout.get("address") or "No current standout"
    score = (standout.get("viability") or {}).get("score") or standout.get("fit_score") or "—"
    confidence = rec.get("confidence") or "low"

    return f"""
    <section class="page exec-page">
      <header class="sec-head">
        <span class="sec-kicker">Recommendation</span>
        <h2 class="sec-title">What I Would Do</h2>
      </header>
      <div class="exec-hero">
        <span class="exec-label">Standout pick</span>
        <h3>{_esc(addr)}</h3>
        <p>{_esc(rec.get('summary',''))}</p>
        <div class="exec-facts">
          <span><b>{_esc(price)}</b><i>Guide</i></span>
          <span><b>{_esc(score)}</b><i>Score</i></span>
          <span><b>{_esc(str(confidence).title())}</b><i>Confidence</i></span>
        </div>
      </div>
      <div class="exec-grid">
        <article>
          <span class="mini-h">Why this leads</span>
          <ul>{reasons}</ul>
        </article>
        <article>
          <span class="mini-h">Watch before committing</span>
          <ul>{watch}</ul>
        </article>
        <article>
          <span class="mini-h">Next moves</span>
          <ul>{actions}</ul>
        </article>
        <article class="wrong-note">
          <span class="mini-h">Why this might be wrong</span>
          <ul>{why_wrong}</ul>
        </article>
      </div>
    </section>"""


def _inspection_run(props: List[Dict[str, Any]]) -> str:
    plan = build_inspection_plan(props)
    stops = plan.get("stops") or []
    unscheduled = plan.get("unscheduled") or []
    target = plan.get("target_date")
    try:
        target_label = datetime.fromisoformat(str(target)).strftime("%A %-d %B %Y") if target else "Next open day"
    except ValueError:
        target_label = str(target or "Next open day")

    rows = ""
    for i, stop in enumerate(stops[:10]):
        rows += f"""
        <tr class="{'clash' if stop.get('clash') else ''}">
          <td class="ir-no">{i + 1:02d}</td>
          <td class="ir-time">{_esc(stop.get('time_label',''))}</td>
          <td class="ir-prop">
            <span>{_esc(stop.get('address',''))}</span>
            <i>{_esc(stop.get('suburb',''))} · {_esc(stop.get('priority',''))}</i>
          </td>
          <td class="ir-note">{_esc(stop.get('travel_note',''))}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="4" class="ir-empty">No open times are visible in the current shortlist. Call agents first.</td></tr>'

    followups = "".join(
        f"<li><b>{_esc(item.get('suburb',''))}</b> {_esc(item.get('address',''))}</li>"
        for item in unscheduled[:8]
    )
    followup_block = f"""
      <div class="ir-follow">
        <span class="mini-h">Agent follow-up</span>
        <ul>{followups}</ul>
      </div>""" if followups else ""

    return f"""
    <section class="page inspection-page">
      <header class="sec-head">
        <span class="sec-kicker">Weekend Plan</span>
        <h2 class="sec-title">Inspection Run</h2>
      </header>
      <p class="contents-intro">{_esc(target_label)} · {_esc(plan.get('summary',''))}. Confirm every time with the agent before leaving.</p>
      <table class="inspection-run">
        <thead><tr><th></th><th>Time</th><th>Property</th><th>Route note</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      {followup_block}
    </section>"""


def _scorecard(v: Optional[Dict[str, Any]]) -> str:
    """A compact, data-driven viability breakdown (from viability.score_listing)."""
    if not v:
        return ""
    comps = v.get("components") or {}
    lens = v.get("lens", "owner_occupier")
    lens_label = "Yield + fit" if lens == "investor" else "Lifestyle fit"
    bar_map = [
        ("Brief match", comps.get("hard_criteria")),
        ("Budget fit", comps.get("budget_fit")),
        (lens_label, comps.get("yield_or_lifestyle")),
        ("Preferences", comps.get("preference_match")),
    ]
    bars = ""
    for lab, val in bar_map:
        if val is None:
            continue
        pct = max(0.0, min(1.0, float(val))) * 100
        bars += (f'<div class="sc-row"><span class="sc-lab">{_esc(lab)}</span>'
                 f'<span class="sc-track"><span class="sc-fill" style="width:{pct:.0f}%"></span></span></div>')
    pen = comps.get("dealbreaker_penalty") or 0
    pen_note = f'<p class="sc-pen">Risk penalty applied: −{pen:.1f}</p>' if pen else ""
    return f"""
      <div class="scorecard">
        <span class="sc-h">Viability — {_esc(v.get('band',''))}
          <b>{_esc(v.get('score',''))}</b><i>/10 · {_esc(lens.replace('_',' '))} lens</i></span>
        {bars}
        {pen_note}
      </div>"""


def _decision_panels(p: Dict[str, Any]) -> str:
    """Evidence panels generated by decision_engine.analyse_listing."""
    val = p.get("valuation") or {}
    risks = p.get("risks") or {}
    dd = p.get("due_diligence") or {}
    action = p.get("action_plan") or {}

    blocks = ""
    if val:
        comp_range = val.get("comparable_range") or []
        rng = " - ".join(_money(x) for x in comp_range) if len(comp_range) == 2 else "Insufficient comps"
        if val.get("mode") == "rent":
            metric_bits = [
                f"{_esc(val.get('comparable_count',0))} rent comps",
                f"median {_money(val.get('median_comparable'))}/wk",
                f"range {_esc(rng)}/wk",
                f"{_esc(val.get('weekly_affordability','unknown'))}",
                f"{_esc(val.get('application_urgency','low'))} urgency",
            ]
        else:
            metric_bits = [
                f"{_esc(val.get('comparable_count',0))} comps",
                f"median {_money(val.get('median_comparable'))}",
                f"range {_esc(rng)}",
                f"{_esc(val.get('confidence',''))} confidence",
            ]
        blocks += f"""
        <div class="decision-card">
          <span class="dc-k">Valuation</span>
          <b class="dc-h">{_esc(str(val.get('fairness','unknown')).title())}</b>
          <p>{_esc(val.get('negotiation_posture',''))}</p>
          <small>{" · ".join(metric_bits)}</small>
        </div>"""

    top_risks = risks.get("top") or []
    if top_risks:
        items = "".join(f"<li><b>{_esc(r.get('severity',''))}</b> {_esc(r.get('label',''))}</li>" for r in top_risks[:3])
        blocks += f"""
        <div class="decision-card">
          <span class="dc-k">Risk</span>
          <b class="dc-h">{_esc(risks.get('summary',''))}</b>
          <ul class="dc-list">{items}</ul>
        </div>"""

    dd_items = dd.get("items") or []
    if dd_items:
        items = "".join(
            f"<li><b>{_esc(i.get('severity',''))}</b> {_esc(i.get('category','').replace('_',' '))}: {_esc(i.get('recommended_action',''))}</li>"
            for i in dd_items[:4]
        )
        blocks += f"""
        <div class="decision-card">
          <span class="dc-k">Due diligence</span>
          <b class="dc-h">{_esc(dd.get('summary',''))}</b>
          <ul class="dc-list">{items}</ul>
        </div>"""

    best = action.get("best_next_action") or {}
    if best:
        blocks += f"""
        <div class="decision-card action">
          <span class="dc-k">Action plan</span>
          <b class="dc-h">{_esc(best.get('label',''))}</b>
          <p>{_esc(best.get('detail',''))}</p>
          <small>{_esc(best.get('priority',''))} priority · deadline: {_esc(best.get('deadline',''))}</small>
        </div>"""

    return f'<div class="decision-grid">{blocks}</div>' if blocks else ""


def _property_pages(idx: int, p: Dict[str, Any]) -> str:
    """A deliberate two-page magazine spread per property:
    Page A = the photo-led plate; Page B = the data-led dossier."""
    imgs = p.get("images") or {}
    hero = imgs.get("hero")
    gallery = imgs.get("gallery") or []
    floorplan = imgs.get("floorplan")

    hero_style = f'style="background-image:url(\'{_esc(hero)}\')"' if hero else ""

    stats = (
        _stat(p.get("beds", "—"), "Beds")
        + _stat(p.get("baths", "—"), "Baths")
        + _stat(p.get("cars", "—"), "Cars")
        + _stat(p.get("property_type", "—"), "Type")
    )

    highlights = "".join(f"<li>{_esc(h)}</li>" for h in (p.get("highlights") or []))
    caveat = ""
    if p.get("caveat"):
        caveat = f'<div class="caveat"><span class="caveat-lab">One watch-out</span>{_esc(p["caveat"])}</div>'
    wrong_notes = why_this_might_be_wrong(p)
    wrong_note = ""
    if wrong_notes:
        wrong_items = "".join(f"<li>{_esc(item)}</li>" for item in wrong_notes)
        wrong_note = f"""
        <div class="wrong-note prop-wrong">
          <span class="caveat-lab">Why this might be wrong</span>
          <ul>{wrong_items}</ul>
        </div>"""

    fin = p.get("financials") or {}
    fin_rows = ""
    fin_map = [
        ("Est. rent", _money(fin.get("est_rent_weekly")) + ("/wk" if fin.get("est_rent_weekly") else "")),
        ("Gross yield", (f"{fin['gross_yield_pct']:.1f}%" if fin.get("gross_yield_pct") is not None else "—")),
        ("Strata", _money(fin.get("strata_quarterly")) + ("/qtr" if fin.get("strata_quarterly") else "")),
        ("Council", _money(fin.get("council_annual")) + ("/yr" if fin.get("council_annual") else "")),
    ]
    for lab, val in fin_map:
        fin_rows += f'<div class="fin-row"><span>{_esc(lab)}</span><b>{_esc(val)}</b></div>'
    fin_note = f'<p class="fin-note">{_esc(fin.get("notes"))}</p>' if fin.get("notes") else ""

    # Every property's visuals read as a balanced block: a 2x2 feature tile
    # (the floorplan if it exists, else the lead photo) plus thumbs that fill
    # complete rows of the 4-col grid (4 thumbs = 2 rows, 8 = 3 rows).
    if floorplan:
        feature_tile = (f'<div class="mz-floor"><span class="fp-lab">Floorplan</span>'
                        f'<div class="fp-img" style="background-image:url(\'{_esc(floorplan)}\')"></div></div>')
        thumbs = list(gallery)
    elif gallery:
        feature_tile = (f'<div class="mz-feature" '
                        f'style="background-image:url(\'{_esc(gallery[0])}\')"></div>')
        thumbs = gallery[1:]
    else:
        feature_tile, thumbs = "", []
    n = len(thumbs)
    n_tiles = 8 if n >= 8 else 4
    gal_items = "".join(
        f'<div class="mz-cell" style="background-image:url(\'{_esc(u)}\')"></div>'
        for u in thumbs[:n_tiles]
    )

    agents = p.get("agents") or []
    agent_block = ""
    if agents:
        a = agents[0]
        contacts = " · ".join(filter(None, [a.get("mobile"), a.get("email")]))
        agency = (p.get("agency") or {}).get("name", "")
        agent_block = f"""
        <div class="agent">
          <div>
            <span class="agent-name">{_esc(a.get('name',''))}</span>
            <span class="agent-agency">{_esc(agency)}</span>
          </div>
          <span class="agent-contact">{_esc(contacts)}</span>
        </div>"""

    insp = p.get("inspections") or []
    insp_block = ""
    if insp:
        first = insp[0]
        try:
            s = datetime.fromisoformat(str(first.get("start")))
            e = datetime.fromisoformat(str(first.get("end")))
            when = s.strftime("%a %-d %b, %-I:%M") + e.strftime("–%-I:%M%p").lower()
        except Exception:
            when = str(first.get("start", ""))
        insp_block = f'<div class="insp"><span class="insp-lab">Next inspection</span>{_esc(when)}</div>'

    headline = _esc(p.get("headline") or p.get("address", ""))

    # ---- Page A — the plate (photo-led) ----
    page_a = f"""
    <section class="page prop-plate">
      <div class="prop-hero" {hero_style}>
        <div class="hero-scrim"></div>
        <div class="hero-index">No. {idx:02d}</div>
        <div class="hero-overlay">
          <div class="hero-price">{_esc(p.get('price','—'))}</div>
          <h2 class="hero-headline">{headline}</h2>
          <p class="hero-address">{_esc(p.get('address',''))}</p>
        </div>
        {_fit_arc(p.get('fit_score'))}
      </div>
      <div class="plate-body">
        <div class="stats-row">{stats}</div>
        <div class="verdict">
          <span class="verdict-lab">The verdict</span>
          <p class="verdict-text">{_esc(p.get('verdict',''))}</p>
        </div>
        <div class="plate-cols">
          <div class="plate-why">
            <h3 class="why-h">Why it fits the brief</h3>
            <div class="why-prose">{_paragraphs(p.get('why_it_fits'))}</div>
          </div>
          <div class="plate-side">
            <ul class="highlights">{highlights}</ul>
          </div>
        </div>
      </div>
    </section>"""

    # ---- Page B — the dossier (data-led) ----
    page_b = f"""
    <section class="page prop-dossier">
      <header class="dossier-head">
        <span class="dh-index">No. {idx:02d} · continued</span>
        <span class="dh-addr">{_esc(p.get('address',''))}</span>
      </header>
      <div class="dossier-cols">
        <div class="dossier-main">
          {caveat}
          {wrong_note}
          <h4 class="desc-h">From the listing</h4>
          <div class="desc">{_paragraphs(p.get('description'))}</div>
          {_chips(p.get('features'))}
          {_decision_panels(p)}
        </div>
        <aside class="prop-aside">
          {_scorecard(p.get('viability'))}
          <div class="fin-card">
            <span class="fin-h">Financials &amp; yield</span>
            {fin_rows}
            {fin_note}
          </div>
          {agent_block}
          {insp_block}
          <a class="listing-link">{_esc(p.get('url',''))}</a>
        </aside>
      </div>
      <div class="dossier-visuals">
        <span class="visuals-lab">{"Gallery &amp; floorplan" if floorplan else "Gallery"}</span>
        <div class="mosaic">{feature_tile}{gal_items}</div>
      </div>
    </section>"""

    return page_a + page_b


# --------------------------------------------------------------------------- #
# Document assembly
# --------------------------------------------------------------------------- #
def render_html(payload: Dict[str, Any], palette: str = "folio") -> str:
    meta = payload.get("meta", {})
    brief = payload.get("brief", {})
    market = payload.get("market", {})
    props = payload.get("properties", [])

    hero_img = None
    if props:
        hero_img = (props[0].get("images") or {}).get("hero")

    spreads = "".join(_property_pages(i + 1, p) for i, p in enumerate(props))
    root = _palette_root(palette)
    property_start_page = 8

    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=Newsreader:ital,opsz,wght@0,6..72,300..600;1,6..72,300..500&family=Archivo:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{root}{CSS}</style>
</head>
<body>
  {_cover(meta, hero_img)}
  {_brief(brief)}
  {_market(market)}
  {_executive_recommendation(props)}
  {_contents(props, start_page=property_start_page)}
  {_inspection_run(props)}
  {spreads}
  <section class="page closing">
    <div class="closing-inner">
      <span class="mast-mark big">◆</span>
      <p class="closing-line">{_esc(meta.get('closing', 'Compiled by your buying agent. Numbers are estimates — verify strata, council and contract terms before offering.'))}</p>
      <span class="closing-mast">PROPERTY&nbsp;FOLIO</span>
    </div>
  </section>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# Palettes — each a cohesive, premium direction. Same layout, different mood.
# --------------------------------------------------------------------------- #
PALETTES: Dict[str, Dict[str, str]] = {
    # Warm, classic editorial real-estate luxe.
    "folio": {
        "paper": "#F7F3EC", "paper2": "#EFE9DE", "ink": "#1C2620",
        "ink_soft": "#46514A", "dark": "#1F3D32", "accent": "#B5894E",
        "accent_soft": "#C8A56E", "hair": "#D8CFC0", "danger": "#9A4631",
    },
    # Cool, gallery-like monochrome with a single oxblood jewel accent.
    "oyster": {
        "paper": "#F2F0EB", "paper2": "#E7E3DB", "ink": "#15171A",
        "ink_soft": "#4A4D52", "dark": "#202227", "accent": "#7C3A3A",
        "accent_soft": "#A65A53", "hair": "#D7D2C8", "danger": "#7C3A3A",
    },
    # Crisp, airy coastal Sydney: deep navy with a warm clay accent.
    "harbour": {
        "paper": "#FBFAF7", "paper2": "#EEEDE6", "ink": "#16263B",
        "ink_soft": "#46566A", "dark": "#16263B", "accent": "#C2814F",
        "accent_soft": "#D8A579", "hair": "#DAD8CE", "danger": "#B05a3c",
    },
}


def _palette_root(name: str) -> str:
    pal = PALETTES.get(name, PALETTES["folio"])
    return (":root{"
            f"--paper:{pal['paper']};--paper-2:{pal['paper2']};--ink:{pal['ink']};"
            f"--ink-soft:{pal['ink_soft']};--forest:{pal['dark']};"
            f"--brass:{pal['accent']};--brass-soft:{pal['accent_soft']};"
            f"--hair:{pal['hair']};--danger:{pal['danger']};}}")


CSS = r"""
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact;}
@page{ size:A4; margin:0; }
html,body{margin:0;padding:0;background:var(--paper);color:var(--ink);
  font-family:"Newsreader",Georgia,serif; font-optical-sizing:auto;}
.page{ position:relative; width:210mm; min-height:297mm; padding:18mm 17mm;
  page-break-after:always; overflow:hidden; background:var(--paper); }
.page:last-child{ page-break-after:auto; }

/* ---- shared type ---- */
h1,h2,h3,h4{font-family:"Fraunces",serif;font-weight:500;margin:0;}
.label,.sec-kicker,.cf-lab,.stat-lab,.mini-h,.verdict-lab,.fin-h,.insp-lab,
.fp-lab,.brief-lab,.caveat-lab,.cover-eyebrow,.mast-name,.mast-issue{
  font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.22em; font-size:8.5px; font-weight:600; color:var(--brass);}

/* ===================== COVER ===================== */
.cover{ padding:0; color:#F4EFE6; background:var(--forest); }
.cover-photo{ position:absolute; inset:0; background-size:cover;
  background-position:center; filter:saturate(.92) contrast(1.02); }
.cover-scrim{ position:absolute; inset:0;
  background:linear-gradient(180deg, rgba(15,28,22,.42) 0%, rgba(15,28,22,.30) 38%,
    rgba(15,28,22,.72) 78%, rgba(15,28,22,.92) 100%); }
.cover-grain{ position:absolute; inset:0; opacity:.10; mix-blend-mode:overlay;
  background-image:radial-gradient(circle at 1px 1px, #fff 1px, transparent 0);
  background-size:4px 4px; }
.cover-inner{ position:relative; height:297mm; padding:16mm 16mm 14mm;
  display:flex; flex-direction:column; }
.masthead{ display:flex; align-items:center; gap:10px;
  border-bottom:1px solid rgba(244,239,230,.35); padding-bottom:10px; }
.mast-mark{ color:var(--brass-soft); font-size:13px; }
.mast-name{ color:#F4EFE6; letter-spacing:.4em; font-size:11px; }
.mast-issue{ margin-left:auto; color:rgba(244,239,230,.7); }
.cover-mid{ margin-top:auto; margin-bottom:auto; }
.cover-eyebrow{ color:var(--brass-soft); margin:0 0 14px; }
.cover-title{ font-size:62px; line-height:.98; font-weight:340;
  letter-spacing:-.01em; max-width:13ch; text-shadow:0 2px 30px rgba(0,0,0,.3); }
.cover-title{ font-variation-settings:"opsz" 144; }
.cover-standfirst{ font-family:"Newsreader",serif; font-style:italic;
  font-size:16px; line-height:1.5; max-width:46ch; margin-top:18px;
  color:rgba(244,239,230,.92); }
.cover-foot{ display:flex; gap:38px; padding-top:16px;
  border-top:1px solid rgba(244,239,230,.35); }
.cf-block{ display:flex; flex-direction:column; gap:4px; }
.cf-lab{ color:var(--brass-soft); }
.cf-val{ font-size:14px; color:#F4EFE6; }

/* ===================== SECTION HEADS ===================== */
.sec-head{ display:flex; align-items:baseline; gap:16px;
  border-bottom:2px solid var(--ink); padding-bottom:8px; margin-bottom:20px; }
.sec-head-2{ margin-top:30px; }
.sec-kicker{ color:var(--brass); }
.sec-title{ font-size:34px; font-weight:360; letter-spacing:-.01em; }

/* ===================== BRIEF ===================== */
.brief-grid{ display:grid; grid-template-columns:0.85fr 1.15fr; gap:22px; }
.brief-card{ background:var(--paper-2); border:1px solid var(--hair);
  padding:18px 18px 12px; }
.card-h{ font-size:18px; margin-bottom:12px; }
.brief-row{ display:flex; justify-content:space-between; gap:10px;
  padding:7px 0; border-bottom:1px solid var(--hair); }
.brief-row:last-child{ border-bottom:none; }
.brief-lab{ color:var(--brass); align-self:center; }
.brief-val{ font-size:13.5px; text-align:right; max-width:60%; }
.brief-prose p{ font-size:13.5px; line-height:1.62; margin:0 0 10px; color:var(--ink-soft); }
.brief-cols{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:6px; }
.mini-h{ display:block; margin-bottom:8px; }
.mini-h.breaker{ color:var(--danger); }
.want{ list-style:none; margin:0; padding:0; }
.want li{ font-size:12.5px; line-height:1.5; padding:4px 0 4px 16px;
  position:relative; color:var(--ink-soft); }
.want li::before{ content:"—"; position:absolute; left:0; color:var(--brass); }
.breaker-list li::before{ content:"×"; color:var(--danger); font-weight:700; }

/* ===================== MARKET (Section 02) ===================== */
.market-standfirst{ font-family:"Newsreader",serif; font-style:italic;
  font-size:16px; line-height:1.55; color:var(--ink); margin:0 0 14px; max-width:66ch; }
.market-overview p{ font-size:13px; line-height:1.62; color:var(--ink-soft);
  margin:0 0 9px; max-width:70ch; }
.source-panel{ border:1px solid var(--hair); border-left:3px solid var(--brass);
  padding:10px 12px; margin:0 0 14px; background:rgba(255,255,255,.22); }
.source-status-missing,.source-status-needs_review{ border-left-color:var(--danger);
  background:rgba(154,70,49,.06); }
.source-head{ display:flex; justify-content:space-between; gap:12px; align-items:center;
  font-family:"Archivo",sans-serif; text-transform:uppercase; letter-spacing:.12em;
  font-size:8.5px; color:var(--brass); font-weight:600; }
.source-head b{ color:var(--ink); font-weight:700; }
.source-warnings,.source-facts{ list-style:none; margin:8px 0 0; padding:0; }
.source-warnings li{ font-size:11.5px; line-height:1.35; color:var(--danger);
  margin:0 0 4px; }
.source-fact{ display:grid; grid-template-columns:1fr auto; gap:2px 10px;
  border-top:1px solid var(--hair); padding-top:7px; margin-top:7px; }
.source-fact span{ font-size:11.5px; color:var(--ink); }
.source-fact i{ font-family:"Archivo",sans-serif; font-style:normal; text-transform:uppercase;
  letter-spacing:.08em; font-size:7.5px; color:var(--ink-soft); text-align:right; }
.source-fact em{ grid-column:1 / -1; font-style:normal; font-size:8.5px;
  color:var(--ink-soft); word-break:break-all; }
.source-stale span,.source-unknown span{ color:var(--danger); }
.sub-badges{ display:flex; gap:6px; flex-shrink:0; }
.mbadge{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.12em; font-size:8.5px; font-weight:600; padding:4px 9px;
  border-radius:2px; border:1px solid var(--hair); color:var(--ink-soft); }
.mbadge.tone-up{ background:var(--forest); color:#EFE7D6; border-color:var(--forest); }
.mbadge.tone-flat{ background:var(--paper); color:var(--ink-soft); }
.mbadge.tone-down{ background:var(--danger); color:#F6EDE6; border-color:var(--danger); }

/* At-a-glance comparison table */
.market-subhead{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.2em; font-size:10px; color:var(--brass); font-weight:600;
  margin:22px 0 11px; padding-bottom:7px; border-bottom:1px solid var(--hair); }
.glance{ width:100%; border-collapse:collapse; }
.glance thead th{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.1em; font-size:8.5px; font-weight:600; color:var(--ink-soft);
  text-align:left; padding:0 10px 9px 0; border-bottom:2px solid var(--ink); }
.glance tbody td{ font-size:12.5px; color:var(--ink-soft); padding:11px 10px 11px 0;
  border-bottom:1px solid var(--hair); vertical-align:middle; }
.glance tbody tr:last-child td{ border-bottom:none; }
.glance .g-name{ font-family:"Fraunces",serif; font-size:15px; color:var(--ink);
  letter-spacing:-.01em; }
.glance .g-med{ font-family:"Fraunces",serif; font-size:14px; color:var(--ink); }
.glance .g-traj{ text-align:right; padding-right:0; }

/* ===================== SHORTLIST / CONTENTS ===================== */
.exec-hero{ border:2px solid var(--ink); padding:18px 20px; margin-bottom:20px;
  background:linear-gradient(180deg, rgba(255,255,255,.24), rgba(255,255,255,.08)); }
.exec-label{ display:block; font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.2em; font-size:9px; font-weight:600; color:var(--brass); margin-bottom:8px; }
.exec-hero h3{ font-size:30px; line-height:1.08; max-width:18ch; }
.exec-hero p{ font-family:"Newsreader",serif; font-style:italic; font-size:16px;
  line-height:1.5; color:var(--ink); max-width:58ch; margin:10px 0 14px; }
.exec-facts{ display:flex; border-top:1px solid var(--hair); padding-top:12px; gap:28px; }
.exec-facts span{ display:flex; flex-direction:column; gap:2px; }
.exec-facts b{ font-family:"Fraunces",serif; font-size:18px; font-weight:500; color:var(--ink); }
.exec-facts i{ font-family:"Archivo",sans-serif; font-style:normal; text-transform:uppercase;
  letter-spacing:.13em; font-size:8px; color:var(--brass); }
.exec-grid{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.exec-grid article{ border-top:2px solid var(--ink); padding-top:10px; break-inside:avoid; }
.exec-grid ul,.wrong-note ul{ list-style:none; padding:0; margin:0; }
.exec-grid li,.wrong-note li{ position:relative; padding:0 0 8px 15px;
  font-size:12.5px; line-height:1.45; color:var(--ink-soft); }
.exec-grid li::before,.wrong-note li::before{ content:"—"; position:absolute; left:0;
  color:var(--brass); }
.wrong-note{ background:rgba(154,70,49,.06); border-color:var(--danger) !important; }
.wrong-note .mini-h,.wrong-note .caveat-lab{ color:var(--danger); }

.contents-intro{ font-family:"Newsreader",serif; font-style:italic; font-size:15px;
  line-height:1.55; color:var(--ink); margin:0 0 16px; max-width:64ch; }
.shortlist{ width:100%; border-collapse:collapse; }
.shortlist thead th{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.1em; font-size:8.5px; font-weight:600; color:var(--ink-soft);
  text-align:left; padding:0 10px 9px 0; border-bottom:2px solid var(--ink); }
.shortlist thead th.sl-th-score{ text-align:center; }
.shortlist tbody td{ padding:12px 10px 12px 0; border-bottom:1px solid var(--hair);
  vertical-align:middle; }
.shortlist tbody tr:last-child td{ border-bottom:none; }
.sl-no{ font-family:"Fraunces",serif; font-size:15px; color:var(--brass);
  width:30px; }
.sl-prop{ width:100%; }
.sl-addr{ display:block; font-family:"Fraunces",serif; font-size:15px;
  color:var(--ink); letter-spacing:-.01em; line-height:1.2; }
.sl-config{ display:block; font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.08em; font-size:8px; color:var(--ink-soft); margin-top:3px; }
.sl-price{ font-family:"Fraunces",serif; font-size:14px; color:var(--ink);
  white-space:nowrap; }
.sl-score{ text-align:center; white-space:nowrap; }
.sl-score b{ font-family:"Fraunces",serif; font-size:17px; font-weight:500;
  color:var(--forest); }
.sl-score i{ display:block; font-family:"Archivo",sans-serif; font-style:normal;
  text-transform:uppercase; letter-spacing:.1em; font-size:7px; color:var(--brass);
  margin-top:2px; }
.sl-page{ font-family:"Archivo",sans-serif; font-size:11px; color:var(--ink-soft);
  white-space:nowrap; text-align:right; padding-right:0; }

/* ===================== INSPECTION RUN ===================== */
.inspection-run{ width:100%; border-collapse:collapse; margin-top:4px; }
.inspection-run thead th{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.1em; font-size:8.5px; font-weight:600; color:var(--ink-soft);
  text-align:left; padding:0 10px 9px 0; border-bottom:2px solid var(--ink); }
.inspection-run tbody td{ padding:12px 10px 12px 0; border-bottom:1px solid var(--hair);
  vertical-align:middle; }
.inspection-run tbody tr:last-child td{ border-bottom:none; }
.inspection-run tr.clash td{ background:rgba(154,70,49,.07); }
.ir-no{ font-family:"Fraunces",serif; font-size:15px; color:var(--brass); width:30px; }
.ir-time{ font-family:"Fraunces",serif; font-size:15px; color:var(--ink); white-space:nowrap; }
.ir-prop span{ display:block; font-family:"Fraunces",serif; font-size:15px; color:var(--ink);
  line-height:1.2; }
.ir-prop i{ display:block; font-family:"Archivo",sans-serif; font-style:normal;
  text-transform:uppercase; letter-spacing:.08em; font-size:8px; color:var(--ink-soft);
  margin-top:3px; }
.ir-note{ font-size:12px; line-height:1.35; color:var(--ink-soft); width:44mm; }
.ir-empty{ font-size:13px; color:var(--ink-soft); font-style:italic; }
.ir-follow{ margin-top:22px; border-top:2px solid var(--ink); padding-top:12px; }
.ir-follow ul{ columns:2; column-gap:22px; list-style:none; padding:0; margin:0; }
.ir-follow li{ break-inside:avoid; font-size:12px; line-height:1.45;
  color:var(--ink-soft); margin:0 0 8px; }
.ir-follow b{ color:var(--brass); font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.08em; font-size:8px; margin-right:6px; }

/* Macro drivers strip */
.forces{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:6px; }
.force{ border-top:2px solid var(--ink); padding:11px 2px 0; }
.force-top{ display:flex; align-items:center; justify-content:space-between;
  gap:8px; margin-bottom:8px; }
.force-lab{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.12em; font-size:9px; font-weight:600; color:var(--ink); }
.force-body{ font-size:11.5px; line-height:1.55; color:var(--ink-soft); margin:0; }

/* Suburb-by-suburb deep dive */
.sub-briefs{ display:flex; flex-direction:column; gap:0; margin-top:4px; }
.sub-brief{ break-inside:avoid; padding:16px 0 17px;
  border-bottom:1px solid var(--hair); }
.sub-brief:first-child{ padding-top:0; }
.sub-brief-head{ display:flex; align-items:baseline; flex-wrap:wrap;
  gap:10px 14px; margin-bottom:10px; }
.sub-brief-name{ font-size:23px; font-weight:400; letter-spacing:-.01em;
  line-height:1; }
.sub-brief-med{ font-family:"Fraunces",serif; font-size:13px; color:var(--ink-soft); }
.sub-brief-med i{ font-style:normal; color:var(--brass); }
.sub-brief-head .sub-badges{ margin-left:auto; }
.sub-commentary p{ font-size:12.5px; line-height:1.58; color:var(--ink-soft); margin:0 0 7px; }
.sub-commentary p:last-child{ margin-bottom:0; }
.market-outlook{ margin-top:18px; border-top:2px solid var(--ink); padding-top:14px;
  display:grid; grid-template-columns:auto 1fr; gap:20px; align-items:start; }
.outlook-lab{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.2em; font-size:9px; color:var(--brass); white-space:nowrap;
  padding-top:3px; }
.outlook-body p{ font-family:"Newsreader",serif; font-size:14px; line-height:1.55;
  color:var(--ink); margin:0 0 8px; }
.outlook-body p:last-child{ margin-bottom:0; }

/* ===================== PROPERTY SPREAD ===================== */
.prop-plate{ padding:0; display:flex; flex-direction:column; }
.prop-hero{ position:relative; height:132mm; background-size:cover;
  background-position:center; background-color:var(--forest); }
.hero-scrim{ position:absolute; inset:0;
  background:linear-gradient(180deg, rgba(16,18,20,.12) 30%, rgba(16,18,20,.55) 72%,
    rgba(16,18,20,.88) 100%); }
.hero-index{ position:absolute; top:14mm; left:17mm; color:#F4EFE6;
  font-family:"Fraunces",serif; font-size:15px; letter-spacing:.1em;
  border:1px solid rgba(244,239,230,.6); padding:5px 11px; border-radius:2px;
  backdrop-filter:blur(2px); }
.hero-overlay{ position:absolute; left:17mm; right:17mm; bottom:11mm; color:#F4EFE6; }
.hero-price{ font-family:"Archivo",sans-serif; font-weight:600; font-size:13px;
  letter-spacing:.06em; color:var(--brass-soft); margin-bottom:6px; }
.hero-headline{ font-size:36px; font-weight:380; line-height:1.02;
  max-width:20ch; text-shadow:0 2px 24px rgba(0,0,0,.4); }
.hero-address{ font-style:italic; font-size:14px; margin-top:6px;
  color:rgba(244,239,230,.92); }
.fit-ring{ position:absolute; top:13mm; right:16mm; }
.fit-track{ fill:none; stroke:rgba(244,239,230,.3); stroke-width:5; }
.fit-fill{ fill:none; stroke:var(--brass-soft); stroke-width:5; stroke-linecap:round; }
.fit-num{ fill:#F4EFE6; font-family:"Fraunces",serif; font-size:20px;
  text-anchor:middle; font-weight:500; }

.plate-body{ padding:11mm 17mm 12mm; flex:1; }
.stats-row{ display:flex; gap:0; border-bottom:1px solid var(--hair);
  margin-bottom:16px; }
.stat{ flex:1; padding:0 14px 14px 0; display:flex; flex-direction:column; gap:3px; }
.stat-val{ font-family:"Fraunces",serif; font-size:21px; }

.verdict{ border-left:3px solid var(--brass); padding:2px 0 2px 14px; margin-bottom:18px; }
.verdict-text{ font-family:"Fraunces",serif; font-style:italic; font-size:20px;
  line-height:1.4; margin:5px 0 0; font-weight:360; }
.plate-cols{ display:grid; grid-template-columns:1.5fr 1fr; gap:26px; }
.why-h{ font-size:19px; margin-bottom:8px; }
.why-prose p{ font-size:13.5px; line-height:1.66; margin:0 0 11px; }
.highlights{ list-style:none; margin:0 0 18px; padding:0; }
.highlights li{ font-size:13px; line-height:1.5; padding:6px 0 6px 18px;
  position:relative; border-bottom:1px dotted var(--hair); }
.highlights li::before{ content:"✓"; position:absolute; left:0;
  color:var(--brass); font-weight:700; }
.caveat{ background:var(--paper-2); border-left:3px solid var(--brass-soft);
  padding:11px 13px; margin:0 0 18px; font-size:12.5px; line-height:1.5; }
.caveat-lab{ display:block; margin-bottom:4px; }
.prop-wrong{ border-left:3px solid var(--danger); padding:10px 12px; margin:0 0 16px; }
.prop-wrong li{ font-size:11.5px; padding-bottom:5px; }

/* viability scorecard */
.scorecard{ background:var(--paper-2); border:1px solid var(--hair); padding:14px 15px; }
.sc-h{ display:block; font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.16em; font-size:8.5px; color:var(--brass); margin-bottom:12px; }
.sc-h b{ font-family:"Fraunces",serif; font-size:18px; letter-spacing:0;
  color:var(--ink); margin-left:6px; }
.sc-h i{ font-style:normal; font-size:8px; color:var(--ink-soft); letter-spacing:.1em; }
.sc-row{ display:flex; align-items:center; gap:9px; margin-bottom:8px; }
.sc-lab{ flex:0 0 64px; font-size:10px; color:var(--ink-soft); }
.sc-track{ flex:1; height:5px; background:var(--hair); border-radius:3px; overflow:hidden; }
.sc-fill{ display:block; height:100%; background:var(--brass); border-radius:3px; }
.sc-pen{ font-size:10px; color:var(--danger); margin:8px 0 0; font-style:italic; }

/* ===================== PROPERTY · PAGE B (dossier) ===================== */
.prop-dossier{ display:flex; flex-direction:column; }
.dossier-head{ display:flex; align-items:baseline; justify-content:space-between;
  border-bottom:2px solid var(--ink); padding-bottom:8px; margin-bottom:18px; }
.dh-index{ font-family:"Archivo",sans-serif; text-transform:uppercase;
  letter-spacing:.2em; font-size:9px; color:var(--brass); }
.dh-addr{ font-family:"Fraunces",serif; font-size:16px; }
.dossier-cols{ display:grid; grid-template-columns:1.5fr 1fr; gap:26px; }
.desc-h{ font-size:16px; margin:0 0 8px; }
.desc p{ font-size:12px; line-height:1.6; color:var(--ink-soft); margin:0 0 8px; }
.prose-list{ list-style:none; margin:0 0 8px; padding:0; }
.prose-list li{ font-size:12px; line-height:1.5; padding:2px 0 2px 14px;
  position:relative; color:var(--ink-soft); }
.prose-list li::before{ content:"·"; position:absolute; left:2px; color:var(--brass);
  font-weight:700; }
.chips{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
.chip{ font-family:"Archivo",sans-serif; font-size:9.5px; letter-spacing:.04em;
  text-transform:uppercase; background:var(--paper-2); border:1px solid var(--hair);
  padding:4px 9px; border-radius:2px; color:var(--ink-soft); }

/* deterministic decision-engine panels */
.decision-grid{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:11px; }
.decision-card{ border:1px solid rgba(31,39,33,.16); background:rgba(255,255,255,.32);
  padding:8px 9px; min-height:58px; break-inside:avoid; }
.dc-k{ display:block; font-family:"Archivo",sans-serif; font-size:6.4pt; text-transform:uppercase;
  letter-spacing:.08em; color:var(--muted); margin-bottom:3px; }
.dc-h{ display:block; font-family:"Archivo",sans-serif; font-size:8.4pt; line-height:1.1; color:var(--ink); }
.decision-card p{ margin:4px 0 0; font-size:7.4pt; line-height:1.25; }
.decision-card small{ display:block; margin-top:4px; font-family:"Archivo",sans-serif; font-size:6.5pt;
  color:var(--muted); line-height:1.25; }
.dc-list{ margin:4px 0 0; padding-left:12px; font-size:7.2pt; line-height:1.28; }
.dc-list li{ margin:2px 0; }
.dc-list b{ font-family:"Archivo",sans-serif; text-transform:uppercase; font-size:5.8pt; color:var(--brass); }
.decision-card.action{ border-color:rgba(149,116,71,.34); background:rgba(149,116,71,.08); }

/* aside */
.prop-aside{ display:flex; flex-direction:column; gap:14px; }
.fin-card{ background:var(--forest); color:#F4EFE6; padding:15px 16px; }
.fin-h{ display:block; color:var(--brass-soft); margin-bottom:10px; }
.fin-row{ display:flex; justify-content:space-between; font-size:13px;
  padding:7px 0; border-bottom:1px solid rgba(244,239,230,.18); }
.fin-row:last-of-type{ border-bottom:none; }
.fin-row b{ font-family:"Fraunces",serif; font-weight:500; }
.fin-note{ font-size:11px; line-height:1.45; font-style:italic;
  color:rgba(244,239,230,.8); margin:10px 0 0; }
.dossier-visuals{ margin-top:auto; padding-top:22px; }
.visuals-lab{ display:block; margin-bottom:8px; }
.mosaic{ display:grid; grid-template-columns:repeat(4,1fr); grid-auto-rows:22mm;
  grid-auto-flow:row dense; gap:6px; }
.mz-floor{ grid-column:span 2; grid-row:span 2; border:1px solid var(--hair);
  background:var(--paper-2); padding:8px 11px; display:flex; flex-direction:column; }
.mz-floor .fp-lab{ margin-bottom:6px; }
.mz-feature{ grid-column:span 2; grid-row:span 2; background-size:cover;
  background-position:center; background-color:var(--paper-2); }
.mz-cell{ background-size:cover; background-position:center;
  background-color:var(--paper-2); }
.fp-lab{ display:block; }
.fp-img{ width:100%; flex:1; min-height:0; background-size:contain;
  background-repeat:no-repeat; background-position:center; background-color:#fff; }
.agent{ border-top:1px solid var(--hair); padding-top:12px;
  display:flex; flex-direction:column; gap:5px; }
.agent-name{ font-family:"Fraunces",serif; font-size:15px; }
.agent-agency{ display:block; font-family:"Archivo",sans-serif; font-size:9px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--brass); margin-top:2px; }
.agent-contact{ font-size:12px; color:var(--ink-soft); }
.insp{ background:var(--paper-2); border:1px solid var(--hair); padding:10px 12px; }
.insp-lab{ display:block; margin-bottom:4px; }
.insp{ font-size:13px; }
.listing-link{ font-family:"Archivo",sans-serif; font-size:9px; letter-spacing:.04em;
  color:var(--brass); word-break:break-all; }


/* ===================== CLOSING ===================== */
.closing{ background:var(--forest); color:#F4EFE6; display:flex;
  align-items:center; justify-content:center; }
.closing-inner{ text-align:center; max-width:48ch; }
.mast-mark.big{ font-size:26px; color:var(--brass-soft); display:block; margin-bottom:20px; }
.closing-line{ font-family:"Newsreader",serif; font-style:italic; font-size:15px;
  line-height:1.6; color:rgba(244,239,230,.9); }
.closing-mast{ display:block; margin-top:26px; font-family:"Archivo",sans-serif;
  letter-spacing:.42em; font-size:11px; color:var(--brass-soft); }
"""


# --------------------------------------------------------------------------- #
# Render to PDF
# --------------------------------------------------------------------------- #
def build_report(payload: Dict[str, Any], out_path: str | Path,
                 *, palette: str = "folio", keep_html: bool = False) -> Path:
    """Render the payload to a PDF at ``out_path``. Returns the path."""
    if sync_playwright is None:
        raise RuntimeError("Playwright not installed. ./venv/bin/pip install playwright")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_doc = render_html(payload, palette=palette)
    html_path = out_path.with_suffix(".html")
    html_path.write_text(html_doc, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # file:// so the remote font + image CDN URLs resolve over the network
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle", timeout=90_000)
        try:
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(800)
        except Exception:
            pass
        page.pdf(path=str(out_path), format="A4", print_background=True,
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()

    if not keep_html:
        html_path.unlink(missing_ok=True)

    _compress_pdf(out_path)
    return out_path


def _compress_pdf(path: Path, setting: str = "/screen") -> None:
    """Shrink the PDF in place via Ghostscript so it never trips upload limits.
    No-op if gs is missing or the result isn't smaller."""
    gs = shutil.which("gs")
    if not gs:
        return
    tmp = path.with_suffix(".gs.pdf")
    try:
        subprocess.run(
            [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
             f"-dPDFSETTINGS={setting}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
             f"-sOutputFile={tmp}", str(path)],
            check=True, timeout=180,
        )
        if tmp.exists() and 0 < tmp.stat().st_size < path.stat().st_size:
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except Exception:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Sample payload (real cached listing 2020878443) so we can render a real PDF
# --------------------------------------------------------------------------- #
def sample_payload() -> Dict[str, Any]:
    base = "https://rimh2.domainstatic.com.au"
    hero = (f"{base}/AYeELdchxzGdVnYAEyIyfjklhuI=/fit-in/1920x1080/"
            "filters:format(webp):quality(85):no_upscale()/"
            "2020878443_1_1_260528_115328-w1920-h1280")
    gallery = [
        f"{base}/-UXmnzZv5vNArUYDM5Ui6HjHqI4=/fit-in/1920x1080/filters:format(webp):quality(85):no_upscale()/2020878443_2_1_260528_115328-w1920-h1280",
        f"{base}/5fSXxhPuszvL-Hy2aedDSiRXJpo=/fit-in/1920x1080/filters:format(webp):quality(85):no_upscale()/2020878443_3_1_260528_115328-w1920-h1280",
        f"{base}/HMC-dYFbCPaeSvMPIh-D2OUc8No=/fit-in/1920x1080/filters:format(webp):quality(85):no_upscale()/2020878443_4_1_260528_115328-w1920-h1280",
        f"{base}/OJO_dr_uZsGhahU1TC7YulzHnho=/720x540/filters:format(webp):quality(85)/2020878443_5_1_260528_115328-w1920-h1280",
    ]
    floorplan = (f"{base}/K9lgVZbWXIY9G-4ff_NUysCf4eo=/fit-in/1920x1080/"
                 "filters:format(webp):quality(85):no_upscale()/"
                 "2020878443_11_3_260528_115328-w4000-h2829")
    return {
        "meta": {
            "title": "The Inner-South Dossier",
            "issue": "Vol. I · June 2026",
            "eyebrow": "A CURATED BUYING DOSSIER",
            "date": "3 June 2026",
            "prepared_for": "Ben",
            "prepared_by": "Dune 2 · Buying Agent",
            "standfirst": "A working shortlist for a low-maintenance two-bedder "
                          "within walking distance of Green Square — read for the "
                          "lifestyle, bought for the growth.",
            "closing": "Compiled by your buying agent on the Pi. Yields and outgoings "
                       "are estimates from comparable evidence — verify strata, council "
                       "and contract terms before offering.",
        },
        "brief": {
            "objective": "both",
            "budget_buy": 1_200_000,
            "budget_rent": 900,
            "beds": 2, "baths": 1, "cars": 1,
            "region": "Inner-south Sydney, walkable to the CBD",
            "prose": "A low-maintenance 2-bedroom apartment or small townhouse in "
                     "inner-south Sydney, ideally within walking distance of a train "
                     "station and good cafes. Owner-occupier first, but capital growth "
                     "matters.",
            "must_haves": ["Secure parking", "North-facing / good light",
                           "Balcony or courtyard", "Walk to a station"],
            "deal_breakers": ["Ground floor on a main road", "No parking",
                              "Flood / fire overlay"],
        },
        "market": {
            "standfirst": "Zetland and Waterloo are doing the heavy lifting in the "
                          "Green Square corridor: steady stock, two-bedders clustering "
                          "just under $1.05M, and rents holding firm on the back of "
                          "transport access.",
            "suburbs": [
                {"name": "Zetland", "median": "$1.05M", "range": "$0.95M–$1.20M",
                 "note": "Most active; new stock keeps pricing honest."},
                {"name": "Waterloo", "median": "$0.98M", "range": "$0.86M–$1.12M",
                 "note": "Slightly cheaper entry, older stock."},
                {"name": "Rosebery", "median": "$1.10M", "range": "$0.99M–$1.25M",
                 "note": "Tightly held; fewer two-bedders trade."},
            ],
            "sources": [
                {
                    "key": "sample_suburb_comps",
                    "label": "Stored sold comparable evidence",
                    "value": "sample payload",
                    "source_name": "Property Hunter local database",
                    "source_url": "data/property_hunter.sqlite3",
                    "observed_at": "2026-06-03T09:00:00+10:00",
                    "freshness_days": 90,
                }
            ],
        },
        "properties": [{
            "headline": "MGM Martin — Two-Bedroom in the 'Elite' Release",
            "address": "6/22 Gadigal Avenue, Zetland NSW 2017",
            "suburb": "Zetland",
            "price": "$950,000 – $1,045,000",
            "beds": 2, "baths": 2, "cars": 1, "property_type": "Apartment",
            "url": "https://www.domain.com.au/6-22-gadigal-avenue-zetland-nsw-2017-2020878443",
            "fit_score": 8.4,
            "verdict": "A textbook fit — single-level, light, secure parking, and a "
                       "five-minute walk to Green Square. Priced to actually transact.",
            "why_it_fits": "This is squarely on-brief: a single-level two-bedder in "
                "Victoria Park's Elite release, under 5km from the CBD and less than a "
                "kilometre's stroll to Green Square station. The covered balcony, "
                "stone kitchen and main-with-ensuite layout answer the low-maintenance, "
                "lifestyle-first mandate, and the secure basement car space clears the "
                "parking deal-breaker outright.\n\n"
                "Capital-growth story is the Green Square renewal precinct itself — "
                "still maturing, with retail (East Village), parks (Joynton, Gunyama "
                "aquatic centre) and transport all already delivered rather than "
                "promised. Building amenity (indoor pool, gym) is a genuine rentability "
                "edge if it ever shifts to an investment.",
            "highlights": [
                "Secure basement car space — clears the parking deal-breaker",
                "<1km walk to Green Square station",
                "Covered entertainer's balcony + main with ensuite",
                "Indoor pool & gym in-complex (rentability edge)",
                "Opposite Joynton Park — good for the dog",
            ],
            "caveat": "Price guide tops out at $1.045M — at the upper end it eats most "
                      "of the buy budget, so the strata figure matters. Confirm before offering.",
            "description": "Positioned within Victoria Park's sought-after 'Elite' "
                "development, this stylish apartment combines contemporary design, "
                "quality finishes and peaceful parkland views in a low-maintenance "
                "setting. Two generous bedrooms, single-level layout, covered balcony, "
                "stone kitchen with gas cooking, main with ensuite, internal laundry, "
                "air-conditioning, lift access and a secure car space.\n\n"
                "- Less than 1km to Green Square station\n"
                "- Next to East Village shopping & dining\n"
                "- Indoor pool and fully equipped gym in-complex",
            "features": ["Air Conditioning", "Built-In Wardrobes", "Pool", "Ensuite",
                         "Intercom", "Security Access", "Basement Parking",
                         "Close to Transport", "Close to Shops"],
            "images": {"hero": hero, "gallery": gallery, "floorplan": floorplan},
            "agency": {"name": "MGM Martin"},
            "agents": [{"name": "David Bettini", "mobile": "0420 361 827",
                        "email": "david@mgmmartin.com"}],
            "inspections": [{"start": "2026-06-06T10:45:00", "end": "2026-06-06T11:15:00"}],
            "financials": {
                "est_rent_weekly": 880, "gross_yield_pct": 4.4,
                "strata_quarterly": 1450, "council_annual": 1100,
                "notes": "Yield on the upper guide ($1.045M) at an $880/wk rent — "
                         "in line with the Zetland two-bedder band. Strata is an "
                         "estimate for a pool/gym building; confirm on the S184.",
            },
            "viability": {
                "score": 9.7, "band": "Exceptional fit", "lens": "owner_occupier",
                "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                               "yield_or_lifestyle": 0.92, "preference_match": 0.92,
                               "dealbreaker_penalty": 0.0},
            },
        }],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="report_builder.py")
    ap.add_argument("--payload", help="Path to a JSON payload file")
    ap.add_argument("--out", default=str(REPORTS_DIR / "property_folio.pdf"))
    ap.add_argument("--sample", action="store_true",
                    help="Render a sample report from the cached listing")
    ap.add_argument("--palette", default="folio", choices=list(PALETTES),
                    help="Colour palette")
    ap.add_argument("--palettes", action="store_true",
                    help="Render the sample once per palette (for comparison)")
    ap.add_argument("--keep-html", action="store_true")
    args = ap.parse_args(argv)

    if args.sample or args.palettes:
        payload = sample_payload()
    elif args.payload:
        payload = json.loads(Path(args.payload).read_text())
    else:
        ap.error("provide --sample or --payload")

    if args.palettes:
        for name in PALETTES:
            out = build_report(payload, REPORTS_DIR / f"folio_{name}.pdf",
                               palette=name, keep_html=args.keep_html)
            print(f"Wrote {out}")
        return 0

    out = build_report(payload, args.out, palette=args.palette, keep_html=args.keep_html)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
