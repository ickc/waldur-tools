@echo off
rem The Windows half of scripts/activate.sh -- pixi runs this one on win-64 and
rem that one everywhere else. Safe to commit: it contains no secrets.
rem
rem It sets the same two defaults and stops there. `.envrc.local` is a shell
rem file and cmd.exe cannot source it, so the token is read straight out of it
rem by waldur_tools.config instead, which does that on every platform. The
rem workflow is therefore the same as on Unix: write the token into
rem .envrc.local, and the next command picks it up.

rem Defaults, overridable from the surrounding environment. Written straight
rem into the variable being defaulted rather than through a scratch name: this
rem runs in the caller's environment, cmd variable names are case-insensitive,
rem and a helper called `root` would quietly overwrite -- and then delete -- a
rem `ROOT` the caller had set. Activation only adds these two.
if not defined WALDUR_API_URL set "WALDUR_API_URL=https://portal-api.isambard.ac.uk"
if not defined WALDUR_CACHE_DIR if defined PIXI_PROJECT_ROOT set "WALDUR_CACHE_DIR=%PIXI_PROJECT_ROOT%\data"
if not defined WALDUR_CACHE_DIR set "WALDUR_CACHE_DIR=%CD%\data"
