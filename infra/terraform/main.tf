# Proof of Review on Azure (UK South by default): Container Apps, Postgres Flexible Server with TimescaleDB,
# Azure Cache for Redis, Blob storage (via the S3-compatible setting or a gateway), Key Vault for secrets.
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
}

locals {
  name = "por-${var.environment}"
  tags = { app = "proof-of-review", environment = var.environment }
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

resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_redis_cache" "main" {
  name                 = "redis-${local.name}"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  capacity             = 0
  family               = "C"
  sku_name             = "Basic"
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
  tags                            = local.tags
}

resource "azurerm_storage_container" "files" {
  name                  = "por-files"
  storage_account_id    = azurerm_storage_account.files.id
  container_access_type = "private"
}

# --- Secrets ----------------------------------------------------------------------------

data "azurerm_client_config" "current" {}

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

# --- Compute ----------------------------------------------------------------------------

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

locals {
  database_url = "postgresql+psycopg://por:${random_password.db.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/por?sslmode=require"
  redis_url    = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:6380/0"
  app_env = {
    POR_ENV               = "prod"
    POR_PUBLIC_BASE_URL   = var.public_base_url
    POR_INLINE_JOBS       = "false"
    POR_LIBREOFFICE_URL   = "http://ca-${local.name}-libreoffice"
    POR_MODEL_PROVIDER    = var.model_provider
    POR_MODEL_ID          = var.model_id
    POR_OIDC_ISSUER       = var.oidc_issuer
    POR_OIDC_AUDIENCE     = var.oidc_audience
    POR_OIDC_JWKS_URL     = var.oidc_jwks_url
    POR_DEV_LOGIN_ENABLED = "false"
    POR_S3_BUCKET         = var.s3_bucket
    POR_S3_ENDPOINT       = var.s3_endpoint
  }
  app_secrets = {
    "database-url"  = local.database_url
    "redis-url"     = local.redis_url
    "secret-key"    = random_password.secret_key.result
    "model-api-key" = var.model_api_key
    "s3-access-key" = var.s3_access_key
    "s3-secret-key" = var.s3_secret_key
  }
  secret_env = {
    POR_DATABASE_URL  = "database-url"
    POR_REDIS_URL     = "redis-url"
    POR_SECRET_KEY    = "secret-key"
    POR_MODEL_API_KEY = "model-api-key"
    POR_S3_ACCESS_KEY = "s3-access-key"
    POR_S3_SECRET_KEY = "s3-secret-key"
  }
}

resource "azurerm_container_app" "api" {
  for_each                     = { api = ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port 8000"], worker = ["python", "-m", "services.worker"] }
  name                         = "ca-${local.name}-${each.key}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  dynamic "secret" {
    for_each = { for k, v in local.app_secrets : k => v if v != "" }
    content {
      name  = secret.key
      value = secret.value
    }
  }

  template {
    min_replicas = 1
    max_replicas = each.key == "api" ? 4 : 2
    container {
      name    = each.key
      image   = var.app_image
      cpu     = 1
      memory  = "2Gi"
      command = each.value
      dynamic "env" {
        for_each = local.app_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = { for k, v in local.secret_env : k => v if local.app_secrets[v] != "" }
        content {
          name        = env.key
          secret_name = env.value
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
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
