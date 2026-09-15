"""Idempotent Phase 0 baseline seed: default org/site, provider category and
capability catalog data. Safe to call on every startup.
"""

from sqlalchemy.orm import Session

from pyxie_core.models import AppSettings, Capability, Organization, Policy, Provider, ProviderCategory, SafetyRule, Site
from pyxie_core.seed_data import CAPABILITIES, PROVIDER_CATEGORIES
from pyxie_core.safety_rules_seed import SAFETY_RULES

DEFAULT_POLICIES = [
    ("organization", "storage.warning_threshold_pct", {"pct": 85}),
    ("organization", "protection.max_backup_age_hours", {"hours": 48}),
    ("organization", "protection.require_for_all_workloads", {"enabled": False}),
    ("organization", "metrics.retention_days", {"days": 400}),
]

# Providers with no configured target in this lab, seeded so the Providers
# page can show their real implementation/validation status rather than a
# hardcoded frontend list.
STRUCTURAL_ONLY_PROVIDERS = [
    ("protection", "veeam", "Veeam Backup & Replication", "implemented", "not_tested"),
    ("protection", "commvault", "Commvault", "implemented", "not_tested"),
]


def seed_defaults(db: Session):
    for cat_id, description in PROVIDER_CATEGORIES:
        if not db.query(ProviderCategory).filter(ProviderCategory.id == cat_id).one_or_none():
            db.add(ProviderCategory(id=cat_id, description=description))
    db.flush()

    for cap_id, cat_id, description in CAPABILITIES:
        if not db.query(Capability).filter(Capability.id == cap_id).one_or_none():
            db.add(Capability(id=cap_id, category_id=cat_id, description=description))
    db.flush()

    for rule_id, title, description, category in SAFETY_RULES:
        if not db.query(SafetyRule).filter(SafetyRule.id == rule_id).one_or_none():
            db.add(SafetyRule(id=rule_id, title=title, description=description, category=category))
    db.flush()

    for category_id, provider_type, name, impl_status, live_status in STRUCTURAL_ONLY_PROVIDERS:
        existing = (
            db.query(Provider)
            .filter(Provider.category_id == category_id, Provider.provider_type == provider_type, Provider.instance_name == "unconfigured")
            .one_or_none()
        )
        if existing is None:
            db.add(
                Provider(
                    category_id=category_id,
                    provider_type=provider_type,
                    name=name,
                    instance_name="unconfigured",
                    enabled=False,
                    contract_version=1,
                    connection_health="unavailable",
                    implementation_status=impl_status,
                    live_validation_status=live_status,
                )
            )
    db.flush()

    org = db.query(Organization).filter(Organization.slug == "default").one_or_none()
    if org is None:
        org = Organization(name="Default Organization", slug="default")
        db.add(org)
        db.flush()

    site = db.query(Site).filter(Site.organization_id == org.id, Site.slug == "default").one_or_none()
    if site is None:
        site = Site(organization_id=org.id, name="Default Site", slug="default")
        db.add(site)
        db.flush()

    settings_row = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
    if settings_row is None:
        settings_row = AppSettings(id=1)
        db.add(settings_row)

    for scope_type, key, value in DEFAULT_POLICIES:
        exists = (
            db.query(Policy)
            .filter(Policy.scope_type == scope_type, Policy.scope_id.is_(None), Policy.key == key)
            .one_or_none()
        )
        if exists is None:
            db.add(Policy(scope_type=scope_type, scope_id=None, key=key, value=value))

    db.commit()
    return org, site
