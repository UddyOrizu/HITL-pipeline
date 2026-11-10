# Proof of Review on Azure (UK South by default): Container Apps, Postgres Flexible Server with TimescaleDB,
# Azure Cache for Redis, Blob storage (managed identity), Key Vault for every secret, ClamAV next to the API.
# This is a starting point for a pilot environment, not a hardened production landing zone.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.10" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "azurerm" {
  features {}
  # The storage account has shared keys turned off, so Terraform reaches its data plane with Entra ID too.
  storage_use_azuread = true
}

locals {
  name = "por-${var.environment}"
  tags = { app = "proof-of-review", environment = var.environment }

  # The API's own address in the Container Apps environment. Workers, connectors and the MCP server call it here
  # rather than through public_base_url, which may sit behind a custom domain, a WAF or a sign-in proxy.
  api_internal_url  = "https://ca-${local.name}-api.${azurerm_container_app_environment.main.default_domain}"
  # public_base_url wins; otherwise the custom domain; otherwise the API's default Container Apps address.
  custom_domain_url = var.custom_domain != "" ? "https://${var.custom_domain}" : ""
  public_base_url   = coalesce(var.public_base_url, local.custom_domain_url, local.api_internal_url)
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 90
  tags                = local.tags
}

# --- Data -------------------------------------------------------------------------------

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.name}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  administrator_login           = "por"
  administrator_password        = random_password.db.result
  sku_name                      = var.db_sku
  storage_mb                    = 65536
  backup_retention_days         = 35
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true
  zone                          = "1"
  tags                          = local.tags
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "TIMESCALEDB"
}

resource "azurerm_postgresql_flexible_server_configuration" "preload" {
  name      = "shared_preload_libraries"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "timescaledb"
}

resource "azurerm_postgresql_flexible_server_database" "por" {
  name      = "por"
  server_id = azurerm_postgresql_flexible_server.main.id
}

# The 0.0.0.0 rule admits every Azure service, from any subscription, to the database's login prompt. The Container
# Apps environment here has no virtual network, so its outbound addresses are not fixed and this rule is what lets
# the apps connect. For production, put the environment and the server in a virtual network (delegated subnets,
# a private DNS zone, public_network_access_enabled = false) and set postgres_allow_azure_services = false.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  count            = var.postgres_allow_azure_services ? 1 : 0
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "allowed" {
  for_each         = var.postgres_allowed_ip_ranges
  name             = each.key
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = each.value.start
  end_ip_address   = each.value.end
}

# Standard: a replicated primary/replica pair with an SLA, so a node restart does not drop the job and event
# streams. Basic is a single node with no SLA. Data persistence (RDB/AOF) needs Premium; without it a full
# cache outage loses queued messages (the scheduler re-queues submissions stuck in processing).
resource "azurerm_redis_cache" "main" {
  name                 = "redis-${local.name}"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  capacity             = var.redis_capacity
  family               = var.redis_sku == "Premium" ? "P" : "C"
  sku_name             = var.redis_sku
  minimum_tls_version  = "1.2"
  non_ssl_port_enabled = false
  tags                 = local.tags
}

resource "azurerm_storage_account" "files" {
  name                            = replace("st${local.name}files", "-", "")
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false # managed identity only; download links are user delegation SAS
  tags                            = local.tags
}

resource "azurerm_storage_container" "files" {
  name                  = "por-files"
  storage_account_id    = azurerm_storage_account.files.id
  container_access_type = "private"
}

# --- Identity and secrets ---------------------------------------------------------------
# The apps run as one user-assigned managed identity. Secrets live in Key Vault and Container Apps reads them
# through that identity; blob storage is reached the same way, so no storage keys exist in configuration.

data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_key_vault" "main" {
  name                       = "kv-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = true
  soft_delete_retention_days = 30
  rbac_authorization_enabled = true
  tags                       = local.tags
}

# Whoever runs Terraform writes the secrets; the app identity can only read them.
resource "azurerm_role_assignment" "kv_deployer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "kv_app" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "blob_app" {
  scope                = azurerm_storage_account.files.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# Needed for user delegation SAS (short-lived download links signed with the identity, not an account key).
