# Graph Report - HITL-pipeline  (2026-11-24)

## Corpus Check
- 187 files · ~147,067 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 13 file(s) not represented in the graph (top: (none) 5, .css 5, .example 1)

## Summary
- 2949 nodes · 9887 edges · 117 communities (94 shown, 23 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 880 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `223cff51`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- make_tenant
- Reports.tsx
- Connectors.tsx
- task_for
- App.tsx
- collections_abc
- auth.py
- UserPrincipal
- jobs.py
- ingestion/__init__.py
- test_fix_auth_sso.py
- test_fix_ops_platform.py
- Workspace.tsx
- jobs.py
- Held.tsx
- auditor.py
- test_risk_and_canaries.py
- users.py
- Submission
- Canary
- ConnectorConfig
- api
- test_fix_ops_connectors.py
- JiraConnector
- routers/review.py
- test_ingestion.py
- TenantSettings.tsx
- submissions.py
- session_scope
- config.py
- User
- assurance.py
- main.tf
- worker.py
- environment
- model_gateway.py
- main.py
- package.json
- make_tenant
- scheduler.py
- AuditEvent
- get_settings
- deps.py
- sharepoint.py
- x-app-env
- get_settings
- api
- assurance.py
- sso.py
- ModelGateway
- routers/reports.py
- utcnow
- test_fix_ops_platform.py
- metrics.py
- test_fix_pipeline_model.py
- FakeRedis
- Settings
- portal.py
- conftest.py
- test_passwords.py
- test_fix_auth_sessions.py
- run.py
- Workflows.tsx
- oversight.py
- session.py
- docker-compose.yml
- services
- Span
- teams.py
- postgres
- Running Proof of Review locally
- set_transport
- models.py
- compilerOptions
- canary_generator.py
- tenant.py
- demo_agent.py
- Proof of Review
- env
- migrate
- set_gateway
- AuditLog.tsx
- storage.py
- mcp
- AzureBlobStorage
- sqlalchemy_orm
- healthcheck
- logging_setup.py
- FakeRedis
- minio
- postgres
- FakeSock
- netguard.py
- pytest
- CLAUDE.md
- proof-of-review
- test_workflow.py
- time
- test_fix_db_role.py
- test_fix_downloads_and_auditor_evidence.py
- auth_methods

## God Nodes (most connected - your core abstractions)
1. `make_tenant()` - 238 edges
2. `Submission` - 157 edges
3. `session_scope()` - 155 edges
4. `auth()` - 150 edges
5. `submit_text()` - 122 edges
6. `get_settings()` - 120 edges
7. `UserPrincipal` - 104 edges
8. `utcnow()` - 104 edges
9. `User` - 101 edges
10. `api()` - 90 edges

## Surprising Connections (you probably didn't know these)
- `Operations` --references--> `retention_days()`  [INFERRED]
  README.md → services/retention.py
- `test_migrations_match_models()` --uses--> `Base`  [INFERRED]
  tests/test_migrations.py → db/models.py
- `document_available()` --uses--> `Document`  [INFERRED]
  services/retention.py → db/models.py
- `mcp_session()` --indirect_call--> `http()`  [INFERRED]
  scripts/demo_agent.py → connectors/base.py
- `run_agent()` --indirect_call--> `status()`  [INFERRED]
  scripts/demo_agent.py → tests/test_workflow.py

## Import Cycles
- 2-file cycle: `services/jobs.py -> services/metrics.py -> services/jobs.py`

## Communities (117 total, 23 thin omitted)

### Community 0 - "make_tenant"
Cohesion: 0.06
Nodes (57): ModelCall, call_cost_usd(), price_for(), verify(), review(), submit_text(), skim_approve(), sstatus() (+49 more)

### Community 1 - "Reports.tsx"
Cohesion: 0.08
Nodes (67): react, BarChart(), ChartFrame(), DataTable(), Legend(), LegendItem, LiveReadout(), TableData (+59 more)

### Community 2 - "Connectors.tsx"
Cohesion: 0.09
Nodes (33): apiAll(), FieldDef, fieldsFor(), FieldType, KIND_INFO, KindInfo, kindTitle(), ORIGINS (+25 more)

### Community 3 - "task_for"
Cohesion: 0.05
Nodes (50): acknowledge_flag(), add_review_comment(), build(), get_review_feedback(), get_review_status(), list_review_tasks(), main(), mode() (+42 more)

### Community 4 - "App.tsx"
Cohesion: 0.06
Nodes (57): react-dom, react-router-dom, dependencies, react@18.3.1, react-dom@18.3.1, react-router-dom@6.28.0, ApiError, clearUserState() (+49 more)

### Community 5 - "collections_abc"
Cohesion: 0.07
Nodes (9): upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), upgrade() (+1 more)

