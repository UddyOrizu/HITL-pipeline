export type SourceApp = {
  id: string; name: string; api_key_prefix: string; webhook_url: string | null; oauth_client_id: string | null;
  oauth_enabled: boolean; active: boolean; previous_key_valid_until: string | null; created_at: string;
};

// Returned once by create / rotate calls.
export type AppSecrets = SourceApp & { api_key?: string; webhook_secret?: string; oauth_client_secret?: string | null };

export type Workflow = { id: string; name: string; source_app_ids: string[] | null };

export type KindSpec = { required: string[]; secret: string[]; polls: boolean | string };

export type Connector = {
  id: string; source_app_id: string; kind: string; active: boolean; config: Record<string, any>;
  polls: boolean; last_run_at: string | null; last_error: string | null;
};

export type Delivery = {
  id: string; source_app_id: string; source_app: string | null; submission_id: string | null; event: string;
  status: string; attempts: number; last_status_code: number | null; last_error: string | null; created_at: string;
  next_attempt_at: string | null; delivered_at: string | null;
};

export type Loaded<T> = { data: T | null; error: unknown; loading: boolean; reload: () => void };

export const MASK = "***";

export function fullTime(iso: string | null | undefined) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
