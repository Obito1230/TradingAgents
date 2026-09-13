@echo off
setlocal EnableExtensions

rem =====================================================================
rem  DeepSeek Harness plugin fix  (APPLY)
rem
rem  Root cause: dsh-context@0.49.1 declares peer deps newer than this
rem  host (dsh 0.1.2-alpha.1 -> cordis 4.0.1 / schemastery 3.18.1 /
rem  dsh-session & dsh-settings 0.1.2-alpha.1), producing 4 host-compat
rem  risks. This reverts dsh-context to 0.38.5 (last known-good version,
rem  proven compatible by the market log).
rem
rem  Scope: ONLY the dsh-context version in package.json + pnpm-lock.yaml
rem  + node_modules. cordis.patch.yml, bundle order and every other plugin
rem  are left untouched.
rem
rem  Idempotent: creates a snapshot ONLY if none exists; aborts if one
rem  already exists (run rollback.bat first to reset).
rem
rem  EDIT THE TWO PATHS BELOW IF YOURS DIFFER.
rem =====================================================================

set "PROFILE=%USERPROFILE%\.dsh\profiles\desktop"
set "PNPM=%APPDATA%\DSH Desktop\runtime-commands\bin\pnpm.cmd"
set "SNAPSHOT=%PROFILE%\.dsh-fix-snapshot"
set "FROM=0.49.1"
set "TO=0.38.5"

echo === dsh-context compatibility fix: apply (%FROM% -^> %TO%) ===

rem --- sanity: profile exists ---
if not exist "%PROFILE%\package.json" (
    echo [ABORT] profile not found: %PROFILE%
    echo         edit the PROFILE variable and retry.
    exit /b 1
)

rem --- sanity: packaged pnpm exists ---
if not exist "%PNPM%" (
    echo [ABORT] packaged pnpm not found: %PNPM%
    echo         edit the PNPM variable and retry.
    exit /b 1
)

rem --- idempotency guard: refuse if a snapshot already exists ---
if exist "%SNAPSHOT%" (
    echo [ABORT] snapshot already exists: %SNAPSHOT%
    echo         apply already ran ^(or was interrupted^). Run rollback.bat first,
    echo         or confirm and delete that directory manually before re-running.
    exit /b 2
)

rem --- pre-flight: current version must be the expected pre-fix version ---
findstr /c:"dsh-context" "%PROFILE%\package.json" | findstr /c:"%FROM%" >nul
if errorlevel 1 (
    echo [ABORT] package.json does not show dsh-context %FROM%.
    echo         The profile is not in the expected pre-fix state. No changes made.
    exit /b 3
)

echo.
echo [1/3] snapshotting pre-apply state to: %SNAPSHOT%
mkdir "%SNAPSHOT%" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] could not create snapshot directory.
    exit /b 1
)
copy /y "%PROFILE%\package.json"   "%SNAPSHOT%\package.json"   >nul
copy /y "%PROFILE%\pnpm-lock.yaml" "%SNAPSHOT%\pnpm-lock.yaml" >nul
if exist "%PROFILE%\pnpm-workspace.yaml" copy /y "%PROFILE%\pnpm-workspace.yaml" "%SNAPSHOT%\pnpm-workspace.yaml" >nul
if not exist "%SNAPSHOT%\package.json" goto :snapfail
if not exist "%SNAPSHOT%\pnpm-lock.yaml" goto :snapfail
> "%SNAPSHOT%\README.txt" echo apply snapshot for dsh-context %FROM% -^> %TO%

echo [2/3] running: pnpm add -w dsh-context@%TO% --save-exact
pushd "%PROFILE%"
call "%PNPM%" add -w dsh-context@%TO% --save-exact
set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo.
    echo [FAIL] pnpm add exited with code %RC%.
    echo         Pre-apply files were snapshotted at: %SNAPSHOT%
    echo         Run rollback.bat to restore, or fix the error and re-run apply.
    exit /b %RC%
)

echo [3/3] verifying package.json now shows dsh-context %TO% ...
findstr /c:"dsh-context" "%PROFILE%\package.json" | findstr /c:"%TO%" >nul
if errorlevel 1 (
    echo [WARN] package.json does not show dsh-context %TO% after install.
    echo        Inspect %PROFILE%\package.json before restarting.
) else (
    echo        OK.
)

echo.
echo [OK] dsh-context downgraded %FROM% -^> %TO%. package.json, pnpm-lock.yaml
echo      and node_modules are updated. cordis.patch.yml and bundle order untouched.
echo [NEXT] restart DeepSeek Harness for the change to take effect.
exit /b 0

:snapfail
echo [FAIL] failed to copy a file into the snapshot; removing partial snapshot.
rmdir /s /q "%SNAPSHOT%" >nul 2>&1
exit /b 1
