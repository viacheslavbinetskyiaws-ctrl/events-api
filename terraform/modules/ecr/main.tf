resource "aws_ecr_repository" "this" {
  for_each = toset(var.repository_names)

  name                 = "${var.name_prefix}-${each.key}"
  image_tag_mutability = "MUTABLE"

  # Ephemeral by design (2026-09-22 cost decision): this module now lives in
  # terraform/cluster and is destroyed on every `down`. Without force_delete,
  # `destroy` fails outright on a non-empty repository — confirmed against
  # the provider docs, not assumed — and every one of these repos holds
  # real images between cycles.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Caps unbounded growth while a cluster stays up across many merges (each
# push to main pushes 5 new image tags) — the repos themselves are ephemeral
# now, but for as long as one is alive it shouldn't grow forever.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
