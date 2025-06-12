@echo off
rem -----------------------------------------------------------------
rem  rename-and-move.bat
rem  Prompts user, renames a file, then moves it elsewhere.
rem -----------------------------------------------------------------
setlocal enabledelayedexpansion

:: Ask for the file to rename
set "src=histogram.csv"
if not exist "%src%" (
    echo File "%src%" not found.
    pause
    goto :eof
)

:: Ask for the new file name (keep extension if you need it)
set /p "newname=Enter NEW name (histograms_****.csv): "
if "%newname%"=="" (
    echo No new name supplied.
    pause
    goto :eof
)

:: Rename the file
echo Renaming...
ren "%src%" "histograms_%newname%.csv"
if errorlevel 1 (
    echo Rename failed.
    pause
    goto :eof
)


:: Get today's date in yyyyMMdd format
for /f "tokens=2 delims==" %%I in ('"wmic os get localdatetime /value"') do set datetime=%%I
set today=%datetime:~0,8%

set target_dir=data\%today%

:: Create target directory if it doesn't exist
if not exist "%target_dir%" (
    mkdir "%target_dir%"
)


:: Move the renamed file
echo Moving to "%target_dir%"...
move /Y "histograms_%newname%.csv" "%target_dir%"
if errorlevel 1 (
    echo Move failed.
    pause
    goto :eof
)

echo Operation completed successfully.
pause
endlocal
