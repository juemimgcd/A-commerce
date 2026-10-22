#!/usr/bin/env bash
# 发布版本来自 pyproject.toml；保持既有 tag/发布资产不被后续提交覆盖。
set -euo pipefail
cd "$(dirname "$0")/.."
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
if [[ ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "Use a stable X.Y.Z project version; v0 releases are marked prerelease." >&2
  exit 1
fi
tag="v$version"
: "${GITHUB_REPOSITORY:?Run inside GitHub Actions}"
: "${GITHUB_SHA:?Run inside GitHub Actions}"
image="ghcr.io/$(printf '%s' "$GITHUB_REPOSITORY" | tr '[:upper:]' '[:lower:]')"

case "${1:-}" in
  plan)
    publish=false
    if [[ "${PUBLISH_ALLOWED:-false}" == true ]]; then
      latest=$(git tag --list 'v[0-9]*' --sort=-v:refname | sed -n '1p')
      if [[ -n "$latest" && "$(printf '%s\n' "$latest" "$tag" | sort -V | tail -n 1)" != "$tag" ]]; then
        echo "Version $tag is older than $latest; bump pyproject.toml before releasing." >&2
        exit 1
      fi
      if ! git show-ref --verify --quiet "refs/tags/$tag"; then
        publish=true
      elif [[ "$(git rev-list -n 1 "$tag")" == "$GITHUB_SHA" ]]; then
        # 允许重跑失败的发布；已经发布完成的 Release 永不覆盖。
        draft=$(gh release view "$tag" --json isDraft --jq .isDraft)
        [[ "$draft" != true ]] || publish=true
      fi
    fi
    printf 'tag=%s\nimage=%s\npublish=%s\n' "$tag" "$image" "$publish" >> "$GITHUB_OUTPUT"
    ;;
  publish)
    [[ "${PUBLISH_ALLOWED:-false}" == true ]]
    [[ "${IMAGE_DIGEST:-}" =~ ^sha256:[a-f0-9]{64}$ ]]
    mkdir -p dist/deployment
    git archive HEAD deploy .env.example scripts/configure_local.py docs/releasing.md | tar -x -C dist/deployment
    printf 'COMMERCE_IMAGE=%s@%s\n' "$image" "$IMAGE_DIGEST" > dist/deployment/image.env
    tar -czf "dist/a-commerce-$tag-deploy.tar.gz" -C dist/deployment .
    git archive --format=tar.gz --prefix="a-commerce-$tag/" -o "dist/a-commerce-$tag-source.tar.gz" HEAD
    (cd dist && sha256sum ./*.tar.gz > SHA256SUMS)
    cat > dist/release-notes.md <<EOF
Automated release of commit $GITHUB_SHA.

Container: \`$image:$tag\` (linux/amd64 and linux/arm64).
Immutable deployment image: \`$image@$IMAGE_DIGEST\`.

Download the deployment bundle and SHA256SUMS, then follow docs/releasing.md.
This release uses a simulated payment provider; production acceptance is pending.
EOF
    if gh release view "$tag" --json isDraft > dist/existing-release.json 2>dist/release-query.log; then
      [[ "$(jq -r .isDraft dist/existing-release.json)" == true ]]
    else
      prerelease=()
      [[ "$version" != 0.* ]] || prerelease=(--prerelease)
      gh release create "$tag" --target "$GITHUB_SHA" --title "$tag" --draft \
        "${prerelease[@]}" --notes-file dist/release-notes.md
    fi
    gh release upload "$tag" dist/*.tar.gz dist/SHA256SUMS --clobber
    gh release edit "$tag" --draft=false
    printf 'Published [%s](https://github.com/%s/releases/tag/%s)\n' "$tag" "$GITHUB_REPOSITORY" "$tag" >> "$GITHUB_STEP_SUMMARY"
    ;;
  *) echo "Usage: bash scripts/release.sh plan|publish" >&2; exit 2 ;;
esac
