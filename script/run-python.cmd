@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_DRYRUN="
set "DP_EXIT_CODE=0"
set "DP_PUSHED="

if "%~1"=="" goto usage
pushd "%~dp0.." >nul 2>&1
if errorlevel 1 goto cwd_error
set "DP_PUSHED=1"
if not exist "%~1" goto missing_script

"%SystemRoot%\System32\where.exe" py.exe >nul 2>&1
if errorlevel 1 goto try_python
py.exe -3 -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py.exe -3 %*
set "DP_EXIT_CODE=%ERRORLEVEL%"
goto finish

:try_python
"%SystemRoot%\System32\where.exe" python.exe >nul 2>&1
if errorlevel 1 goto no_python
python.exe -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" >nul 2>&1
if errorlevel 1 goto no_python
python.exe %*
set "DP_EXIT_CODE=%ERRORLEVEL%"
goto finish

:usage
echo 사용법: run-python.cmd "Python 스크립트 경로" [인자...]
set "DP_EXIT_CODE=64"
goto finish

:cwd_error
echo 오류: 저장소 폴더로 이동하지 못했습니다.
set "DP_EXIT_CODE=72"
goto finish

:missing_script
echo 오류: 실행할 Python 스크립트를 찾지 못했습니다.
set "DP_EXIT_CODE=66"
goto finish

:no_python
echo 오류: Python 3을 찾지 못했습니다. Python 3을 설치한 뒤 다시 실행해 주세요.
echo 확인 명령: py -3 --version 또는 python --version
echo 이 도구는 Python이나 pip 패키지를 자동 설치하지 않습니다.
set "DP_EXIT_CODE=9009"

:finish
if defined DP_PUSHED popd
if "%DP_EXIT_CODE%"=="0" goto leave
echo.
echo 작업이 실패했습니다. 종료 코드: %DP_EXIT_CODE%
if "%DOORAY_PLUS_NO_PAUSE%"=="1" goto leave
echo 내용을 확인한 뒤 아무 키나 누르면 창이 닫힙니다.
pause >nul

:leave
exit /b %DP_EXIT_CODE%
