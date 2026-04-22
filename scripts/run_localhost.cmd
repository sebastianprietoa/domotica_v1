@echo off
set "RUNTIME_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%RUNTIME_PYTHON%" (
  echo No se encontro el runtime Python de Codex en:
  echo %RUNTIME_PYTHON%
  exit /b 1
)

set "PYTHONPATH=%~dp0..\src;%~dp0.."
"%RUNTIME_PYTHON%" "%~dp0run_localhost.py" %*
