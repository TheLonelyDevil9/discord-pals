#!/bin/bash

echo "========================================"
echo "  Discord Pals - Interactive Setup"
echo "========================================"
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 is not installed!"
    echo
    echo "Install with:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv"
    echo "  macOS: brew install python3"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

echo "[OK] Python $PYTHON_VERSION found"

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo "ERROR: Python 3.10 or higher is required!"
    echo "You have Python $PYTHON_VERSION"
    exit 1
fi

if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 13 ]; then
    echo "[INFO] Python 3.13+ detected - audioop-lts will be installed for compatibility"
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    echo "ERROR: main.py not found!"
    echo "Please run this script from the discord-pals folder."
    exit 1
fi
echo "[OK] Project files found"

echo
echo "========================================"
echo "  Step 1: Virtual Environment"
echo "========================================"
echo

if [ -d "venv" ]; then
    echo "[OK] Virtual environment already exists"
else
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create virtual environment!"
        exit 1
    fi
    echo "[OK] Virtual environment created"
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt -q
if [ $? -ne 0 ]; then
    echo "WARNING: Some dependencies failed to install."
    echo "Trying again without orjson (optional performance package)..."
    grep -v "^orjson" requirements.txt > requirements_minimal.txt
    pip install -r requirements_minimal.txt -q
    rm requirements_minimal.txt
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to install dependencies!"
        exit 1
    fi
    echo "[OK] Dependencies installed (without orjson)"
else
    echo "[OK] Dependencies installed"
fi

echo
echo "========================================"
echo "  Step 2: Configure AI Providers"
echo "========================================"
echo

# Arrays to store env var names
PROVIDER_KEYS=()
BOT_TOKENS=()

if [ -f "providers.json" ]; then
    echo "[OK] providers.json already exists"
    read -p "Do you want to reconfigure providers? (y/N): " RECONFIGURE
    if [ "$RECONFIGURE" != "y" ] && [ "$RECONFIGURE" != "Y" ]; then
        SKIP_PROVIDERS=1
    fi
fi

if [ -z "$SKIP_PROVIDERS" ]; then
    echo
    echo "How many AI providers do you want to configure?"
    echo "(Each provider needs an OpenAI-compatible API endpoint)"
    echo
    read -p "Number of providers (1-5, default=1): " PROVIDER_COUNT
    PROVIDER_COUNT=${PROVIDER_COUNT:-1}
    
    echo "{" > providers.json
    echo '  "providers": [' >> providers.json
    
    for ((i=0; i<PROVIDER_COUNT; i++)); do
        NUM=$((i+1))
        echo
        echo "--- Provider $NUM ---"
        read -p "Provider name (e.g., OpenAI, DeepSeek): " P_NAME
        read -p "API URL (e.g., https://api.openai.com/v1): " P_URL
        read -p "Env var name for API key (e.g., OPENAI_API_KEY): " P_KEY_ENV
        read -p "Model name (e.g., gpt-4o, deepseek-chat): " P_MODEL
        
        # Store key env var for later
        PROVIDER_KEYS+=("$P_KEY_ENV")
        
        if [ $i -eq $((PROVIDER_COUNT-1)) ]; then
            echo "    {\"name\": \"$P_NAME\", \"url\": \"$P_URL\", \"key_env\": \"$P_KEY_ENV\", \"model\": \"$P_MODEL\"}" >> providers.json
        else
            echo "    {\"name\": \"$P_NAME\", \"url\": \"$P_URL\", \"key_env\": \"$P_KEY_ENV\", \"model\": \"$P_MODEL\"}," >> providers.json
        fi
    done
    
    echo '  ],' >> providers.json
    echo '  "timeout": 60' >> providers.json
    echo "}" >> providers.json
    
    echo
    echo "[OK] providers.json created"
fi

echo
echo "========================================"
echo "  Step 3: Configure Discord Bots"
echo "========================================"
echo

