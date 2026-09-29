#!/usr/bin/env bash

set -euo pipefail

readonly REPO="AlanRockefeller/findphotodates.py"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
cd "$SCRIPT_DIR"

VERSION="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' findphotodates.py)"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
    echo "Unable to determine a valid version from findphotodates.py" >&2
    exit 1
fi
readonly VERSION
readonly TAG="v$VERSION"

if ! grep -Eq "^## ${TAG//./\\.}( |$)" CHANGELOG.md; then
    echo "CHANGELOG.md needs an entry for $TAG" >&2
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Commit all release changes before tagging." >&2
    exit 1
fi
if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
    echo "Tag $TAG already exists locally. Bump the version for a new release." >&2
    exit 1
fi
# Exit code 2 means the tag isn't there; anything else nonzero means the
# query itself failed (network, auth), so don't assume the tag is free.
rc=0
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null || rc=$?
if [[ $rc -eq 0 ]]; then
    echo "Tag $TAG already exists on origin. Bump the version for a new release." >&2
    exit 1
elif [[ $rc -ne 2 ]]; then
    echo "Unable to check origin for tag $TAG (git exit code $rc)." >&2
    exit 1
fi

echo "Building release $TAG for $REPO"
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
