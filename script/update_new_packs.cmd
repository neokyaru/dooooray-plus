@echo off
setlocal DisableDelayedExpansion
"%~dp0run-python.cmd" "%~dp0update_new_packs.py" %*