### Community 6 - "auth.py"
Cohesion: 0.08
Nodes (40): effective_roles(), _auth_time(), change_own_password(), _choose_tenant(), dev_login(), DevLoginIn, _expires_in(), ForgotIn (+32 more)

### Community 7 - "UserPrincipal"
Cohesion: 0.10
Nodes (38): _app(), app_out(), AppIn, AppPatch, _check_webhook(), _clean(), _connector(), connector_kinds() (+30 more)

### Community 8 - "jobs.py"
Cohesion: 0.10
Nodes (16): backoff_seconds(), enqueue(), handler_name(), _load_jobs(), relay_one(), relay_outbox(), release_delayed(), retry_later() (+8 more)

### Community 9 - "ingestion/__init__.py"
Cohesion: 0.08
Nodes (36): Document, Section, Span, _candidate_spans(), decode_text_file(), extract_docx(), flush_section(), extract_pdf() (+28 more)

### Community 10 - "test_fix_auth_sso.py"
Cohesion: 0.11
Nodes (10): set_transport(), InternalTokenEndpointIdP, platform_idp(), public_dns(), _tenant_sso(), test_discovered_token_endpoint_on_a_private_address_is_refused(), test_private_issuer_is_refused_at_call_time(), test_upstream_errors_are_not_echoed() (+2 more)

### Community 11 - "test_fix_ops_platform.py"
Cohesion: 0.12
Nodes (6): S3Storage, AzureError, ClientError, FakeS3, test_s3_creates_the_bucket_only_when_it_is_missing(), test_s3_sends_the_encryption_header_only_when_configured()

### Community 12 - "Workspace.tsx"
Cohesion: 0.09
Nodes (31): activateOnKey(), CommentT, DocumentView(), FlagT, SectionT, SEV, SpanEl(), SpanT (+23 more)

### Community 13 - "jobs.py"
Cohesion: 0.15
Nodes (17): register(), OutboxMessage, _event_fields(), publish(), subscribe(), test_publishing_retries_when_redis_blips(), _age_rows(), FakeRedis (+9 more)

### Community 14 - "Held.tsx"
Cohesion: 0.08
Nodes (77): apiPage(), download(), qs(), Deliveries(), EVENTS, STATUSES, tone(), RefDoc (+69 more)

### Community 15 - "auditor.py"
Cohesion: 0.10
Nodes (18): require_roles(), dep(), stored_file(), Page, paginate(), set_headers(), slice_list(), auditor_only() (+10 more)

### Community 16 - "test_risk_and_canaries.py"
Cohesion: 0.27
Nodes (10): all_span_text(), canary_row(), open_task(), test_canary_caught_by_comment_is_revealed(), test_canary_is_injected_only_into_the_review_copy(), test_heuristic_risk_flags(), test_llm_failure_falls_back_to_heuristic(), test_missed_canary_is_revealed_as_calibration() (+2 more)

### Community 17 - "users.py"
Cohesion: 0.13
Nodes (27): _active_admins(), _actor(), _check_password(), _check_roles(), _check_teams(), create_team(), create_user(), erase_user() (+19 more)

### Community 18 - "Submission"
Cohesion: 0.09
Nodes (54): _stats(), ReviewTask, Submission, admins_may_review(), caps_for(), create_task(), eligible_reviewers(), estimate_effort() (+46 more)

