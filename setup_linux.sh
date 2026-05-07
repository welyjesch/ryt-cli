#!/bin/bash

# Get the absolute path of the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CLI_PATH="$SCRIPT_DIR/cli.py"

# Define the target bin directory
BIN_DIR="$HOME/.local/bin"
TARGET="$BIN_DIR/ryt-cli"

# Create bin directory if it doesn't exist
mkdir -p "$BIN_DIR"

# Create the wrapper script
cat << EOF > "$TARGET"
#!/bin/bash
cd "$SCRIPT_DIR" && uv run cli.py "\$@"
EOF

# Make the wrapper executable
chmod +x "$TARGET"

echo "ryt-cli has been set up at $TARGET"
echo "Ensure $BIN_DIR is in your PATH."
