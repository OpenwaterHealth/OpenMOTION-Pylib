@echo off
setlocal

set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..

cd /d "%REPO_ROOT%"

:: Create reports dir if it doesn't exist
if not exist reports mkdir reports

:: Detect whether pytest-html is available
pytest --co -q --html=reports/_probe.html --self-contained-html >nul 2>&1
if %ERRORLEVEL%==0 (
    set HTML_FLAGS=--html=reports/report.html --self-contained-html
    set HTML_NOTE=HTML report: reports\report.html
) else (
    set HTML_FLAGS=
    set HTML_NOTE=(pytest-html not installed — plain output only)
)

echo Running OpenMotion SDK hardware tests...
echo %HTML_NOTE%
echo.

if "%1"=="full" (
    echo Mode: FULL ^(including slow and destructive tests^)
    echo Warning: devices will enter DFU and require power-cycle after this run.
    echo.
    pytest tests/ --junitxml=reports/junit.xml %HTML_FLAGS%
) else if "%1"=="slow" (
    echo Mode: non-destructive including slow tests
    echo.
    pytest tests/ -m "not destructive" --junitxml=reports/junit.xml %HTML_FLAGS%
) else (
    echo Mode: fast non-destructive ^(default^)
    echo To run slow tests:       run_tests.bat slow
    echo To run full suite:       run_tests.bat full
    echo To run offline dry-run:  set OPENMOTION_DEMO=1 and re-run
    echo.
    pytest tests/ -m "not slow and not destructive" --junitxml=reports/junit.xml %HTML_FLAGS%
)

echo.
if %ERRORLEVEL%==0 (
    echo All tests passed.
) else (
    echo One or more tests failed.
    if not "%HTML_FLAGS%"=="" echo See reports\report.html for details.
)

exit /b %ERRORLEVEL%
