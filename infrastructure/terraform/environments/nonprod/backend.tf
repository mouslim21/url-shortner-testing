# Account-specific bucket and region are supplied with -backend-config.
terraform {
  backend "s3" {
    key          = "nonprod/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
