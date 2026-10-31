#!/usr/bin/env bash
# Sourced after the caller sets env_file. Keep the deployed overlays when replacing API containers.
compose=(docker compose --env-file "$env_file" -f deploy/compose.yaml)
overlays=(compose.redis-sentinel.yaml)
# Compose labels record the exact files used to create this project's API.
# Resolve their basenames in the current release, never source old release files.
api_container=$("${compose[@]}" -f deploy/compose.redis-sentinel.yaml ps -a -q api)
if [[ -n "$api_container" ]]; then
  config_files=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$api_container")
  IFS=',' read -r -a deployed_files <<< "$config_files"
  for file in "${deployed_files[@]}"; do
    name=${file##*/}
    case "$name" in
      compose.yaml|compose.redis-sentinel.yaml) continue ;;
      compose.ha.yaml|compose.web.yaml|compose.support.yaml|compose.monitoring.yaml)
        overlays+=("$name") ;;
      *)
        echo "Unknown deployed Compose file: $name; refusing to discard its settings." >&2
        exit 1 ;;
    esac
  done
fi
if [[ "${COMMERCE_HA:-0}" == 1 && " ${overlays[*]} " != *" compose.ha.yaml "* ]]; then
  overlays+=(compose.ha.yaml)
fi
for name in "${overlays[@]}"; do
  if [[ ! -f "deploy/$name" ]]; then
    echo "Missing deployed overlay: deploy/$name" >&2
    exit 1
  fi
  compose+=(-f "deploy/$name")
done
