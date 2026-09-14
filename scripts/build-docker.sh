#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

DOCKERFILE="$PROJECT_ROOT/dockerfiles/Dockerfile.drone"
IMAGE="drone:latest"

if [[ ! -f "$DOCKERFILE" ]]; then
  echo "Error: Dockerfile not found: $DOCKERFILE" >&2
  exit 1
fi

echo "Building Docker image..."
echo "  Dockerfile: $DOCKERFILE"
echo "  Image:      $IMAGE"
echo

docker build \
  -f "$DOCKERFILE" \
  -t "$IMAGE" \
  "$PROJECT_ROOT"

echo
echo "Docker image built successfully:"
echo "  $IMAGE"
