@echo off
REM Build a one-file Windows executable of AutoMCP: dist\automcp.exe
REM Usage (Windows cmd):  build_exe.bat
REM Tip: run it inside a virtual environment to keep PyInstaller out of your main Python.
REM PyInstaller's temporary files go to %TEMP%\automcp_build; only dist\automcp.exe is created here.
REM No .env file or API key is bundled: the exe reads AZURE_OPENAI_API_KEY from the environment at run time.

cd /d "%~dp0"

python -m pip install "PyYAML>=6.0" "openapi-spec-validator>=0.5.0" "python-dotenv>=1.0.0" "InquirerPy>=0.3.4" "openai>=1.0.0" pyinstaller
if errorlevel 1 exit /b 1

python -m PyInstaller --onefile --name automcp --paths scripts ^
  --collect-data openapi_spec_validator --collect-data openapi_schema_validator --collect-data jsonschema_specifications ^
  --clean --noconfirm --distpath dist --workpath "%TEMP%\automcp_build" --specpath "%TEMP%\automcp_build" ^
  scripts\main.py
if errorlevel 1 exit /b 1

echo.
echo Built dist\automcp.exe
