@echo off
setlocal DisableDelayedExpansion
"%~dp0run-python.cmd" "%~dp0set_pack_password.py" %*
