@echo off
setlocal
cd /d "%~dp0"
set "EXHIBIT_DIR=%~dp0artifacts\exhibition"
set "EXHIBIT_SERVER=%~dp0scripts\serve_exhibit.py"
if exist "%~dp0index.html" (
    set "EXHIBIT_DIR=%~dp0."
    set "EXHIBIT_SERVER=%~dp0serve_exhibit.py"
)
if not exist "%EXHIBIT_DIR%\index.html" goto missing_bundle
if not exist "%EXHIBIT_SERVER%" goto missing_bundle

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    py -3.12 "%EXHIBIT_SERVER%" --directory "%EXHIBIT_DIR%" %*
    goto finished
)
py -3 -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>nul
if not errorlevel 1 (
    py -3 "%EXHIBIT_SERVER%" --directory "%EXHIBIT_DIR%" %*
    goto finished
)

:try_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>nul
if errorlevel 1 goto missing_python
python "%EXHIBIT_SERVER%" --directory "%EXHIBIT_DIR%" %*
goto finished

:missing_bundle
echo Exhibition files were not found beside this launcher or in artifacts\exhibition.
echo Extract the complete exhibition ZIP before starting this file.
echo You can also open tablet.html directly, or use offline-guide.pdf if included.
pause
exit /b 1

:missing_python
echo Python 3.9 or newer is required for the local web server; Python 3.12 is recommended.
echo This launcher does not install Python or download packages.
echo Prepare Python before the exhibition, or open tablet.html / offline-guide.pdf.
pause
exit /b 1

:finished
set "EXHIBIT_EXIT=%ERRORLEVEL%"
if not "%EXHIBIT_EXIT%"=="0" (
    echo The exhibition server stopped with an error. Read the message above.
    pause
)
exit /b %EXHIBIT_EXIT%
