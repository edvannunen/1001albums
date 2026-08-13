@echo off
echo Stopping server on port 8000...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a
    set FOUND=1
)
if %FOUND%==0 (
    echo Nothing was listening on port 8000.
) else (
    echo Done.
)
pause
