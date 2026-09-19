@echo off
set ver=1.3.3
rem build script for the distributable versions of tadpole
if not exist "venv\" (
    python -m venv venv
)
if not exist "venv\Lib\site-packages\PyInstaller" (
    venv\Scripts\python -m pip install pyinstaller
)
if not exist "venv\Lib\site-packages\PIL" (
    venv\Scripts\python -m pip install Pillow
)
if not exist "venv\Lib\site-packages\PyQt5" (
    venv\Scripts\python -m pip install PyQt5
)
pyinstaller wolfpole.py -n wolfpole.exe -F --icon madpole.ico --clean --noconsole --add-data="README.md;."