@echo off
rem The Windows half of scripts/activate.sh -- pixi runs this one on win-64 and
rem that one everywhere else. Safe to commit: it contains no secrets.
rem
rem It sets the same two defaults and stops there. `.envrc.local` is a shell
rem file and cmd.exe cannot source it, so the token is read straight out of it
rem by waldur_tools.config instead, which does that on every platform. The
rem workflow is therefore the same as on Unix: write the token into
rem .envrc.local, and the next command picks it up.

set "root=%PIXI_PROJECT_ROOT%"
if not defined root set "root=%CD%"

rem Defaults, overridable from the surrounding environment.
if not defined WALDUR_API_URL set "WALDUR_API_URL=https://portal-api.isambard.ac.uk"
if not defined WALDUR_CACHE_DIR set "WALDUR_CACHE_DIR=%root%\data"

set "root="