### Community 19 - "Canary"
Cohesion: 0.11
Nodes (14): Canary, _alter_number(), canary_for_task(), catch_rate(), _fill(), inject(), _mirror_flags(), _pick() (+6 more)

### Community 20 - "ConnectorConfig"
Cohesion: 0.08
Nodes (26): _fingerprint(), on_decided(), checked_url(), config_of(), content_hash(), mark_writeback(), save_state(), writeback_done() (+18 more)

### Community 21 - "api"
Cohesion: 0.07
Nodes (38): api(), redeliver(), create(), save(), rotate(), disableOAuth(), setActive(), ROLE_INFO (+30 more)

### Community 22 - "test_fix_ops_connectors.py"
Cohesion: 0.13
Nodes (30): ConnectorError, lock_name(), poll_one(), poll_connector(), ado_connector(), fake_api(), FakeAdo, FakeGraph (+22 more)

### Community 24 - "routers/review.py"
Cohesion: 0.13
Nodes (20): request_channel(), UserPrincipal, logout_all(), ack_flag(), add_comment(), CommentIn, decide(), DecisionIn (+12 more)

### Community 25 - "test_ingestion.py"
Cohesion: 0.12
Nodes (24): docx_bytes(), pdf_bytes(), plain_text(), pptx_bytes(), txt_bytes(), canary_span(), find_task(), login() (+16 more)

### Community 26 - "TenantSettings.tsx"
Cohesion: 0.08
Nodes (55): Confirm(), CopyButton(), CopyField(), copyText(), Err(), errorText(), Ok(), Section() (+47 more)

### Community 27 - "submissions.py"
Cohesion: 0.12
Nodes (24): AppPrincipal, assurance_summary(), cancel_submission(), create(), create_submission(), _decode(), FileIn, get_evidence() (+16 more)

### Community 28 - "session_scope"
Cohesion: 0.09
Nodes (40): PasswordResetToken, session_scope(), cleanup_tokens(), auth(), _make_platform_admin(), test_erasure_scrubs_submission_metadata_and_reset_links(), test_list_pagination_headers(), test_tenant_admin_cannot_take_over_a_platform_admin() (+32 more)

### Community 29 - "config.py"
Cohesion: 0.11
Nodes (10): _b64(), burn_time(), check_policy(), _derive(), _dummy_hash(), hash_password(), PasswordPolicyError, set_password() (+2 more)

### Community 30 - "User"
Cohesion: 0.13
Nodes (31): Comment, Decision, FlagAcknowledgement, RiskFlag, TelemetryEvent, TenantScoped, User, reveal() (+23 more)

### Community 31 - "assurance.py"
Cohesion: 0.24
Nodes (12): bucket(), _buckets(), _count(), _empty_reviewer(), _filtered_subs(), _median(), overview(), breakdown() (+4 more)

### Community 32 - "main.tf"
Cohesion: 0.05
Nodes (91): azurerm_container_app.api, azurerm_container_app_custom_domain.api, azurerm_container_app_environment_certificate.api, azurerm_container_app_environment.main, azurerm_container_app.libreoffice, azurerm_container_app.mcp, azurerm_key_vault.main, azurerm_key_vault_secret.app (+83 more)

### Community 33 - "worker.py"
Cohesion: 0.11
Nodes (18): cli(), dead_list(), dead_purge(), dead_replay(), _decode(), _deliveries(), _ensure_group(), handle() (+10 more)

### Community 34 - "environment"
Cohesion: 0.18
Nodes (29): environment, POR_APP_DB_PASSWORD, POR_APP_DB_USER, POR_BOOTSTRAP_ADMIN_EMAIL, POR_BOOTSTRAP_ADMIN_PASSWORD, POR_CLAMAV_HOST, POR_CORS_ORIGINS, POR_DATABASE_URL (+21 more)

### Community 35 - "model_gateway.py"
Cohesion: 0.12
Nodes (8): _chain(), classify(), _classify_message(), is_output_error(), ModelUnavailable, _run_failed(), _status_of(), usage_of()

