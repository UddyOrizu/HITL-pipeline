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
  description = "Container image for the API and worker (built from the repo Dockerfile)."
  type        = string
}

variable "libreoffice_image" {
  description = "Container image for infra/libreoffice."
  type        = string
}

variable "public_base_url" {
  type = string
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

variable "oidc_issuer" {
  type    = string
  default = ""
}

variable "oidc_audience" {
  type    = string
  default = ""
}

variable "oidc_jwks_url" {
  type    = string
  default = ""
}

variable "s3_endpoint" {
  description = "S3-compatible endpoint for file storage (for example a MinIO gateway in front of Blob storage)."
  type        = string
  default     = ""
}

variable "s3_bucket" {
  type    = string
  default = "por-files"
}

variable "s3_access_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "s3_secret_key" {
  type      = string
  default   = ""
  sensitive = true
}
