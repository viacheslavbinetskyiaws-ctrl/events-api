#!/usr/bin/env bash
# Renders Kustomize overlays with SAMPLE cluster values (an account and
# region that are not this project's) and fails if any real or placeholder
# infrastructure value survives. A survivor means a manifest site is not
# covered by k8s/components/cluster-config-replacements, which would silently
# point that workload at stale or invalid infrastructure.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ $# -eq 0 ]]; then
  echo "usage: $0 <overlay-name> [<overlay-name> ...]   (names under k8s/overlays/)" >&2
  exit 2
fi

# Work on a copy so a real generated cluster-config.env is never clobbered.
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
cp -R "${root}/k8s" "${work}/k8s"
if [[ -d "${work}/k8s/components/cluster-config-source" ]]; then
  cp "${root}/scripts/cluster/sample-cluster-config.env" \
    "${work}/k8s/components/cluster-config-source/cluster-config.env"
fi

status=0
for overlay in "$@"; do
  if ! rendered="$(kubectl kustomize "${work}/k8s/overlays/${overlay}" 2>&1)"; then
    echo "FAIL ${overlay}: kustomize build error"
    echo "${rendered}"
    status=1
    continue
  fi
  bad=0
  for pattern in '938500344309' 'choe4u6ye3yf' 'eu-central-1' '000000000000' 'placeholder\.invalid' 'placeholder-region'; do
    if grep -nE -- "${pattern}" <<<"${rendered}"; then
      echo "FAIL ${overlay}: '${pattern}' survived rendering"
      bad=1
    fi
  done
  if [[ ${bad} -eq 0 ]]; then
    echo "ok   ${overlay}"
  else
    status=1
  fi
done
exit ${status}
