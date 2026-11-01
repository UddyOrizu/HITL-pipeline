"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-26 22:32:49.785132
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('actor', sa.String(length=200), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity', sa.String(length=50), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=True),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('prev_hash', sa.String(length=64), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'seq')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_entity_id'), ['entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('telemetry_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('review_task_id', sa.String(length=36), nullable=False),
    sa.Column('type', sa.String(length=40), nullable=False),
    sa.Column('span_id', sa.String(length=36), nullable=True),
    sa.Column('section_id', sa.String(length=36), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('client_ts', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', 'created_at')
    )
    with op.batch_alter_table('telemetry_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_telemetry_events_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('ix_telemetry_task_time', ['review_task_id', 'created_at'], unique=False)

    op.create_table('tenants',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('region', sa.String(length=20), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('source_apps',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('api_key_prefix', sa.String(length=16), nullable=False),
    sa.Column('api_key_hash', sa.String(length=128), nullable=False),
    sa.Column('oauth_client_id', sa.String(length=64), nullable=True),
    sa.Column('oauth_client_secret_hash', sa.String(length=128), nullable=True),
    sa.Column('webhook_url', sa.String(length=2000), nullable=True),
    sa.Column('webhook_secret', sa.String(length=128), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('oauth_client_id')
    )
    with op.batch_alter_table('source_apps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_source_apps_api_key_prefix'), ['api_key_prefix'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_apps_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('teams',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('daily_ceiling_minutes', sa.Integer(), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_teams_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('roles', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('team_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('oidc_subject', sa.String(length=255), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'email')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('workflows',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('family_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('stages', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('canary_rate', sa.Float(), nullable=False),
    sa.Column('canary_domain', sa.String(length=50), nullable=False),
    sa.Column('telemetry_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('cap_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('spot_check_rate', sa.Float(), nullable=False),
    sa.Column('risk_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('assurance_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('source_app_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('family_id', 'version')
    )
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_workflows_family_id'), ['family_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_workflows_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('connector_configs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_app_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('state', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['source_app_id'], ['source_apps.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('connector_configs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_connector_configs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('submissions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workflow_id', sa.String(length=36), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('source_app_id', sa.String(length=36), nullable=False),
    sa.Column('external_ref', sa.String(length=500), nullable=True),
    sa.Column('idempotency_key', sa.String(length=200), nullable=True),
    sa.Column('content_origin', sa.String(length=10), nullable=False),
    sa.Column('risk_tier', sa.String(length=10), nullable=False),
    sa.Column('ai_model', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('stage_index', sa.Integer(), nullable=False),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('previous_submission_id', sa.String(length=36), nullable=True),
    sa.Column('escalated', sa.Boolean(), nullable=False),
    sa.Column('hold_reason', sa.String(length=200), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('final_outcome', sa.String(length=30), nullable=True),
    sa.Column('final_reason', sa.Text(), nullable=True),
    sa.Column('released_at', sa.DateTime(), nullable=True),
    sa.Column('processing_error', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['source_app_id'], ['source_apps.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'source_app_id', 'idempotency_key')
    )
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submissions_source_app_id'), ['source_app_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_submissions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_submissions_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('webhook_deliveries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_app_id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=True),
    sa.Column('event', sa.String(length=50), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('last_status_code', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['source_app_id'], ['source_apps.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('webhook_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_source_app_id'), ['source_app_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('assurance_scores',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('components', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('below_threshold', sa.Boolean(), nullable=False),
    sa.Column('computed_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assurance_scores', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assurance_scores_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assurance_scores_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('filename', sa.String(length=500), nullable=False),
    sa.Column('original_file_uri', sa.String(length=2000), nullable=False),
    sa.Column('mime_type', sa.String(length=200), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('render_kind', sa.String(length=20), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('evidence_packs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('file_uri', sa.String(length=2000), nullable=False),
    sa.Column('json_uri', sa.String(length=2000), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('generated_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('evidence_packs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evidence_packs_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_packs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('review_tasks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('reviewer_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.Integer(), nullable=False),
    sa.Column('stage_name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.Column('due_at', sa.DateTime(), nullable=True),
    sa.Column('opened_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('effort_minutes', sa.Float(), nullable=False),
    sa.Column('sla_escalated', sa.Boolean(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('review_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_tasks_reviewer_id'), ['reviewer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_tasks_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_tasks_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('comments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('review_task_id', sa.String(length=36), nullable=False),
    sa.Column('span_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('suggested_text', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['review_task_id'], ['review_tasks.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_comments_review_task_id'), ['review_task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_comments_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('decisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('review_task_id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('cited_span_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('telemetry_summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('warnings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('decided_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['review_task_id'], ['review_tasks.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('review_task_id')
    )
    with op.batch_alter_table('decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_decisions_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_decisions_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('sections',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=1000), nullable=True),
    sa.Column('location', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sections_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sections_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('spans',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('section_id', sa.String(length=36), nullable=False),
    sa.Column('order', sa.Float(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('location', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('review_only', sa.Boolean(), nullable=False),
    sa.Column('replaces_span_id', sa.String(length=36), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.ForeignKeyConstraint(['section_id'], ['sections.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('spans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_spans_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_spans_section_id'), ['section_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_spans_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('canaries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('span_id', sa.String(length=36), nullable=False),
    sa.Column('target_span_id', sa.String(length=36), nullable=True),
    sa.Column('type', sa.String(length=40), nullable=False),
    sa.Column('difficulty', sa.String(length=10), nullable=False),
    sa.Column('injected_text', sa.Text(), nullable=False),
    sa.Column('caught', sa.Boolean(), nullable=True),
    sa.Column('caught_by_event_id', sa.String(length=36), nullable=True),
    sa.Column('scored_task_id', sa.String(length=36), nullable=True),
    sa.Column('scored_reviewer_id', sa.String(length=36), nullable=True),
    sa.Column('revealed_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['span_id'], ['spans.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('canaries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_canaries_scored_reviewer_id'), ['scored_reviewer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_canaries_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_canaries_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('risk_flags',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('span_id', sa.String(length=36), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('mandatory', sa.Boolean(), nullable=False),
    sa.Column('claim_text', sa.Text(), nullable=True),
    sa.Column('claim_tags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['span_id'], ['spans.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('risk_flags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_risk_flags_span_id'), ['span_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_risk_flags_submission_id'), ['submission_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_risk_flags_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('flag_acknowledgements',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('review_task_id', sa.String(length=36), nullable=False),
    sa.Column('risk_flag_id', sa.String(length=36), nullable=False),
    sa.Column('opened_at', sa.DateTime(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['review_task_id'], ['review_tasks.id'], ),
    sa.ForeignKeyConstraint(['risk_flag_id'], ['risk_flags.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('review_task_id', 'risk_flag_id')
    )
    with op.batch_alter_table('flag_acknowledgements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_flag_acknowledgements_review_task_id'), ['review_task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_flag_acknowledgements_tenant_id'), ['tenant_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('flag_acknowledgements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_flag_acknowledgements_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_flag_acknowledgements_review_task_id'))

    op.drop_table('flag_acknowledgements')
    with op.batch_alter_table('risk_flags', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_risk_flags_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_risk_flags_submission_id'))
        batch_op.drop_index(batch_op.f('ix_risk_flags_span_id'))

    op.drop_table('risk_flags')
    with op.batch_alter_table('canaries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_canaries_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_canaries_submission_id'))
        batch_op.drop_index(batch_op.f('ix_canaries_scored_reviewer_id'))

    op.drop_table('canaries')
    with op.batch_alter_table('spans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_spans_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_spans_section_id'))
        batch_op.drop_index(batch_op.f('ix_spans_document_id'))

    op.drop_table('spans')
    with op.batch_alter_table('sections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sections_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_sections_document_id'))

    op.drop_table('sections')
    with op.batch_alter_table('decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_decisions_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_decisions_submission_id'))

    op.drop_table('decisions')
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_comments_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_comments_review_task_id'))

    op.drop_table('comments')
    with op.batch_alter_table('review_tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_tasks_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_review_tasks_submission_id'))
        batch_op.drop_index(batch_op.f('ix_review_tasks_reviewer_id'))

    op.drop_table('review_tasks')
    with op.batch_alter_table('evidence_packs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evidence_packs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_evidence_packs_submission_id'))

    op.drop_table('evidence_packs')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documents_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_documents_submission_id'))

    op.drop_table('documents')
    with op.batch_alter_table('assurance_scores', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assurance_scores_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_assurance_scores_submission_id'))

    op.drop_table('assurance_scores')
    with op.batch_alter_table('webhook_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_status'))
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_source_app_id'))

    op.drop_table('webhook_deliveries')
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_submissions_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_submissions_status'))
        batch_op.drop_index(batch_op.f('ix_submissions_source_app_id'))

    op.drop_table('submissions')
    with op.batch_alter_table('connector_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_connector_configs_tenant_id'))

    op.drop_table('connector_configs')
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_workflows_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_workflows_family_id'))

    op.drop_table('workflows')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_teams_tenant_id'))

    op.drop_table('teams')
    with op.batch_alter_table('source_apps', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_source_apps_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_source_apps_api_key_prefix'))

    op.drop_table('source_apps')
    op.drop_table('tenants')
    with op.batch_alter_table('telemetry_events', schema=None) as batch_op:
        batch_op.drop_index('ix_telemetry_task_time')
        batch_op.drop_index(batch_op.f('ix_telemetry_events_tenant_id'))

    op.drop_table('telemetry_events')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_events_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_audit_events_entity_id'))

    op.drop_table('audit_events')
