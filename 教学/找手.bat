@echo off
setlocal
title find hand_agent.py
set OUT=%USERPROFILE%\Desktop\found_hand_agent.txt
echo === find hand_agent.py === > %OUT%
for %%D in ("%USERPROFILE%\Desktop" "%USERPROFILE%\Downloads" "%USERPROFILE%\Documents" "D:\" "E:\") do (
  if exist %%D (
    echo --- searching %%D --- >> %OUT%
    dir /s /b "%%~D\hand_agent.py" 2>nul >> %OUT%
  )
)
echo.
echo ---------------- RESULT ----------------
type %OUT%
echo ----------------------------------------
echo.
echo saved to: %OUT%
echo.
echo If a path appears above, open that folder.
echo It must also contain mcu_hid.py
echo.
pause