echo "How many Discord bots do you want to run?"
echo "(Each bot needs its own Discord application token)"
echo
read -p "Number of bots (1-10, default=1): " BOT_COUNT
BOT_COUNT=${BOT_COUNT:-1}

if [ $BOT_COUNT -gt 1 ]; then
    echo
    echo "Configuring multi-bot mode..."
    
    echo "{" > bots.json
    echo '  "bots": [' >> bots.json
    
    for ((i=0; i<BOT_COUNT; i++)); do
        NUM=$((i+1))
        echo
        echo "--- Bot $NUM ---"
        read -p "Bot display name (e.g., Firefly): " B_NAME
        read -p "Env var for Discord token (e.g., FIREFLY_DISCORD_TOKEN): " B_TOKEN_ENV
        read -p "Character file name without .md (e.g., firefly): " B_CHARACTER
        
        # Store token env var for later
        BOT_TOKENS+=("$B_TOKEN_ENV")
        
        if [ $i -eq $((BOT_COUNT-1)) ]; then
            echo "    {\"name\": \"$B_NAME\", \"token_env\": \"$B_TOKEN_ENV\", \"character\": \"$B_CHARACTER\"}" >> bots.json
        else
            echo "    {\"name\": \"$B_NAME\", \"token_env\": \"$B_TOKEN_ENV\", \"character\": \"$B_CHARACTER\"}," >> bots.json
        fi
    done
    
    echo '  ]' >> bots.json
    echo "}" >> bots.json
    
    echo
    echo "[OK] bots.json created for $BOT_COUNT bots"
else
    echo "Single-bot mode selected."
    echo "Using DISCORD_TOKEN and DEFAULT_CHARACTER from .env"
fi

echo
echo "========================================"
echo "  Step 4: Environment Variables"
echo "========================================"
echo

if [ -f ".env" ]; then
    echo "[OK] .env file already exists"
    read -p "Do you want to edit .env now? (y/N): " EDIT_ENV
    if [ "$EDIT_ENV" = "y" ] || [ "$EDIT_ENV" = "Y" ]; then
        ${EDITOR:-nano} .env
    fi
else
    echo "Creating .env file..."
    
    cat > .env << 'EOF'
# ============================================
# SINGLE-BOT MODE (without bots.json)
# ============================================
DISCORD_TOKEN=your_discord_token_here
DEFAULT_CHARACTER=firefly

EOF
    
    # Add bot tokens if multi-bot - use ACTUAL env var names entered
    if [ $BOT_COUNT -gt 1 ]; then
        echo "# ============================================" >> .env
        echo "# MULTI-BOT MODE (with bots.json)" >> .env
        echo "# ============================================" >> .env
        for token in "${BOT_TOKENS[@]}"; do
            echo "$token=your_token_here" >> .env
        done
        echo "" >> .env
    fi
    
    echo "# ============================================" >> .env
    echo "# AI PROVIDER API KEYS" >> .env
    echo "# ============================================" >> .env

    # Add provider keys - use ACTUAL env var names entered
    if [ ${#PROVIDER_KEYS[@]} -gt 0 ]; then
        for key in "${PROVIDER_KEYS[@]}"; do
            echo "$key=your_key_here" >> .env
        done
    else
        echo "OPENAI_API_KEY=your_key_here" >> .env
        echo "DEEPSEEK_API_KEY=your_key_here" >> .env
    fi

    echo "" >> .env
    echo "# ============================================" >> .env
    echo "# LOCAL LLM (Ollama, LM Studio, llama.cpp, etc)" >> .env
    echo "# ============================================" >> .env
    echo "LOCAL_API_KEY=not-needed" >> .env
    echo "# LOCAL_API_URL=http://localhost:11434/v1" >> .env

    echo
    echo "[OK] .env file created"
    echo
    echo "Opening .env for you to add your API keys..."
    ${EDITOR:-nano} .env
fi

echo
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo
echo "Next steps:"
echo "  1. Make sure .env has your DISCORD_TOKEN and API keys"
echo "  2. Create character files in characters/ folder"
echo "  3. Run: ./run.sh"
echo
