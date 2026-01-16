@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   Discord Pals - Interactive Setup
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH!
    echo.
    echo Download Python from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM Check Python version
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYTHON_VER!") do (
    set PYTHON_MAJOR=%%a
    set PYTHON_MINOR=%%b
)

echo [OK] Python !PYTHON_VER! found

if !PYTHON_MAJOR! LSS 3 (
    echo ERROR: Python 3.10 or higher is required!
    echo You have Python !PYTHON_VER!
    pause
    exit /b 1
)
if !PYTHON_MAJOR! EQU 3 if !PYTHON_MINOR! LSS 10 (
    echo ERROR: Python 3.10 or higher is required!
    echo You have Python !PYTHON_VER!
    pause
    exit /b 1
)

if !PYTHON_MAJOR! EQU 3 if !PYTHON_MINOR! GEQ 13 (
    echo [INFO] Python 3.13+ detected - audioop-lts will be installed for compatibility
)

REM Check if main.py exists
if not exist "main.py" (
    echo ERROR: main.py not found!
    echo Please run this script from the discord-pals folder.
    pause
    exit /b 1
)
echo [OK] Project files found

echo.
echo ========================================
echo   Step 1: Virtual Environment
echo ========================================
echo.

if exist "venv" (
    echo [OK] Virtual environment already exists
) else (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)

echo Activating virtual environment...
call venv\Scripts\activate

echo Installing dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo WARNING: Some dependencies failed to install.
    echo Trying again without orjson ^(optional performance package^)...
    findstr /v "^orjson" requirements.txt > requirements_minimal.txt
    pip install -r requirements_minimal.txt -q
    del requirements_minimal.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies!
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed ^(without orjson^)
) else (
    echo [OK] Dependencies installed
)

echo.
echo ========================================
echo   Step 2: Configure AI Providers
echo ========================================
echo.

REM Store provider key env vars
set "PROVIDER_KEYS="

if exist "providers.json" (
    echo [OK] providers.json already exists
    set /p RECONFIGURE="Do you want to reconfigure providers? (y/N): "
    if /i not "!RECONFIGURE!"=="y" goto :skip_providers
)

echo.
echo How many AI providers do you want to configure?
echo (Each provider needs an OpenAI-compatible API endpoint)
echo.
set /p PROVIDER_COUNT="Number of providers (1-5, default=1): "
if "!PROVIDER_COUNT!"=="" set PROVIDER_COUNT=1

echo { > providers.json
echo   "providers": [ >> providers.json

set /a LAST_PROVIDER=!PROVIDER_COUNT!-1
for /l %%i in (0,1,!LAST_PROVIDER!) do (
    set /a NUM=%%i+1
    echo.
    echo --- Provider !NUM! ---
    set /p "P_NAME=Provider name (e.g., OpenAI, DeepSeek): "
    set /p "P_URL=API URL (e.g., https://api.openai.com/v1): "
    set /p "P_KEY_ENV=Env var name for API key (e.g., OPENAI_API_KEY): "
    set /p "P_MODEL=Model name (e.g., gpt-4o, deepseek-chat): "
    
    REM Store key env var for later
    set "PROVIDER_KEYS=!PROVIDER_KEYS! !P_KEY_ENV!"
    
    if %%i==!LAST_PROVIDER! (
        echo     {"name": "!P_NAME!", "url": "!P_URL!", "key_env": "!P_KEY_ENV!", "model": "!P_MODEL!"} >> providers.json
    ) else (
        echo     {"name": "!P_NAME!", "url": "!P_URL!", "key_env": "!P_KEY_ENV!", "model": "!P_MODEL!"}, >> providers.json
    )
)

echo   ], >> providers.json
echo   "timeout": 60 >> providers.json
echo } >> providers.json

echo.
echo [OK] providers.json created

:skip_providers

echo.
echo ========================================
echo   Step 3: Configure Discord Bots
echo ========================================
echo.

REM Store bot token env vars
set "BOT_TOKENS="

echo How many Discord bots do you want to run?
echo (Each bot needs its own Discord application token)
echo.
set /p BOT_COUNT="Number of bots (1-10, default=1): "
if "!BOT_COUNT!"=="" set BOT_COUNT=1

if !BOT_COUNT! GTR 1 (
    echo.
    echo Configuring multi-bot mode...
    
    echo { > bots.json
    echo   "bots": [ >> bots.json
    
    set /a LAST_BOT=!BOT_COUNT!-1
    for /l %%i in (0,1,!LAST_BOT!) do (
        set /a NUM=%%i+1
        echo.
        echo --- Bot !NUM! ---
        set /p "B_NAME=Bot display name (e.g., Firefly): "
        set /p "B_TOKEN_ENV=Env var for Discord token (e.g., FIREFLY_DISCORD_TOKEN): "
        set /p "B_CHARACTER=Character file name without .md (e.g., firefly): "
        
        REM Store token env var for later
        set "BOT_TOKENS=!BOT_TOKENS! !B_TOKEN_ENV!"
        
        if %%i==!LAST_BOT! (
            echo     {"name": "!B_NAME!", "token_env": "!B_TOKEN_ENV!", "character": "!B_CHARACTER!"} >> bots.json
        ) else (
            echo     {"name": "!B_NAME!", "token_env": "!B_TOKEN_ENV!", "character": "!B_CHARACTER!"}, >> bots.json
        )
    )
    
    echo   ] >> bots.json
    echo } >> bots.json
    
    echo.
    echo [OK] bots.json created for !BOT_COUNT! bots
) else (
    echo Single-bot mode selected.
    echo Using DISCORD_TOKEN and DEFAULT_CHARACTER from .env
)

echo.
echo ========================================
echo   Step 4: Environment Variables
echo ========================================
echo.

if exist ".env" (
    echo [OK] .env file already exists
    set /p EDIT_ENV="Do you want to edit .env now? (y/N): "
    if /i "!EDIT_ENV!"=="y" notepad .env
) else (
    echo Creating .env file...
    
    (
        echo # ============================================
        echo # SINGLE-BOT MODE ^(without bots.json^)
        echo # ============================================
        echo DISCORD_TOKEN=your_discord_token_here
        echo DEFAULT_CHARACTER=firefly
        echo.
    ) > .env
    
    REM Add bot tokens if multi-bot - use ACTUAL env var names entered
    if !BOT_COUNT! GTR 1 (
        echo # ============================================ >> .env
        echo # MULTI-BOT MODE ^(with bots.json^) >> .env
        echo # ============================================ >> .env
        for %%t in (!BOT_TOKENS!) do (
            echo %%t=your_token_here >> .env
        )
        echo. >> .env
    )
    
    echo # ============================================ >> .env
    echo # AI PROVIDER API KEYS >> .env
    echo # ============================================ >> .env
    
    REM Add provider keys - use ACTUAL env var names entered
    if defined PROVIDER_KEYS (
        for %%k in (!PROVIDER_KEYS!) do (
            echo %%k=your_key_here >> .env
        )
    ) else (
        echo OPENAI_API_KEY=your_key_here >> .env
        echo DEEPSEEK_API_KEY=your_key_here >> .env
    )
    
    echo.
    echo [OK] .env file created
    echo.
    echo Opening .env for you to add your API keys...
    notepad .env
)

echo.
echo ========================================
echo   Setup Complete!
echo ========================================
echo.
echo Next steps:
echo   1. Make sure .env has your DISCORD_TOKEN and API keys
echo   2. Create character files in characters/ folder
echo   3. Run: run.bat
echo.
echo Press any key to exit...
pause >nul
