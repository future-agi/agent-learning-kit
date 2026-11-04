#!/usr/bin/env bash
set -euo pipefail

# Usage: run.sh <packed-sdk.tgz> <preseeded-local-npm-cache-directory>
# Run inside the main/GCP egress-denied venue. --offline is NOT a firewall.
# The cache must contain typescript@5.9.3 and the SDK dependency closure.
# No npx, source aliases, workspace links, credentials, or install lifecycle scripts.
if [[ $# -ne 2 ]]; then
  printf '%s\n' 'FAIL usage: run.sh <packed-sdk.tgz> <local-npm-cache-directory>' >&2
  exit 2
fi

temporary=''
cleanup() {
  status=$?
  trap - EXIT
  if [[ $status -ne 0 ]]; then printf 'FAIL packed consumer (exit %s)\n' "$status" >&2; fi
  if [[ -n "$temporary" ]]; then rm -rf -- "$temporary"; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

kit=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
tarball=$(CDPATH= cd -- "$(dirname -- "$1")" && printf '%s/%s' "$(pwd -P)" "$(basename -- "$1")")
supplied_cache=$(CDPATH= cd -- "$2" && pwd -P)
[[ -f "$tarball" ]] || { printf '%s\n' 'FAIL tarball is not a regular file' >&2; exit 2; }
node_bin=$(command -v node)
npm_bin=$(command -v npm)
[[ -x "$node_bin" && -x "$npm_bin" ]] || { printf '%s\n' 'FAIL node and npm must be preinstalled' >&2; exit 2; }
# Resolve tools before environment scrubbing; no ambient NODE_OPTIONS/NODE_PATH survive.
tool_path="$(dirname -- "$node_bin"):$(dirname -- "$npm_bin"):/usr/bin:/bin"
temporary=$(mktemp -d "${TMPDIR:-/tmp}/alk-consumer.XXXXXX")
project="$temporary/project"
mkdir -p "$project/ts-consumer" "$temporary/home" "$temporary/logs" "$temporary/input"
# The caller cache is read-only input. Resolve only its top-level path, then copy
# without following nested links. Reject preserved links before npm can use them.
cp -R -P -- "$supplied_cache" "$temporary/npm-cache"
cache_links=$(find -P "$temporary/npm-cache" -type l -print -quit)
[[ -z "$cache_links" ]] || { printf '%s\n' 'FAIL npm cache must not contain symlinks' >&2; exit 2; }
# A read-only seed must still yield a writable private cache; never chmod the input.
chmod -R u+rwX "$temporary/npm-cache"
# Freeze the input locally before auditing/installing; never reread a changing producer path.
cp -- "$tarball" "$temporary/input/package.tgz"
tarball="$temporary/input/package.tgz"
printf '' > "$temporary/user.npmrc"
printf '' > "$temporary/global.npmrc"
printf '%s\n' '{"name":"alk-packed-consumer","private":true,"type":"module"}' > "$project/package.json"
cp -- "$kit/esm-consumer.mjs" "$project/esm-consumer.mjs"
cp -- "$kit/ts-consumer/index.ts" "$kit/ts-consumer/tsconfig.json" "$project/ts-consumer/"

clean() {
  env -i \
    PATH="$tool_path" HOME="$temporary/home" TMPDIR="$temporary" \
    NPM_CONFIG_USERCONFIG="$temporary/user.npmrc" \
    NPM_CONFIG_GLOBALCONFIG="$temporary/global.npmrc" \
    NPM_CONFIG_CACHE="$temporary/npm-cache" NPM_CONFIG_LOGS_DIR="$temporary/logs" \
    NPM_CONFIG_OFFLINE=true NPM_CONFIG_IGNORE_SCRIPTS=true \
    NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false NPM_CONFIG_UPDATE_NOTIFIER=false \
    "$@"
}

# Scripts live outside archive contents. Audit without extracting the tarball.
clean "$node_bin" "$kit/check-artifacts.cjs" "$tarball"
# Running from this empty project avoids inheriting a source checkout's .npmrc/workspace.
cd -- "$project"
clean "$npm_bin" install --prefix "$project" --cache "$temporary/npm-cache" --offline --ignore-scripts \
  --no-audit --no-fund --no-package-lock --save-exact "$tarball" 'typescript@5.9.3'
clean "$node_bin" --version
clean "$npm_bin" --version
clean "$node_bin" "$project/node_modules/typescript/bin/tsc" --version
clean "$node_bin" "$project/esm-consumer.mjs"
clean "$node_bin" "$project/node_modules/typescript/bin/tsc" --project "$project/ts-consumer/tsconfig.json"
printf '%s\n' 'PASS packed consumer: ESM runtime and strict NodeNext declarations (TypeScript 5.9.3)'