resource "azurerm_role_assignment" "blob_delegator" {
  scope                = azurerm_storage_account.files.id
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "random_password" "encryption_key" {
  length  = 64
  special = false
}

# Production start-up refuses to run without a metrics token (while metrics are on); one is generated unless given.
resource "random_password" "metrics_token" {
  length  = 48
  special = false
}

# Bearer token for the hosted MCP server; generated unless given.
resource "random_password" "mcp_http_token" {
  length  = 48
  special = false
}

locals {
  database_url = "postgresql+psycopg://por:${random_password.db.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/por?sslmode=require"
  redis_url    = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:6380/0"

  # Key Vault secret name => value. Empty values are skipped.
  all_secrets = {
    "database-url"             = local.database_url
    "redis-url"                = local.redis_url
    "secret-key"               = random_password.secret_key.result
    "encryption-key"           = random_password.encryption_key.result
    "model-api-key"            = var.model_api_key
    "oidc-client-secret"       = var.oidc_client_secret
    "bootstrap-admin-password" = var.bootstrap_admin_password
    "smtp-password"            = var.smtp_password
    "mcp-api-key"              = var.mcp_api_key
    "mcp-http-token"           = var.mcp_api_key != "" ? coalesce(var.mcp_http_token, random_password.mcp_http_token.result) : ""
    "metrics-token"            = coalesce(var.metrics_token, random_password.metrics_token.result)
    "sentry-dsn"               = var.sentry_dsn
  }
  # Secrets only the MCP app reads.
  mcp_secrets = ["mcp-api-key", "mcp-http-token"]
  # Which setting reads which secret. The MCP key and token are only given to the MCP app.
  secret_env = {
    POR_DATABASE_URL             = "database-url"
    POR_REDIS_URL                = "redis-url"
    POR_SECRET_KEY               = "secret-key"
    POR_ENCRYPTION_KEY           = "encryption-key"
    POR_MODEL_API_KEY            = "model-api-key"
    POR_OIDC_CLIENT_SECRET       = "oidc-client-secret"
    POR_BOOTSTRAP_ADMIN_PASSWORD = "bootstrap-admin-password"
    POR_SMTP_PASSWORD            = "smtp-password"
    POR_METRICS_TOKEN            = "metrics-token"
    POR_SENTRY_DSN               = "sentry-dsn"
  }
  present_secrets = nonsensitive(toset([for k, v in local.all_secrets : k if v != ""]))

  app_env = {
    POR_ENV                       = "prod"
    POR_PUBLIC_BASE_URL           = local.public_base_url
    POR_INTERNAL_API_URL          = local.api_internal_url
    POR_APP_BASE_URL              = var.app_base_url
    POR_CORS_ORIGINS              = var.cors_origins
    POR_INLINE_JOBS               = "false"
    POR_LIBREOFFICE_URL           = "http://ca-${local.name}-libreoffice"
    POR_MODEL_PROVIDER            = var.model_provider
    POR_MODEL_ID                  = var.model_id
    POR_MODEL_BASE_URL            = var.model_base_url
    POR_OIDC_ISSUER               = var.oidc_issuer
    POR_OIDC_AUDIENCE             = var.oidc_audience
    POR_OIDC_JWKS_URL             = var.oidc_jwks_url
    POR_DEV_LOGIN_ENABLED         = "false"
    POR_PASSWORD_LOGIN_ENABLED    = tostring(var.password_login_enabled)
    POR_BOOTSTRAP_ADMIN_EMAIL     = var.bootstrap_admin_email
    POR_AZURE_STORAGE_ACCOUNT_URL = azurerm_storage_account.files.primary_blob_endpoint
    POR_AZURE_STORAGE_CONTAINER   = azurerm_storage_container.files.name
    POR_SMTP_HOST                 = var.smtp_host
    POR_SMTP_USERNAME             = var.smtp_username
    POR_MAIL_FROM                 = var.mail_from
    POR_LOG_FORMAT                = "json"
    POR_TRUST_FORWARDED_FOR       = "true" # the Container Apps ingress sets X-Forwarded-For
    AZURE_CLIENT_ID               = azurerm_user_assigned_identity.app.client_id
  }
}

resource "azurerm_key_vault_secret" "app" {
  for_each     = local.present_secrets
  name         = each.key
  value        = local.all_secrets[each.key]
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_deployer]
}

# --- Compute ----------------------------------------------------------------------------

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

locals {
  apps = {
    api    = ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
    worker = ["python", "-m", "services.worker"]
  }
}

