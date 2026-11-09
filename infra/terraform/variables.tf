variable "environment" {
  type    = string
  default = "pilot"
}

variable "location" {
  description = "Azure region. UK South keeps data in the UK; use westeurope or northeurope for EU residency."
  type        = string
  default     = "uksouth"
}

variable "db_sku" {
  type    = string
  default = "GP_Standard_D2ds_v5"
}

variable "app_image" {
  description = "Container image for the API, worker and MCP server (built from the repo Dockerfile)."
  type        = string
}

variable "libreoffice_image" {
  description = "Container image for infra/libreoffice."
  type        = string
}

variable "clamav_image" {
  description = "ClamAV image, run as a sidecar next to the API so every upload is scanned."
  type        = string
  default     = "clamav/clamav:stable"
}

variable "public_base_url" {
  description = "Public URL of the API (and the bundled web app), e.g. https://review.example.com"
  type        = string
}

variable "app_base_url" {
  description = "Where people open the web app, if different from public_base_url (SSO redirects, email links)."
  type        = string
  default     = ""
}

variable "cors_origins" {
  description = "Comma-separated browser origins allowed to call the API directly. Empty: same-origin only."
  type        = string
  default     = ""
}

variable "model_provider" {
  type    = string
  default = "heuristic"
}

variable "model_id" {
  type    = string
  default = "claude-sonnet-5-5"
}

variable "model_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "model_base_url" {
  description = "Optional customer-owned model endpoint."
  type        = string
  default     = ""
}

variable "oidc_issuer" {
  type    = string
  default = ""
}

variable "oidc_audience" {
  description = "Client id of the web app registration (ID tokens are issued to it)."
  type        = string
  default     = ""
}

variable "oidc_client_secret" {
  description = "Client secret of the web app registration (optional: the browser flow uses PKCE)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "oidc_jwks_url" {
  description = "Optional: discovered from the issuer when empty."
  type        = string
  default     = ""
}

variable "password_login_enabled" {
  description = "Allow email and password sign-in alongside SSO."
  type        = bool
  default     = true
}

variable "bootstrap_admin_email" {
  description = "First admin (and platform administrator), created at first start-up if missing."
  type        = string
  default     = ""
}

variable "bootstrap_admin_password" {
  description = "Initial password for the first admin. Change it after signing in, then clear this variable."
  type        = string
  default     = ""
  sensitive   = true
}

variable "smtp_host" {
  type    = string
  default = ""
}

variable "smtp_username" {
  type    = string
  default = ""
}

variable "smtp_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "mail_from" {
  type    = string
  default = "Proof of Review <no-reply@localhost>"
}

variable "mcp_api_key" {
  description = "Source app API key for a hosted MCP server in submitter mode. Empty: no MCP container app."
  type        = string
  default     = ""
  sensitive   = true
}

variable "metrics_token" {
  description = "Bearer token required to scrape /metrics. Empty: /metrics is open (keep it behind the ingress)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "sentry_dsn" {
  type      = string
  default   = ""
  sensitive = true
}