### Community 36 - "main.py"
Cohesion: 0.15
Nodes (10): check_production_settings(), healthz(), lifespan(), readyz(), signed_file(), spa(), stop_background(), verify_file_signature() (+2 more)

### Community 37 - "package.json"
Cohesion: 0.12
Nodes (20): @types/react, @types/react-dom, typescript, vite, @vitejs/plugin-react, devDependencies, @types/react@18.3.12, @types/react-dom@18.3.1 (+12 more)

### Community 38 - "make_tenant"
Cohesion: 0.09
Nodes (39): outbox(), pkce_challenge(), make_tenant(), test_platform_provider_needs_a_verified_email_to_link(), test_tenant_can_enforce_its_own_sso(), test_app_role_is_held_to_row_level_security(), challenge(), login() (+31 more)

### Community 39 - "scheduler.py"
Cohesion: 0.12
Nodes (16): polls(), acquire(), release(), _mark_ran(), now_too_soon(), periodic(), poll_connectors(), _ran_recently() (+8 more)

### Community 40 - "AuditEvent"
Cohesion: 0.14
Nodes (16): EvidencePack, _anchor(), _body(), _canonical(), compute_hash(), events_for(), head(), record() (+8 more)

### Community 41 - "get_settings"
Cohesion: 0.18
Nodes (8): _redis(), auth_limit(), dep(), client_ip(), enforce(), hit(), _local_hit(), test_forwarded_for_uses_the_address_the_proxy_appended()

### Community 42 - "deps.py"
Cohesion: 0.12
Nodes (15): _bearer(), current_user(), platform_admin(), require_app(), require_reviewer(), session_revocation_id(), oauth_token(), is_postgres() (+7 more)

### Community 43 - "sharepoint.py"
Cohesion: 0.12
Nodes (9): AzureDevOpsConnector, run_once(), _strip_html(), check(), http(), por_client(), _content_marker(), _pending_entry() (+1 more)

### Community 44 - "x-app-env"
Cohesion: 0.08
Nodes (24): x-app-env, POR_BOOTSTRAP_ADMIN_EMAIL, POR_BOOTSTRAP_ADMIN_PASSWORD, POR_CLAMAV_HOST, POR_CORS_ORIGINS, POR_DATABASE_URL, POR_ENV, POR_INLINE_JOBS (+16 more)

### Community 45 - "get_settings"
Cohesion: 0.17
Nodes (17): prometheus_metrics(), reviewer_pseudonym(), forgot_password(), settings_out(), Tenant, BootstrapError, ensure_initial_admin(), get_settings() (+9 more)

### Community 46 - "api"
Cohesion: 0.09
Nodes (23): command, condition, depends_on, ports, restart, volumes, condition, api (+15 more)

### Community 47 - "assurance.py"
Cohesion: 0.20
Nodes (12): AssuranceScore, compute(), dashboard(), table(), latest(), load_component(), _num(), policy_for() (+4 more)

### Community 48 - "sso.py"
Cohesion: 0.10
Nodes (27): read_payload(), sign_payload(), any_sso(), blocked_by_enforcement(), _call(), candidates(), _check_target(), complete() (+19 more)

### Community 49 - "ModelGateway"
Cohesion: 0.20
Nodes (14): batches(), build(), run(), Claim, ClaimList, RiskFlagList, RiskFlagOut, heuristic_claims() (+6 more)

### Community 50 - "routers/reports.py"
Cohesion: 0.19
Nodes (11): cost_report(), _csv(), filters(), overview(), _range(), reviewer_report(), sla_report(), _csv_safe() (+3 more)

### Community 51 - "utcnow"
Cohesion: 0.11
Nodes (19): utcnow(), WebhookDelivery, is_transient(), retry_delay(), document_available(), purge_submission(), retention_days(), RetentionError (+11 more)

### Community 52 - "test_fix_ops_platform.py"
Cohesion: 0.09
Nodes (16): BearerAuth, http_app(), dead_letter(), dead(), _free_port(), _libreoffice_app(), redis_mode(), s3_env() (+8 more)