resource "azurerm_container_app" "api" {
  for_each                     = local.apps
  name                         = "ca-${local.name}-${each.key}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags
  depends_on                   = [azurerm_role_assignment.kv_app, azurerm_role_assignment.blob_app]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dynamic "secret" {
    for_each = { for k, v in azurerm_key_vault_secret.app : k => v if !contains(local.mcp_secrets, k) }
    content {
      name                = secret.key
      key_vault_secret_id = secret.value.versionless_id
      identity            = azurerm_user_assigned_identity.app.id
    }
  }

  template {
    # Periodic tasks take a Redis lock, so several worker replicas are safe.
    min_replicas = 1
    max_replicas = each.key == "api" ? 4 : 2
    container {
      name    = each.key
      image   = var.app_image
      cpu     = 1
      memory  = "2Gi"
      command = each.value
      dynamic "env" {
        for_each = { for k, v in local.app_env : k => v if v != "" }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        # The API scans uploads with the ClamAV sidecar in the same replica.
        for_each = each.key == "api" ? { POR_CLAMAV_HOST = "localhost" } : {}
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = { for k, v in local.secret_env : k => v if contains(local.present_secrets, v) }
        content {
          name        = env.key
          secret_name = env.value
        }
      }
      # Start-up runs the migrations first (alembic upgrade head), so allow a few minutes before liveness applies.
      dynamic "startup_probe" {
        for_each = each.key == "api" ? [1] : []
        content {
          transport               = "HTTP"
          port                    = 8000
          path                    = "/healthz"
          initial_delay           = 10
          interval_seconds        = 30
          failure_count_threshold = 10
        }
      }
      dynamic "readiness_probe" {
        for_each = each.key == "api" ? [1] : []
        content {
          transport               = "HTTP"
          port                    = 8000
          path                    = "/readyz" # the database answers
          interval_seconds        = 10
          failure_count_threshold = 3
        }
      }
      dynamic "liveness_probe" {
        for_each = each.key == "api" ? [1] : []
        content {
          transport               = "HTTP"
          port                    = 8000
          path                    = "/healthz"
          interval_seconds        = 30
          failure_count_threshold = 3
        }
      }
    }
    dynamic "container" {
      for_each = each.key == "api" ? [1] : []
      content {
        name   = "clamav"
        image  = var.clamav_image
        cpu    = 1
        memory = "2Gi"
        # The replica only takes traffic once clamd listens (uploads are refused unscanned in prod). The first
        # virus database download can take several minutes.
        startup_probe {
          transport               = "TCP"
          port                    = 3310
          initial_delay           = 60
          interval_seconds        = 60
          failure_count_threshold = 10
        }
        readiness_probe {
          transport               = "TCP"
          port                    = 3310
          interval_seconds        = 30
          failure_count_threshold = 3
        }
        liveness_probe {
          transport               = "TCP"
          port                    = 3310
          interval_seconds        = 60
          failure_count_threshold = 5
        }
      }
    }
  }

  dynamic "ingress" {
    for_each = each.key == "api" ? [1] : []
    content {
      external_enabled = true
      target_port      = 8000
      traffic_weight {
        latest_revision = true
        percentage      = 100
      }
    }
  }
}

# Optional custom domain for the API (and bundled web app). With a PFX certificate it is bound here; without one,
# add an Azure managed certificate to the domain afterwards (portal, or az containerapp hostname bind), which
# Terraform then leaves alone. DNS: a CNAME to the app's default FQDN and an asuid TXT record (see outputs).
resource "azurerm_container_app_environment_certificate" "api" {
  count                        = var.custom_domain != "" && nonsensitive(var.custom_domain_certificate_pfx_base64) != "" ? 1 : 0
  name                         = "cert-${replace(var.custom_domain, ".", "-")}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  certificate_blob_base64      = var.custom_domain_certificate_pfx_base64
  certificate_password         = var.custom_domain_certificate_password
  tags                         = local.tags
}

resource "azurerm_container_app_custom_domain" "api" {
  count                                    = var.custom_domain != "" ? 1 : 0
  name                                     = var.custom_domain
  container_app_id                         = azurerm_container_app.api["api"].id
  container_app_environment_certificate_id = length(azurerm_container_app_environment_certificate.api) > 0 ? azurerm_container_app_environment_certificate.api[0].id : null
  certificate_binding_type                 = length(azurerm_container_app_environment_certificate.api) > 0 ? "SniEnabled" : null

  lifecycle {
    # A managed certificate is bound outside Terraform.
    ignore_changes = [certificate_binding_type, container_app_environment_certificate_id]
  }
}

# Hosted MCP server (submitter mode), only when an API key is given. Every request needs
# "Authorization: Bearer <mcp_http_token>" (output mcp_http_token). Its ingress is internal to the Container Apps
# environment unless mcp_external_ingress is true.
resource "azurerm_container_app" "mcp" {
  count                        = nonsensitive(var.mcp_api_key) != "" ? 1 : 0
  name                         = "ca-${local.name}-mcp"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags
  depends_on                   = [azurerm_role_assignment.kv_app]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  secret {
    name                = "mcp-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.app["mcp-api-key"].versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "mcp-http-token"
    key_vault_secret_id = azurerm_key_vault_secret.app["mcp-http-token"].versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  template {
    min_replicas = 1
    max_replicas = 2
    container {
      name    = "mcp"
      image   = var.app_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "mcp_server.server", "--http"]
      env {
        name  = "POR_MCP_API_URL"
        value = local.api_internal_url
      }
      env {
        name  = "POR_MCP_PORT"
        value = "8000"
      }
      env {
        name  = "POR_MCP_HOST"
        value = "0.0.0.0"
      }
      env {
        name        = "POR_MCP_API_KEY"
        secret_name = "mcp-api-key"
      }
      env {
        name        = "POR_MCP_HTTP_TOKEN"
        secret_name = "mcp-http-token"
      }
    }
  }

  ingress {
    external_enabled = var.mcp_external_ingress
    target_port      = 8000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

resource "azurerm_container_app" "libreoffice" {
  name                         = "ca-${local.name}-libreoffice"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags
  template {
    min_replicas = 1
    max_replicas = 2
    container {
      name   = "libreoffice"
      image  = var.libreoffice_image
      cpu    = 1
      memory = "2Gi"
    }
  }
  ingress {
    external_enabled = false
    target_port      = 8100
    # The API calls http://ca-<name>-libreoffice inside the environment; without this, plain http is redirected
    # to https and the conversion request fails.
    allow_insecure_connections = true
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
