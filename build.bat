@echo off
chcp 65001 >nul
setlocal

pushd "%~dp0"

python -m pip install -U pyinstaller

rem 
pyinstaller ^
  --noconsole ^
  --onedir ^
  --name manga_manager ^
  --icon "kotori.ico" ^
  --add-data "models;models" ^
  --collect-all manga_ocr ^
  --collect-all transformers ^
  --collect-all fugashi ^
  --collect-all unidic_lite ^
  main.py

popd

endlocal
