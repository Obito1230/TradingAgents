@echo off
setlocal EnableExtensions

rem =====================================================================
rem  DeepSeek Harness plugin fix  (ROLLBACK)
rem
rem  Restores dsh-context 0.49.1 (the pre-apply state) from the snapshot
rem  that apply.bat created, then reconciles node_modules with
rem  `pnpm install`. Undoes apply.bat completely.
rem
rem  Idempotent: aborts safely when no snapshot exists; safe to re-run.
rem
rem  EDIT THE TWO PATHS BELOW IF YOURS DIFFER.
rem =====================================================================

set "PROFILE=%USERPROFILE%\.dsh\profiles\desktop"
set "PNPM=%APPDATA%\DSH Desktop\runtime-commands\bin\pnpm.cmd"
set "SNAPSHOT=%PROFILE%\.dsh-fix-snapshot"

echo === dsh-context compatibility fix: rollback ===

rem --- idempotency guard: nothing to do without a snapshot ---
if not exist "%SNAPSHOT%" (
    echo [ABORT] no snapshot found at: %SNAPSHOT%
    echo         Nothing to roll back ^(apply never ran, or already rolled back^).
    exit /b 2
)

if not exist "%PNPM%" (
    echo [ABORT] packaged pnpm not found: %PNPM%
    echo         edit the PNPM variable and retry.
    exit /b 1
)

echo.
echo [1/3] restoring pre-apply files from snapshot ...
copy /y "%SNAPSHOT%\package.json"   "%PROFILE%\package.json"   >nul
copy /y "%SNAPSHOT%\pnpm-lock.yaml" "%PROFILE%\pnpm-lock.yaml" >nul
if exist "%SNAPSHOT%\pnpm-workspace.yaml" copy /y "%SNAPSHOT%\pnpm-workspace.yaml" "%PROFILE%\pnpm-workspace.yaml" >nul
if not exist "%PROFILE%\package.json" goto :restorefail
if not exist "%PROFILE%\pnpm-lock.yaml" goto :restorefail

echo [2/3] running: pnpm install
pushd "%PROFILE%"
call "%PNPM%" install
set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo.
    echo [FAIL] pnpm install exited with code %RC%.
    echo         Snapshot still kept at: %SNAPSHOT%
    echo         Fix the error and re-run rollback.bat.
    exit /b %RC%
)

echo [3/3] removing snapshot %SNAPSHOT% ...
rmdir /s /q "%SNAPSHOT%" >nul 2>&1

echo.
echo [OK] restored to dsh-context 0.49.1 (pre-apply state).
echo [NEXT] restart DeepSeek Harness for the change to take effect.
exit /b 0

:restorefail
echo [FAIL] failed to restore files from snapshot; snapshot kept at %SNAPSHOT%.
exit /b 1
