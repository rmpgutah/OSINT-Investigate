"""Advanced API endpoints: NL search, similarity, recommendations, enrichment, freshness, plugins."""

from __future__ import annotations

import importlib
import inspect
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from osintsuite.db.models import Finding, ModuleRun, Target
from osintsuite.db.repository import Repository
from osintsuite.engine.investigation import InvestigationEngine
from osintsuite.modules.base import BaseModule
from osintsuite.web.dependencies import get_engine, get_repo

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================================
# 91. Natural Language Search
# ============================================================

# Simple entity-type keyword detection
_ENTITY_KEYWORDS = {
    "email": ["email", "mail", "@", "address"],
    "domain": ["domain", "website", "site", "url", "web"],
    "ip": ["ip", "address", "ipv4", "ipv6", "host"],
    "phone": ["phone", "number", "cell", "mobile", "tel"],
    "person": ["person", "name", "who", "individual", "people"],
    "username": ["user", "username", "account", "handle", "profile"],
    "organization": ["org", "company", "business", "corporation"],
}


def _parse_nl_query(text: str) -> dict:
    """Parse natural language query into structured search parameters."""
    text_lower = text.lower()
    words = set(re.findall(r'\w+', text_lower))

    detected_types = []
    for etype, keywords in _ENTITY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            detected_types.append(etype)

    # Extract potential search terms (remove common words)
    stop_words = {
        "find", "search", "show", "me", "all", "the", "with", "for",
        "that", "have", "has", "any", "about", "from", "related", "to",
        "in", "on", "at", "of", "and", "or", "is", "are", "was", "were",
        "a", "an", "this", "those", "their", "what", "which", "who",
    }
    search_terms = [w for w in words if w not in stop_words and len(w) > 1]

    return {
        "detected_types": detected_types,
        "search_terms": search_terms,
        "raw_query": text,
    }


