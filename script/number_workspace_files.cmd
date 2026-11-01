@echo off
setlocal DisableDelayedExpansion
"%~dp0run-python.cmd" "%~dp0number_workspace_files.py" %*
