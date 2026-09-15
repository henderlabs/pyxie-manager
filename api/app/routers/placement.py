import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Node, PlacementAffinityRule, PveTarget
from pyxie_core.placement import get_node_note, get_node_tier, suggest_performance_tier

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/placement", tags=["placement"], dependencies=[Depends(get_current_user)])


@router.get("/node-tier-suggestions")
def node_tier_suggestions(db: Session = Depends(get_db)):
    """Live, read-only: current tiers (from policies) + a hardware-derived
    suggestion for performance_tier. Trust has no hardware signal, so its
    suggestion is always 'standard' -- an honest placeholder, not a guess."""
    nodes = db.query(Node).filter(Node.is_missing.is_(False)).all()
    out = {}
    by_cluster: dict = {}
    for n in nodes:
        by_cluster.setdefault(n.cluster_id, []).append(n)

    specs_by_node = {}
    for cluster_id, cluster_nodes in by_cluster.items():
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one()
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        if target is None:
            continue
        try:
            client, _cred = build_pve_client(db, target)
        except Exception:
            continue
        with client:
            specs_by_node.update(suggest_performance_tier(client, cluster_nodes))

    for n in nodes:
        spec = specs_by_node.get(n.id, {"suggested": "standard", "cpu_cores": None, "cpu_model": None})
        out[str(n.id)] = {
            "node_name": n.name,
            "current_performance_tier": get_node_tier(db, n.id, "performance"),
            "current_trust_tier": get_node_tier(db, n.id, "trust"),
            "current_notes": get_node_note(db, n.id),
            "suggested_performance_tier": spec["suggested"],
            "suggested_trust_tier": "standard",
            "cpu_cores": spec.get("cpu_cores"),
            "cpu_model": spec.get("cpu_model"),
        }
    return out


def _serialize_rule(r: PlacementAffinityRule) -> dict:
    return {
        "id": str(r.id),
        "rule_type": r.rule_type,
        "scope_type": r.scope_type,
        "workload_ids": [str(w) for w in r.workload_ids] if r.workload_ids else None,
        "tag": r.tag,
        "strict": r.strict,
        "description": r.description,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat(),
    }


@router.get("/affinity-rules")
def list_affinity_rules(db: Session = Depends(get_db)):
    return [_serialize_rule(r) for r in db.query(PlacementAffinityRule).order_by(PlacementAffinityRule.created_at.desc()).all()]


class AffinityRuleCreate(BaseModel):
    rule_type: str  # keep_together | keep_apart
    scope_type: str  # workload_pair | tag_group
    workload_ids: list[uuid.UUID] | None = None
    tag: str | None = None
    strict: bool = True
    description: str | None = None


def _validate_affinity_payload(payload: AffinityRuleCreate):
    if payload.rule_type not in ("keep_together", "keep_apart"):
        raise HTTPException(400, "rule_type must be keep_together or keep_apart")
    if payload.scope_type == "workload_pair" and (not payload.workload_ids or len(payload.workload_ids) != 2):
        raise HTTPException(400, "workload_pair rules need exactly 2 workload_ids")
    if payload.scope_type == "tag_group" and not payload.tag:
        raise HTTPException(400, "tag_group rules need a tag")


@router.post("/affinity-rules", dependencies=[Depends(require_admin)])
def create_affinity_rule(payload: AffinityRuleCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _validate_affinity_payload(payload)

    rule = PlacementAffinityRule(
        rule_type=payload.rule_type,
        scope_type=payload.scope_type,
        workload_ids=[str(w) for w in payload.workload_ids] if payload.workload_ids else None,
        tag=payload.tag,
        strict=payload.strict,
        description=payload.description,
        created_by=user.email,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    write_audit_event(
        db, event_category="settings", event_type="placement.affinity_rule.created",
        actor=user.email, actor_type="user",
        metadata={"rule_type": payload.rule_type, "scope_type": payload.scope_type},
    )
    return _serialize_rule(rule)


@router.put("/affinity-rules/{rule_id}", dependencies=[Depends(require_admin)])
def update_affinity_rule(
    rule_id: uuid.UUID, payload: AffinityRuleCreate, user=Depends(get_current_user), db: Session = Depends(get_db)
):
    _validate_affinity_payload(payload)
    rule = db.query(PlacementAffinityRule).filter(PlacementAffinityRule.id == rule_id).one_or_none()
    if rule is None:
        raise HTTPException(404, "rule not found")

    before = _serialize_rule(rule)
    rule.rule_type = payload.rule_type
    rule.scope_type = payload.scope_type
    rule.workload_ids = [str(w) for w in payload.workload_ids] if payload.workload_ids else None
    rule.tag = payload.tag
    rule.strict = payload.strict
    rule.description = payload.description
    db.commit()
    db.refresh(rule)
    write_audit_event(
        db, event_category="settings", event_type="placement.affinity_rule.updated",
        actor=user.email, actor_type="user",
        state_before=before, state_after=_serialize_rule(rule),
        metadata={"rule_id": str(rule.id)},
    )
    return _serialize_rule(rule)


@router.delete("/affinity-rules/{rule_id}", dependencies=[Depends(require_admin)])
def delete_affinity_rule(rule_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = db.query(PlacementAffinityRule).filter(PlacementAffinityRule.id == rule_id).one_or_none()
    if rule is None:
        raise HTTPException(404, "rule not found")
    db.delete(rule)
    db.commit()
    write_audit_event(db, event_category="settings", event_type="placement.affinity_rule.deleted", actor=user.email, actor_type="user")
    return {"status": "ok"}
