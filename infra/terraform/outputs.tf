output "api_url" {
  value = "https://${azurerm_container_app.api["api"].latest_revision_fqdn}"
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "storage_account" {
  value = azurerm_storage_account.files.name
}
