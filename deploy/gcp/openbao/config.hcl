# OpenBao for one cell: file storage on a persistent disk, auto-unsealed with Cloud KMS (no manual unseal keys).
# The VM's service account needs roles/cloudkms.cryptoKeyEncrypterDecrypter on the seal key (gcp-platform module).
ui            = false
# No `disable_mlock` line: OpenBao dropped mlock support and refuses to start if the setting is present with false.

storage "file" {
  path = "/openbao/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true # reachable only on the compose network; never published to the host
}

seal "gcpckms" {
  project    = "env://GCP_PROJECT"
  region     = "env://GCP_LOCATION"
  key_ring   = "zid-seal"
  crypto_key = "openbao-unseal"
}

api_addr = "http://openbao:8200"
