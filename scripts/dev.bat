@echo off
setlocal

set "ROOT_DIR=%~dp0.."
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"

pushd "%ROOT_DIR%\apps\web" || exit /b 1
call npm run build
if errorlevel 1 exit /b %errorlevel%

popd
pushd "%ROOT_DIR%\apps\api" || exit /b 1
"%ROOT_DIR%\metaclass_env\Scripts\python.exe" -m uvicorn metaclass.main:app --host 127.0.0.1 --port %PORT%
set "EXIT_CODE=%errorlevel%"
popd

exit /b %EXIT_CODE%
