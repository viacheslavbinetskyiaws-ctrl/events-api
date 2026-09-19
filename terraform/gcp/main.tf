# Applied locally (Google Application Default Credentials: `gcloud auth
# application-default login`) until Task 9 gives CI keyless GCP access; until
# then it is deliberately absent from .github/workflows/terraform.yaml's
# plan/apply matrix. Everything here is permanent (prevent_destroy) and
# changes rarely.
module "gcp_wif" {
  source = "../modules/gcp_wif"
}
