output "api_url" {
  value = "https://${azurerm_container_app.api["api"].latest_revision_fqdn}"
}

output "public_base_url" {
  value = local.public_base_url
}

output "custom_domain_verification_id" {
  description = "Value of the asuid.<custom_domain> TXT record."
  value       = azurerm_container_app.api["api"].custom_domain_verification_id
}

output "mcp_url" {
  description = "Internal to the Container Apps environment unless mcp_external_ingress is true."
  value       = length(azurerm_container_app.mcp) > 0 ? "https://${azurerm_container_app.mcp[0].ingress[0].fqdn}/mcp" : null
}

output "mcp_http_token" {
  value     = length(azurerm_container_app.mcp) > 0 ? local.all_secrets["mcp-http-token"] : null
  sensitive = true
}

output "metrics_token" {
  value     = local.all_secrets["metrics-token"]
  sensitive = true
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "storage_account" {
  value = azurerm_storage_account.files.name
}

output "key_vault" {
  value = azurerm_key_vault.main.name
}

output "app_identity_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}
