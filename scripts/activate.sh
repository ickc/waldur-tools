# shellcheck shell=sh
#
# Sourced by pixi on every `pixi run` and `pixi shell`. Safe to commit: it
# contains no secrets, it only says where to find them.
#
# The token itself lives in .envrc.local, which is gitignored and never
# committed. Create it with:
#
#     echo 'export WALDUR_API_TOKEN=<your token>' > .envrc.local
#
# Portal tokens are short-lived (hours), so expect to rewrite that file often.
# pixi re-runs this script on every invocation rather than caching it, which is
# exactly what makes that workable: edit the file, and the next command picks
# up the new token with no `pixi shell` restart.

root="${PIXI_PROJECT_ROOT:-$PWD}"

# Defaults, overridable from the surrounding environment or .envrc.local.
: "${WALDUR_API_URL:=https://portal-api.isambard.ac.uk}"
: "${WALDUR_CACHE_DIR:=$root/data}"
export WALDUR_API_URL WALDUR_CACHE_DIR

if [ -f "$root/.envrc.local" ]; then
    . "$root/.envrc.local"
fi

unset root
