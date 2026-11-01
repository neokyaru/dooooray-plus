@echo off
setlocal DisableDelayedExpansion
"%~dp0run-python.cmd" "%~dp0resize_pack_images.py" %*