### Community 53 - "metrics.py"
Cohesion: 0.27
Nodes (8): _escape(), _fmt_labels(), _gauges(), _key(), observe(), render(), _render_gauges(), _stream_gauges()

### Community 54 - "test_fix_pipeline_model.py"
Cohesion: 0.23
Nodes (10): _Agent, _llm(), _retry_now(), _Run, _sub(), test_classify_provider_errors(), __init__(), test_model_outage_is_retried_with_backoff_then_deferred() (+2 more)

### Community 56 - "Settings"
Cohesion: 0.14
Nodes (4): Settings, _split(), test_env_must_be_known(), test_cors_origins_come_from_settings()

### Community 57 - "portal.py"
Cohesion: 0.27
Nodes (10): cancel(), evidence_pdf(), get_mine(), list_mine(), list_workflows(), _mine(), portal_app(), scoped_key() (+2 more)

### Community 58 - "conftest.py"
Cohesion: 0.12
Nodes (20): CanaryLeakError, ack_all_flags(), read_fully(), test_evidence_pack_contents(), canary_for(), decide(), scenario(), test_guard_blocks_a_leaking_payload() (+12 more)

### Community 59 - "test_passwords.py"
Cohesion: 0.24
Nodes (15): add_user(), admin_h(), bootstrap_env(), login(), test_admin_adds_user_with_password_and_they_sign_in(), test_admin_cannot_reset_own_password_or_other_tenants(), test_bootstrap_creates_tenant_and_admin_once_on_startup(), test_bootstrap_never_changes_an_existing_user() (+7 more)

### Community 60 - "test_fix_auth_sessions.py"
Cohesion: 0.26
Nodes (12): claims(), login(), _oauth_app(), _oauth_token(), test_client_tokens_contract_and_audit(), test_connector_app_tokens_still_work(), test_logout_ends_the_whole_session_including_refreshed_and_client_tokens(), test_mcp_token_cannot_decide_where_only_the_workspace_may() (+4 more)

### Community 61 - "run.py"
Cohesion: 0.05
Nodes (59): convert(), _convert(), healthz(), _read_limited(), refuse_large_bodies(), _semaphore(), App, base_env() (+51 more)

### Community 62 - "Workflows.tsx"
Cohesion: 0.15
Nodes (15): BLANK, compact(), save(), FIELD_NAME, NumberInput(), numOrNull(), Policy, same() (+7 more)

### Community 63 - "oversight.py"
Cohesion: 0.13
Nodes (22): act(), ActionIn, _actions(), create_workflow(), dashboard(), _data(), _deferral(), _get() (+14 more)

### Community 64 - "session.py"
Cohesion: 0.39
Nodes (3): get_engine(), _make_engine(), reset_engine()

### Community 65 - "docker-compose.yml"
Cohesion: 0.20
Nodes (9): name, volumes, appdata, minio, pgdata, redis, x-app-build, context (+1 more)

### Community 66 - "services"
Cohesion: 0.12
Nodes (16): image, ports, build, ports, restart, image, ports, command (+8 more)

### Community 67 - "Span"
Cohesion: 0.25
Nodes (10): _approved_corpus(), engine_used(), flag_spans(), flag_submission(), heuristic_flags(), _numbers(), _persist(), _reference_text() (+2 more)

### Community 68 - "teams.py"
Cohesion: 0.32
Nodes (9): _card(), _configs(), _nudge_due(), on_deferred(), on_escalated(), on_task_assigned(), send_sla_nudges(), _wants() (+1 more)

### Community 69 - "postgres"
Cohesion: 0.15
Nodes (13): POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER, interval, retries, test, environment, healthcheck (+5 more)

### Community 70 - "Running Proof of Review locally"
Cohesion: 0.08
Nodes (19): Changing settings, Everyday commands, Running Proof of Review locally, Running the tests, Sign in, Start everything, Starting again from scratch, Submit and review on one MCP server (+11 more)

