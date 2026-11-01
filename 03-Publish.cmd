@echo off
setlocal DisableDelayedExpansion
"%~dp0script\run-python.cmd" "%~dp0script\publish.py" publish %*
