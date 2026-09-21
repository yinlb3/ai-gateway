@echo off
REM ==========================================================================
REM Start the LiteLLM gateway with this project's accounting callback.
REM
REM The script checks for the files the gateway needs, then hands over to
REM litellm. It never edits the price table or the raw logs.
REM ==========================================================================

setlocal

cd /d "%~dp0"

echo === ai-gateway start ===

REM 1. The gateway configuration must exist.
if not exist "config\config.yaml" (
    echo ERROR: config\config.yaml not found.
    echo Copy config\config.example.yaml to config\config.yaml and fill it in.
    exit /b 1
)

REM 2. The price table must exist, because reports need it.
if not exist "config\pricing.yaml" (
    echo ERROR: config\pricing.yaml not found.
    echo Copy and fill in a price table before starting.
    exit /b 1
)

REM 3. The raw log directory is created here so the callback never has to.
if not exist "logs" (
    mkdir "logs"
    echo Created logs\
)

REM 4. The secrets are read from the environment, never from the config.
if "%DEEPSEEK_API_KEY%"=="" (
    echo WARNING: DEEPSEEK_API_KEY is not set. The gateway will reject calls.
)
if "%DATABASE_URL%"=="" (
    echo WARNING: DATABASE_URL is not set. Virtual keys will not work.
)

REM 5. Hand over. The port can be overridden by setting PORT first.
if "%PORT%"=="" set PORT=4000
echo Starting LiteLLM on port %PORT%
echo.

litellm --config "config\config.yaml" --port %PORT%

endlocal
