output "api_url" {
  value = "https://${azurerm_container_app.api["api"].latest_revision_fqdn}"
}

output "mcp_url" {
  value = length(azurerm_container_app.mcp) > 0 ? "https://${azurerm_container_app.mcp[0].latest_revision_fqdn}/mcp" : null
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
