"""SQLAlchemy models for every entity in the Proof of Review data model.

Every table carries tenant_id and timestamps. Ids are UUID strings.
Datetimes are stored as naive UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TenantScoped(TimestampMixin):
    """Marker mixin: tables with a tenant_id column get row-level security on Postgres."""

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)


# --- Tenancy and identity ---------------------------------------------------------------


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    region: Mapped[str] = mapped_column(String(20), default="uk", nullable=False)
    # allow_admin_reviewers, retention_days, currency, usd_to_currency, default_hourly_rate, model_prices,
    # notifications {...}, sso {issuer, client_id, client_secret (encrypted), domains, auto_provision_roles, ...}
    settings: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    # A deactivated tenant's users and source apps can no longer sign in or call the API.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)


class Team(TenantScoped, Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    daily_ceiling_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Reviewer cost per hour for members of this team (cost reports), unless the user has their own rate.
    hourly_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class User(TenantScoped, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # reviewer, oversight_lead, auditor, integration_owner, admin
    roles: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    team_ids: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Local password (scrypt), for sign-in without SSO. None means the user signs in with SSO only.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Tokens issued before this moment are rejected, so a password change signs out every session.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # "Sign out everywhere": tokens issued before this moment are rejected.
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Platform operators can create and manage tenants (/v1/platform). Set for the bootstrap admin.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    hourly_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # overrides the team and tenant rates
    erased_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # personal data removed (GDPR)


class SourceApp(TenantScoped, Base):
    __tablename__ = "source_apps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    oauth_client_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    oauth_client_secret_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    webhook_secret: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # api: an external application. portal: the built-in web upload app, one per tenant.
    kind: Mapped[str] = mapped_column(String(20), default="api", server_default="api", nullable=False)
    # After a key rotation the previous key keeps working until previous_key_expires_at (grace period).
    previous_key_prefix: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    previous_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_key_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --- Workflows --------------------------------------------------------------------------


class Workflow(TenantScoped, Base):
    """One row per version. family_id is the stable workflow id exposed to source apps."""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("family_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # [{"name": str, "team_id": str | None, "sla_hours": float}]
    stages: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    canary_rate: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    canary_domain: Mapped[str] = mapped_column(String(50), default="general", nullable=False)
    telemetry_policy: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    cap_policy: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    spot_check_rate: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    risk_policy: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    assurance_policy: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    source_app_ids: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)


# --- Submissions and documents ----------------------------------------------------------


class Submission(TenantScoped, Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("tenant_id", "source_app_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflows.id"), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_app_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_apps.id"), index=True, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_origin: Mapped[str] = mapped_column(String(10), default="ai", nullable=False)  # ai | human | mixed
    risk_tier: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)  # low | medium | high
    ai_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # received, processing, in_review, spot_check, approved, rejected, changes_requested, deferred, cancelled
    status: Mapped[str] = mapped_column(String(30), default="received", index=True, nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict, nullable=False)
    previous_submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hold_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    final_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # next processing attempt
    # Content removed under the tenant's retention policy (or on request). Decisions, scores, audit and
    # the evidence pack are kept.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    documents: Mapped[list[Document]] = relationship(back_populates="submission", order_by="Document.created_at")


class Document(TenantScoped, Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="primary", nullable=False)  # primary | reference
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_file_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    render_kind: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text | doc | slides | pages

    submission: Mapped[Submission] = relationship(back_populates="documents")


class Section(TenantScoped, Base):
    __tablename__ = "sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # body | page | slide
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    location: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)


class Span(TenantScoped, Base):
    __tablename__ = "spans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True, nullable=False)
    section_id: Mapped[str] = mapped_column(String(36), ForeignKey("sections.id"), index=True, nullable=False)
    order: Mapped[float] = mapped_column(Float, nullable=False)
    # paragraph, heading, list_item, table, title, notes, comment
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    # True only for canary spans: they exist in the review copy and never in the original.
    review_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replaces_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


# --- Controls ---------------------------------------------------------------------------


class RiskFlag(TenantScoped, Base):
    __tablename__ = "risk_flags"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    span_id: Mapped[str] = mapped_column(String(36), ForeignKey("spans.id"), index=True, nullable=False)
    # low_confidence, unsupported_figure, unresolved_citation, reference_mismatch, novel_claim, regulated_advice
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # low | medium | high
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    claim_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_tags: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)


class Canary(TenantScoped, Base):
    __tablename__ = "canaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    span_id: Mapped[str] = mapped_column(String(36), ForeignKey("spans.id"), nullable=False)
    target_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # factual_error, internal_contradiction, fabricated_citation, policy_breach, absurd_statement
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False)  # easy | medium | hard
    injected_text: Mapped[str] = mapped_column(Text, nullable=False)
    caught: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    caught_by_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scored_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scored_reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewTask(TenantScoped, Base):
    __tablename__ = "review_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    stage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_name: Mapped[str] = mapped_column(String(200), default="Review", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="primary", nullable=False)  # primary | spot_check
    status: Mapped[str] = mapped_column(String(20), default="assigned", nullable=False)  # assigned | completed | cancelled
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effort_minutes: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    sla_escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TelemetryEvent(Base):
    """High-volume time series. Composite primary key so Timescale can partition on created_at."""

    __tablename__ = "telemetry_events"
    __table_args__ = (Index("ix_telemetry_task_time", "review_task_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True, default=utcnow)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    review_task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # document_opened, section_in_view, scroll_depth, flag_opened, comment_added, edit_made,
    # focus_lost, focus_gained, decision_submitted, heartbeat
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    section_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    client_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class FlagAcknowledgement(TenantScoped, Base):
    __tablename__ = "flag_acknowledgements"
    __table_args__ = (UniqueConstraint("review_task_id", "risk_flag_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id"), index=True, nullable=False)
    risk_flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("risk_flags.id"), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Comment(TenantScoped, Base):
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id"), index=True, nullable=False)
    span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="comment", nullable=False)  # comment | edit
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    suggested_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class Decision(TenantScoped, Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id"), unique=True, nullable=False)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)  # approve | reject | request_changes
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cited_span_ids: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    telemetry_summary: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    channel: Mapped[str] = mapped_column(String(10), default="api", server_default="api", nullable=False)  # ui | api | mcp


class AssuranceScore(TenantScoped, Base):
    __tablename__ = "assurance_scores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    below_threshold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AuditEvent(Base):
    """Append-only and hash-chained per tenant. Updates and deletes are refused."""

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("tenant_id", "seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class EvidencePack(TenantScoped, Base):
    __tablename__ = "evidence_packs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submissions.id"), index=True, nullable=False)
    file_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    json_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # The tenant audit chain's head (seq, hash) when the record was built. Kept outside the chain, so a full
    # verification can tell when events after an issued evidence pack were removed (tail truncation).
    audit_head_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_head_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


# --- Integrations -----------------------------------------------------------------------


class WebhookDelivery(TenantScoped, Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_app_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_apps.id"), index=True, nullable=False)
    submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConnectorConfig(TenantScoped, Base):
    __tablename__ = "connector_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_app_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_apps.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # sharepoint | teams | jira | azure_devops
    # Secret fields are stored encrypted (services/secretbox.py).
    config: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    state: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Usage, sessions and account recovery ----------------------------------------------


class ModelCall(TenantScoped, Base):
    """One LLM call through the model gateway, with token usage for cost reports."""

    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_tenant_time", "tenant_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    agent: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RevokedToken(TenantScoped, Base):
    """Sign-in tokens revoked before they expire (sign-out). Rows can be dropped once expires_at has passed."""

    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)


class PasswordResetToken(TenantScoped, Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --- Transactional outbox -----------------------------------------------------------------


class OutboxMessage(Base):
    """An event written in the same transaction as the change that caused it, then copied to Redis Streams.

    Platform plumbing rather than tenant data, so it has no tenant_id (and no row-level security): the payload is
    what the event handlers receive. Rows are pruned a few days after they were published."""

    __tablename__ = "event_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stream: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Append-only guard ------------------------------------------------------------------


class AuditImmutableError(RuntimeError):
    pass


@event.listens_for(AuditEvent, "before_update")
def _refuse_audit_update(mapper, connection, target):  # noqa: ARG001
    raise AuditImmutableError("audit_events is append-only; updates are not allowed")


@event.listens_for(AuditEvent, "before_delete")
def _refuse_audit_delete(mapper, connection, target):  # noqa: ARG001
    raise AuditImmutableError("audit_events is append-only; deletes are not allowed")


TENANT_TABLES = [
    t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.columns and t.name != "tenants"
]
