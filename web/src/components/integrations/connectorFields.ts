// Form fields per connector kind: the required/secret keys come from GET /v1/integrations/connector-kinds; labels,
// help text and optional settings follow the docstrings in connectors/<kind>.py.

export type FieldType = "text" | "url" | "secret" | "workflow" | "risk" | "origin" | "query" | "json" | "notify";

export type FieldDef = {
  key: string;
  label: string;
  type: FieldType;
  help?: string;
  placeholder?: string;
  // Sent when left empty (the connector reads these with a default and would break on null).
  fallback?: string;
  group?: "advanced";
};

export type KindInfo = { title: string; blurb: string; fields: FieldDef[] };

export const RISK_TIERS = ["low", "medium", "high"];
export const ORIGINS = ["ai", "human", "mixed"];
export const TEAMS_EVENTS = [
  { key: "assigned", label: "A reviewer is assigned an item" },
  { key: "escalated", label: "An item is escalated" },
  { key: "deferred", label: "An item is deferred" },
];

export const KIND_INFO: Record<string, KindInfo> = {
  sharepoint: {
    title: "SharePoint / OneDrive folder",
    blurb: "Documents dropped in the inbox folder are submitted for review. When decided, the decision is written back as file metadata and the file moves to the matching folder.",
    fields: [
      { key: "aad_tenant_id", label: "Microsoft Entra tenant ID", type: "text", placeholder: "00000000-0000-0000-0000-000000000000" },
      { key: "client_id", label: "App registration client ID", type: "text", help: "An Entra app registration with Microsoft Graph application access to read and write files in the drive." },
      { key: "client_secret", label: "Client secret", type: "secret" },
      { key: "drive_id", label: "Drive ID", type: "text", help: "The document library or OneDrive drive to watch (Graph: /sites/{site}/drives)." },
      { key: "workflow_id", label: "Workflow", type: "workflow" },
      { key: "risk_tier", label: "Risk tier for submitted files", type: "risk", fallback: "medium" },
      { key: "inbox_folder", label: "Inbox folder", type: "text", fallback: "Review/Inbox", group: "advanced" },
      { key: "approved_folder", label: "Approved folder", type: "text", fallback: "Review/Approved", group: "advanced" },
      { key: "rejected_folder", label: "Rejected folder", type: "text", fallback: "Review/Rejected", group: "advanced" },
      { key: "changes_folder", label: "Changes requested folder", type: "text", fallback: "Review/Changes requested", group: "advanced" },
      { key: "content_origin", label: "Content origin", type: "origin", fallback: "mixed", group: "advanced", help: "Who wrote the documents: AI, a person, or a mix." },
    ],
  },
  teams: {
    title: "Microsoft Teams notifications",
    blurb: "Posts Adaptive Cards to a Teams channel when reviewers are assigned work and when items are escalated or deferred. Decisions always happen in the review workspace.",
    fields: [
      { key: "webhook_url", label: "Teams webhook URL", type: "secret", help: "A Teams Workflows (Power Automate) or incoming-webhook URL for the channel. Stored encrypted." },
      { key: "app_base_url", label: "Reviewer app address (optional)", type: "url", placeholder: "https://review.example.com", help: "Where card buttons link to. Defaults to the platform's app URL." },
      { key: "notify", label: "Post a card when", type: "notify", fallback: "assigned,escalated,deferred" },
    ],
  },
  jira: {
    title: "Jira",
    blurb: "Decisions on linked submissions comment on the ticket, move it and attach the evidence pack. With a JQL query, matching tickets are submitted automatically.",
    fields: [
      { key: "base_url", label: "Jira site URL", type: "url", placeholder: "https://your-company.atlassian.net" },
      { key: "email", label: "Account email", type: "text", placeholder: "review-bot@example.com" },
      { key: "api_token", label: "API token", type: "secret", help: "An Atlassian API token for the account above." },
      { key: "submit_jql", label: "Submit tickets matching (JQL, optional)", type: "query",
        placeholder: 'project = LEGAL AND status = "Ready for review"',
        help: "Leave empty to only update tickets linked by the jira_issue_key metadata. Move tickets out of this status once submitted." },
      { key: "workflow_id", label: "Workflow", type: "workflow", help: "Needed when a JQL query is set." },
      { key: "risk_tier", label: "Risk tier for submitted tickets", type: "risk", fallback: "medium" },
      { key: "transitions", label: "Transitions after a decision (JSON, optional)", type: "json",
        placeholder: '{\n  "approved": "31",\n  "rejected": "41",\n  "changes_requested": "21"\n}',
        help: "Jira transition IDs per decision. Leave empty to only comment.", group: "advanced" },
    ],
  },
  azure_devops: {
    title: "Azure DevOps",
    blurb: "Decisions on linked work items add a history entry, move the item and attach the evidence pack. With a WIQL query, matching work items are submitted automatically.",
    fields: [
      { key: "organization", label: "Organization", type: "text", placeholder: "contoso" },
      { key: "project", label: "Project", type: "text", placeholder: "Legal" },
      { key: "pat", label: "Personal access token", type: "secret", help: "Needs Work Items (read & write)." },
      { key: "submit_wiql", label: "Submit work items matching (WIQL, optional)", type: "query",
        placeholder: "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Ready for review'",
        help: "Leave empty to only update work items linked by the ado_work_item_id metadata." },
      { key: "workflow_id", label: "Workflow", type: "workflow", help: "Needed when a WIQL query is set." },
      { key: "risk_tier", label: "Risk tier for submitted work items", type: "risk", fallback: "medium" },
      { key: "states", label: "States after a decision (JSON, optional)", type: "json",
        placeholder: '{\n  "approved": "Done",\n  "rejected": "Removed",\n  "changes_requested": "Active"\n}',
        help: "Work item state per decision. Leave empty to only add a history entry.", group: "advanced" },
      { key: "base_url", label: "Server URL (optional)", type: "url", placeholder: "https://dev.azure.com/<organization>",
        help: "Only for Azure DevOps Server or a custom domain.", group: "advanced" },
    ],
  },
};

const PRETTY: Record<string, string> = { aad_tenant_id: "Tenant ID", client_id: "Client ID", drive_id: "Drive ID", base_url: "Base URL", pat: "PAT" };

// The fields to show for a kind: known ones in order, plus any required/secret key the server lists that we don't know.
export function fieldsFor(kind: string, spec: { required: string[]; secret: string[] } | undefined): FieldDef[] {
  const known = KIND_INFO[kind]?.fields ?? [];
  const keys = new Set(known.map((f) => f.key));
  const extra: FieldDef[] = [...(spec?.required ?? []), ...(spec?.secret ?? [])]
    .filter((k, i, all) => !keys.has(k) && all.indexOf(k) === i)
    .map((k) => ({
      key: k,
      label: PRETTY[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
      type: k === "workflow_id" ? "workflow" : spec?.secret.includes(k) ? "secret" : "text",
    }));
  // A key the server marks secret is always a password field, whatever we thought.
  return [...known.map((f) => (spec?.secret.includes(f.key) && f.type !== "secret" ? { ...f, type: "secret" as const } : f)), ...extra];
}

export function kindTitle(kind: string) {
  return KIND_INFO[kind]?.title ?? kind.replace(/_/g, " ");
}
