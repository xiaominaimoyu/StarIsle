@echo off
setlocal
set "RUNNER_DIR=%~dp0"
set "VENV_PYTHON=%RUNNER_DIR%..\apps\api\.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" -B "%RUNNER_DIR%recovery_drill.py" %*
) else (
  py -3 -B "%RUNNER_DIR%recovery_drill.py" %*
)
set "RUNNER_EXIT=%ERRORLEVEL%"
endlocal & exit /b %RUNNER_EXIT%
