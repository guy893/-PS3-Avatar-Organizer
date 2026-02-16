@echo off
echo Building PS3 Avatar Organizer...
echo.

pip install pyinstaller pillow pycryptodome 2>nul

python -m PyInstaller --onefile --windowed ^
    --add-data "serialstation_titles.csv;." ^
    --add-data "titles.json;." ^
    --add-data "avatar_organizer.py;." ^
    --add-data "app_icon.png;." ^
    --add-data "app_icon.ico;." ^
    --icon "app_icon.ico" ^
    --hidden-import=csv ^
    --name "PS3 Avatar Organizer" ^
    gui_app.py

echo.
if exist "dist\PS3 Avatar Organizer.exe" (
    echo Build successful!
    echo Output: dist\PS3 Avatar Organizer.exe
    echo.
    echo To distribute: ship "PS3 Avatar Organizer.exe" as a single file.
    echo The CSV title database and all dependencies are bundled inside.
) else (
    echo Build failed. Check the output above for errors.
)
echo.
pause
