@echo off
:: ──────────────────────────────────────────────────────────────────────
::  install.bat — Install OpenMotion WinUSB drivers on Windows
::
::  Must be run as Administrator.
::  Installs WinUSB driver packages for all three interfaces of the
::  OpenMotion sensor module (VID 0x0483, PID 0x5A5A).
:: ──────────────────────────────────────────────────────────────────────
setlocal enableextensions
set DIR=%~dp0

echo.
echo  OpenMotion WinUSB Driver Installer
echo  ===================================
echo.

:: Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: This script must be run as Administrator.
    echo  Right-click and select "Run as administrator".
    echo.
    pause
    exit /b 1
)

:: Install the signing certificate (if present)
if exist "%DIR%OpenMotion_signing_cert.cer" (
    echo  Installing signing certificate...
    "%SystemRoot%\System32\certutil.exe" -addstore -f TrustedPublisher "%DIR%OpenMotion_signing_cert.cer" >nul 2>&1
    if %errorlevel% neq 0 (
        echo  WARNING: Could not add certificate to TrustedPublisher store.
    )
    "%SystemRoot%\System32\certutil.exe" -addstore -f Root "%DIR%OpenMotion_signing_cert.cer" >nul 2>&1
    if %errorlevel% neq 0 (
        echo  WARNING: Could not add certificate to Root store.
    )
    echo  Certificate installed.
) else (
    echo  No signing certificate found — skipping.
)

echo.
echo  Installing WinUSB drivers for OpenMotion Sensor Module...
echo.

:: Interface 0 — Command
echo  [1/3] Interface 0 (Command)...
"%SystemRoot%\System32\pnputil.exe" /add-driver "%DIR%openmotion-sensor-if0.inf" /install >nul 2>&1
if %errorlevel% neq 0 (
    echo    FAILED — you may need to install manually via Device Manager.
) else (
    echo    OK
)

:: Interface 1 — Histogram Stream
echo  [2/3] Interface 1 (Histogram Stream)...
"%SystemRoot%\System32\pnputil.exe" /add-driver "%DIR%openmotion-sensor-if1.inf" /install >nul 2>&1
if %errorlevel% neq 0 (
    echo    FAILED — you may need to install manually via Device Manager.
) else (
    echo    OK
)

:: Interface 2 — IMU Stream
echo  [3/3] Interface 2 (IMU Stream)...
"%SystemRoot%\System32\pnputil.exe" /add-driver "%DIR%openmotion-sensor-if2.inf" /install >nul 2>&1
if %errorlevel% neq 0 (
    echo    FAILED — you may need to install manually via Device Manager.
) else (
    echo    OK
)

echo.
echo  ===================================
echo  Driver installation complete.
echo  If the sensor is currently plugged in, unplug and replug it.
echo  ===================================
echo.
pause
exit /b 0