@router.get("/nl-search")
async def natural_language_search(
    q: str,
    repo: Repository = Depends(get_repo),
):
    """Parse a natural language query and search findings."""
    parsed = _parse_nl_query(q)
    pattern = f"%{q}%"

    # Search findings by title and content
    stmt = select(Finding).where(
        or_(
            Finding.title.ilike(pattern),
            Finding.content.ilike(pattern),
        )
    )

    # Also search by individual terms
    for term in parsed["search_terms"][:5]:
        term_pattern = f"%{term}%"
        stmt = stmt.union(
            select(Finding).where(
                or_(
                    Finding.title.ilike(term_pattern),
                    Finding.content.ilike(term_pattern),
                )
            )
        )

    # Filter by detected entity types if any
    if parsed["detected_types"]:
        type_stmt = select(Finding).where(
            Finding.finding_type.in_(parsed["detected_types"])
        )
        for term in parsed["search_terms"][:3]:
            type_stmt = type_stmt.where(
                or_(
                    Finding.title.ilike(f"%{term}%"),
                    Finding.content.ilike(f"%{term}%"),
                )
            )

    result = await repo.session.execute(stmt.limit(50))
    findings = result.scalars().all()

    # Deduplicate by id
    seen = set()
    unique_findings = []
    for f in findings:
        if f.id not in seen:
            seen.add(f.id)
            unique_findings.append(f)

    return {
        "query": parsed,
        "results": [
            {
                "id": str(f.id),
                "target_id": str(f.target_id),
                "module_name": f.module_name,
                "source": f.source,
                "finding_type": f.finding_type,
                "title": f.title,
                "content": (f.content or "")[:300],
                "confidence": f.confidence,
                "is_flagged": f.is_flagged,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in unique_findings[:50]
        ],
        "total": len(unique_findings),
    }


# ============================================================
# 92. Finding Similarity
# ============================================================

def _tokenize(text: str) -> set[str]:
    """Simple word tokenizer for overlap computation."""
    if not text:
        return set()
    return set(re.findall(r'\w+', text.lower()))


def _word_overlap_score(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings."""
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


@router.get("/similar/{finding_id}")
async def find_similar(
    finding_id: uuid.UUID,
    min_score: float = 0.3,
    limit: int = 20,
    repo: Repository = Depends(get_repo),
):
    """Find findings with similar titles using word overlap."""
    source_finding = await repo.get_finding(finding_id)
    if not source_finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    source_text = (source_finding.title or "") + " " + (source_finding.content or "")[:200]

    # Get all findings in the same investigation (via target)
    target = await repo.get_target(source_finding.target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    # Get all targets in the same investigation
    all_targets = await repo.list_targets(target.investigation_id)
    target_ids = [t.id for t in all_targets]

    # Get all findings across these targets
    stmt = select(Finding).where(
        Finding.target_id.in_(target_ids),
        Finding.id != finding_id,
    )
    result = await repo.session.execute(stmt)
    all_findings = result.scalars().all()

    # Compute similarity scores
    similar = []
    for f in all_findings:
        candidate_text = (f.title or "") + " " + (f.content or "")[:200]
        score = _word_overlap_score(source_text, candidate_text)
        if score >= min_score:
            similar.append({
                "id": str(f.id),
                "target_id": str(f.target_id),
                "module_name": f.module_name,
                "title": f.title,
                "content": (f.content or "")[:200],
                "confidence": f.confidence,
                "similarity_score": round(score, 3),
            })

    similar.sort(key=lambda x: x["similarity_score"], reverse=True)
    return {
        "source": {
            "id": str(source_finding.id),
            "title": source_finding.title,
        },
        "similar": similar[:limit],
        "total_compared": len(all_findings),
    }


# ============================================================
# 95. Module Recommendation
# ============================================================

@router.get("/recommend/{target_id}")
async def recommend_modules(
    target_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
    engine: InvestigationEngine = Depends(get_engine),
):
    """Recommend modules that haven't been run yet for a target."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    # Get modules that have been run
    stmt = select(ModuleRun).where(
        ModuleRun.target_id == target_id,
        ModuleRun.status == "completed",
    )
    result = await repo.session.execute(stmt)
    completed_runs = result.scalars().all()
    completed_modules = {run.module_name for run in completed_runs}

    # Get all applicable modules
    recommendations = []
    for name, module in engine.modules.items():
        if target.target_type not in module.applicable_target_types():
            continue

        already_run = name in completed_modules
        reason = ""

        if not already_run:
            # Generate a reason based on target data
            reason = _generate_recommendation_reason(name, target)
            recommendations.append({
                "module_name": name,
                "description": module.description,
                "status": "not_run",
                "reason": reason,
                "priority": _recommendation_priority(name, target),
            })
        else:
            # Find the last run
            last_run = max(
                (r for r in completed_runs if r.module_name == name),
                key=lambda r: r.completed_at or r.started_at or datetime.min.replace(tzinfo=timezone.utc),
                default=None,
            )
            if last_run and last_run.completed_at:
                age = datetime.now(timezone.utc) - last_run.completed_at
                if age.days > 7:
                    recommendations.append({
                        "module_name": name,
                        "description": module.description,
                        "status": "stale",
                        "reason": f"Last run {age.days} days ago. New data may be available.",
                        "priority": "medium",
                        "last_run": last_run.completed_at.isoformat(),
                    })

    recommendations.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("priority", "low"), 3))
    return {
        "target": {"id": str(target.id), "label": target.label, "type": target.target_type},
        "completed_modules": list(completed_modules),
        "recommendations": recommendations,
    }


def _generate_recommendation_reason(module_name: str, target) -> str:
    """Generate a human-readable reason for recommending a module."""
    reasons = {
        "person_search": f"Search public records for {target.full_name or target.label}",
        "email_intel": f"Investigate email address {target.email}" if target.email else "Gather email intelligence",
        "phone_lookup": f"Look up phone number {target.phone}" if target.phone else "Phone intelligence not yet gathered",
        "domain_recon": "Perform domain reconnaissance and WHOIS lookup",
        "social_media": f"Search social media for {target.label}",
        "web_scraper": "Scrape web presence and public profiles",
        "ip_forensics": "Analyze IP address geolocation and ownership",
        "email_breach": f"Check breach databases for {target.email}" if target.email else "Check for data breaches",
        "username_osint": f"Search platforms for username {target.label}",
        "google_dork": f"Run targeted Google searches for {target.label}",
        "court_records": f"Search court records for {target.full_name or target.label}",
        "property_records": f"Look up property records for {target.full_name or target.label}",
    }
    return reasons.get(module_name, f"Run {module_name.replace('_', ' ')} analysis")


