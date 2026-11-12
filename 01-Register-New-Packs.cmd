@echo off
setlocal DisableDelayedExpansion
"%~dp0script\run-python.cmd" "%~dp0script\update_new_packs.py"  %*