### Community 71 - "set_transport"
Cohesion: 0.07
Nodes (9): set_por_client_factory(), set_transport(), use(), FakePorApi, por_api(), install(), FakeServices, services() (+1 more)

### Community 72 - "models.py"
Cohesion: 0.10
Nodes (16): AuditImmutableError, Base, new_id(), _refuse_audit_delete(), _refuse_audit_update(), Team, TimestampMixin, main() (+8 more)

### Community 73 - "compilerOptions"
Cohesion: 0.14
Nodes (13): compilerOptions, isolatedModules, jsx, lib, module, moduleResolution, noEmit, noUnusedLocals (+5 more)

### Community 74 - "canary_generator.py"
Cohesion: 0.16
Nodes (11): build(), run(), sample_spans(), build(), run(), CanaryOut, load_prompt(), ModelGateway (+3 more)

### Community 75 - "tenant.py"
Cohesion: 0.12
Nodes (15): get_db(), _admin_reviewers(), _check_sso(), get_tenant(), PriceIn, purge_submission(), SsoIn, TenantSettingsIn (+7 more)

### Community 77 - "demo_agent.py"
Cohesion: 0.14
Nodes (13): api_tools(), demo_api_key(), main(), run_all(), mcp_session(), resolve_workflow(), run_agent(), submit_once() (+5 more)

### Community 78 - "Proof of Review"
Cohesion: 0.14
Nodes (14): Engineering rules (enforced by tests), Full stack (Docker), How a submission flows, Operations, Proof of Review, Quick start, Reports and costs, Repository layout (+6 more)

### Community 79 - "env"
Cohesion: 0.17
Nodes (6): reset(), reset_storage(), set_transport(), env(), sink(), WebhookSink

### Community 80 - "migrate"
Cohesion: 0.21
Nodes (12): build, context, dockerfile, postgres, build, build, command, depends_on (+4 more)

### Community 81 - "set_gateway"
Cohesion: 0.20
Nodes (7): CallRecord, record_call(), set_gateway(), Usage, usage_gateway(), UsageGateway, run_agent()

### Community 82 - "AuditLog.tsx"
Cohesion: 0.18
Nodes (11): AuditLog(), exportJson(), loadOlder(), Chain, dayStart(), Event, Filters, NO_FILTERS (+3 more)

### Community 83 - "storage.py"
Cohesion: 0.18
Nodes (4): LocalStorage, sign_file_key(), Storage, test_local_storage_refuses_keys_outside_its_root()

### Community 84 - "mcp"
Cohesion: 0.18
Nodes (11): POR_MCP_API_KEY, POR_MCP_API_URL, POR_MCP_HOST, POR_MCP_HTTP_TOKEN, POR_MCP_PORT, command, environment, ports (+3 more)

### Community 85 - "AzureBlobStorage"
Cohesion: 0.17
Nodes (4): AzureBlobStorage, FakeBlobService, FakeContainer, test_azure_storage_creates_the_container_only_when_missing()

### Community 86 - "sqlalchemy_orm"
Cohesion: 0.16
Nodes (8): create_tenant(), list_tenants(), tenant_out(), TenantIn, TenantPatch, update_tenant(), _drop_after_rollback(), _run_after_commit()

### Community 87 - "healthcheck"
Cohesion: 0.44
Nodes (10): healthcheck, healthcheck, interval, retries, start_period, test, timeout, healthcheck (+2 more)

### Community 88 - "logging_setup.py"
Cohesion: 0.31
Nodes (3): configure(), JsonFormatter, _RequestIdFilter

### Community 90 - "minio"
Cohesion: 0.25
Nodes (8): MINIO_ROOT_PASSWORD, MINIO_ROOT_USER, command, environment, image, ports, volumes, minio

### Community 91 - "postgres"
Cohesion: 0.25
Nodes (8): POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER, environment, image, ports, volumes, postgres

### Community 95 - "netguard.py"
Cohesion: 0.36
Nodes (3): _blocked(), check_url(), UnsafeTarget

