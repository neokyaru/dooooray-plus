@echo off
setlocal DisableDelayedExpansion
"%~dp0run-python.cmd" "%~dp0publish.py" validate %*