def _recommendation_priority(module_name: str, target) -> str:
    """Assign priority based on target data availability."""
    high_priority = {
        "person": ["person_search", "social_media", "court_records"],
        "email": ["email_intel", "email_breach", "google_dork"],
        "domain": ["domain_recon", "dns_history", "subdomain_enum", "tech_stack"],
        "phone": ["phone_lookup", "phone_disposable"],
        "username": ["username_osint", "social_media", "google_dork"],
        "ip": ["ip_forensics", "shodan_intel", "geolocation"],
        "organization": ["business_entity", "domain_recon", "google_dork"],
    }
    if module_name in high_priority.get(target.target_type, []):
        return "high"
    return "medium"


# ============================================================
# 96. Enrichment Pipeline
# ============================================================

@router.post("/enrich/{target_id}")
async def enrich_target(
    target_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
    engine: InvestigationEngine = Depends(get_engine),
):
    """Auto-enrich: analyze findings for actionable data and run follow-up modules."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    findings = await repo.get_findings_by_target(target_id)
    followup_modules = set()
    enrichment_reasons = []

    for f in findings:
        content = (f.content or "") + " " + (f.title or "")
        data = f.data or {}

        # Check for emails in findings
        emails_found = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', content)
        if emails_found and "email_intel" in engine.modules:
            followup_modules.add("email_intel")
            enrichment_reasons.append(f"Found email(s) in {f.module_name}: running email_intel")

        if emails_found and "email_breach" in engine.modules:
            followup_modules.add("email_breach")
            enrichment_reasons.append(f"Found email(s) in {f.module_name}: running email_breach")

        # Check for domains
        domains_found = re.findall(r'(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z]{2,})', content)
        if domains_found and "domain_recon" in engine.modules:
            followup_modules.add("domain_recon")
            enrichment_reasons.append(f"Found domain(s) in {f.module_name}: running domain_recon")

        if domains_found and "dns_history" in engine.modules:
            followup_modules.add("dns_history")

        # Check for IPs
        ips_found = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', content)
        if ips_found and "ip_forensics" in engine.modules:
            followup_modules.add("ip_forensics")
            enrichment_reasons.append(f"Found IP(s) in {f.module_name}: running ip_forensics")

        # Check for usernames or social media handles
        handles_found = re.findall(r'@([a-zA-Z0-9_]{3,})', content)
        if handles_found and "username_osint" in engine.modules:
            followup_modules.add("username_osint")
            enrichment_reasons.append(f"Found handle(s) in {f.module_name}: running username_osint")

    # Filter to only applicable modules
    applicable_followups = set()
    for mod_name in followup_modules:
        mod = engine.modules.get(mod_name)
        if mod and target.target_type in mod.applicable_target_types():
            applicable_followups.add(mod_name)

    # Run follow-up modules
    results = {}
    total_new_findings = 0
    for mod_name in applicable_followups:
        try:
            new_findings = await engine.run_module(target_id, mod_name)
            results[mod_name] = len(new_findings)
            total_new_findings += len(new_findings)
        except Exception as e:
            results[mod_name] = f"error: {str(e)}"

    return {
        "target_id": str(target_id),
        "existing_findings": len(findings),
        "followup_modules_identified": list(followup_modules),
        "modules_run": list(applicable_followups),
        "enrichment_reasons": enrichment_reasons,
        "results": results,
        "total_new_findings": total_new_findings,
    }


# ============================================================
# 97. Data Freshness
# ============================================================

@router.get("/freshness/{target_id}")
async def data_freshness(
    target_id: uuid.UUID,
    stale_days: int = 7,
    repo: Repository = Depends(get_repo),
    engine: InvestigationEngine = Depends(get_engine),
):
    """Check freshness of module data. Flag modules not run in N days as stale."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    stmt = select(ModuleRun).where(ModuleRun.target_id == target_id)
    result = await repo.session.execute(stmt)
    all_runs = result.scalars().all()

    # Group by module, find latest run
    latest_runs: dict[str, ModuleRun] = {}
    for run in all_runs:
        existing = latest_runs.get(run.module_name)
        run_time = run.completed_at or run.started_at
        existing_time = (existing.completed_at or existing.started_at) if existing else None
        if not existing or (run_time and existing_time and run_time > existing_time):
            latest_runs[run.module_name] = run

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=stale_days)
    module_status = []

    # Check all applicable modules
    for name, module in engine.modules.items():
        if target.target_type not in module.applicable_target_types():
            continue

        run = latest_runs.get(name)
        if not run:
            module_status.append({
                "module_name": name,
                "status": "never_run",
                "last_run": None,
                "is_stale": True,
                "days_since_run": None,
                "findings_count": 0,
            })
        else:
            run_time = run.completed_at or run.started_at
            days_since = (now - run_time).days if run_time else None
            is_stale = run_time < stale_threshold if run_time else True
            module_status.append({
                "module_name": name,
                "status": run.status,
                "last_run": run_time.isoformat() if run_time else None,
                "is_stale": is_stale,
                "days_since_run": days_since,
                "findings_count": run.findings_count,
            })

    stale_count = sum(1 for m in module_status if m["is_stale"])
    fresh_count = sum(1 for m in module_status if not m["is_stale"])

    return {
        "target_id": str(target_id),
        "stale_threshold_days": stale_days,
        "total_modules": len(module_status),
        "stale_count": stale_count,
        "fresh_count": fresh_count,
        "modules": sorted(module_status, key=lambda x: (not x["is_stale"], x["module_name"])),
    }