### Community 96 - "pytest"
Cohesion: 0.18
Nodes (10): AuditEvent, _cases(), _creds(), test_api_key_and_oauth_client_credentials(), test_audit_chain_is_valid_and_detects_tampering(), test_audit_rows_cannot_be_updated_or_deleted_through_the_orm(), test_cross_tenant_cases_cover_every_route(), test_dev_login_and_roles() (+2 more)

### Community 108 - "test_workflow.py"
Cohesion: 0.29
Nodes (9): status(), test_cancel(), test_invalid_transitions_are_refused(), test_reject_at_first_stage_is_final(), test_request_changes_loop_with_feedback_and_resubmission(), test_sla_breach_escalates(), test_two_stage_flow_uses_different_reviewers(), test_workflow_versioning_pins_submissions() (+1 more)

### Community 109 - "time"
Cohesion: 0.10
Nodes (6): _canary_leak(), observe(), job(), deliver(), _azure_status(), _error_code()

### Community 113 - "test_fix_db_role.py"
Cohesion: 0.33
Nodes (4): ensure_app_role(), main(), test_nothing_to_do_on_sqlite(), test_role_name_is_validated()

### Community 115 - "test_fix_downloads_and_auditor_evidence.py"
Cohesion: 0.47
Nodes (3): _released(), test_auditor_evidence_record_uses_reviewer_pseudonyms(), test_evidence_pack_signed_link()

## Knowledge Gaps
- **1 isolated node(s):** `proof-of-review`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 886 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **23 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `api()` connect `api` to `Reports.tsx`, `Connectors.tsx`, `App.tsx`, `Workspace.tsx`, `Held.tsx`, `AuditLog.tsx`, `TenantSettings.tsx`, `Workflows.tsx`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Are the 114 inferred relationships involving `Submission` (e.g. with `list_submissions()` and `_sub()`) actually correct?**
  _`Submission` has 114 INFERRED edges - model-reasoned connections that need verification._
- **What connects `proof-of-review` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `make_tenant` be split into smaller, more focused modules?**
  _Cohesion score 0.06479081821547575 - nodes in this community are weakly interconnected._
- **Why does `make_tenant()` connect `make_tenant` to `make_tenant`, `task_for`, `auth.py`, `UserPrincipal`, `test_fix_auth_sso.py`, `test_risk_and_canaries.py`, `ConnectorConfig`, `test_fix_ops_connectors.py`, `test_ingestion.py`, `session_scope`, `User`, `get_settings`, `ModelGateway`, `utcnow`, `test_fix_ops_platform.py`, `test_fix_pipeline_model.py`, `conftest.py`, `test_passwords.py`, `test_fix_auth_sessions.py`, `oversight.py`, `models.py`, `pytest`, `test_workflow.py`, `test_fix_db_role.py`, `test_fix_downloads_and_auditor_evidence.py`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Should `Reports.tsx` be split into smaller, more focused modules?**
  _Cohesion score 0.08035087719298245 - nodes in this community are weakly interconnected._
- **Why does `get_settings()` connect `get_settings` to `collections_abc`, `auth.py`, `jobs.py`, `ingestion/__init__.py`, `test_fix_auth_sso.py`, `jobs.py`, `auditor.py`, `Submission`, `Canary`, `ConnectorConfig`, `test_ingestion.py`, `submissions.py`, `session_scope`, `config.py`, `User`, `worker.py`, `main.py`, `make_tenant`, `scheduler.py`, `get_settings`, `deps.py`, `sharepoint.py`, `sso.py`, `utcnow`, `test_fix_ops_platform.py`, `metrics.py`, `test_fix_pipeline_model.py`, `Settings`, `portal.py`, `conftest.py`, `test_passwords.py`, `test_fix_auth_sessions.py`, `run.py`, `session.py`, `teams.py`, `models.py`, `tenant.py`, `demo_agent.py`, `storage.py`, `logging_setup.py`, `netguard.py`, `time`, `test_fix_db_role.py`, `auth_methods`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._