# ============================================================
# 100. Plugin System
# ============================================================

@router.get("/plugins")
async def list_plugins():
    """Scan the plugins/ directory for .py files that subclass BaseModule."""
    # Look for plugins dir relative to project root
    possible_paths = [
        Path.cwd() / "plugins",
        Path(__file__).parent.parent.parent.parent / "plugins",
        Path.home() / ".osintsuite" / "plugins",
    ]

    plugins_dir = None
    for p in possible_paths:
        if p.exists() and p.is_dir():
            plugins_dir = p
            break

    if not plugins_dir:
        return {
            "plugins_dir": str(possible_paths[0]),
            "exists": False,
            "plugins": [],
            "message": f"No plugins directory found. Create one at {possible_paths[0]} with .py files that subclass BaseModule.",
        }

    discovered = []
    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = py_file.stem
        try:
            spec = importlib.util.spec_from_file_location(
                f"osintsuite_plugin_{module_name}", str(py_file)
            )
            if not spec or not spec.loader:
                continue

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Find all classes that subclass BaseModule
            for attr_name, attr_val in inspect.getmembers(mod, inspect.isclass):
                if issubclass(attr_val, BaseModule) and attr_val is not BaseModule:
                    plugin_info = {
                        "file": py_file.name,
                        "class_name": attr_name,
                        "module_name": getattr(attr_val, "name", module_name),
                        "description": getattr(attr_val, "description", "No description"),
                        "target_types": [],
                    }
                    # Try to get target types
                    try:
                        instance = attr_val.__new__(attr_val)
                        plugin_info["target_types"] = instance.applicable_target_types()
                    except Exception:
                        pass

                    discovered.append(plugin_info)

        except Exception as e:
            discovered.append({
                "file": py_file.name,
                "class_name": None,
                "error": str(e),
            })

    return {
        "plugins_dir": str(plugins_dir),
        "exists": True,
        "plugins": discovered,
        "total": len(discovered),
    }
