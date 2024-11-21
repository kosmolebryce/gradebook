#!/bin/bash

# install-man.sh
# Script to install gradebook man page

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default paths
MAN_SOURCE="gradebook.1"
SYSTEM_MAN_PATH="/usr/local/share/man/man1"
USER_MAN_PATH="$HOME/.local/share/man/man1"

show_help() {
    cat << EOF
Install script for gradebook man page

Usage: ./install-man.sh [options]

Options:
    --help          Show this help message
    --system        Install system-wide (requires sudo)
    --user          Install for current user only (default)
    --uninstall     Remove installed man page
EOF
}

# Function to compress man page
compress_man() {
    local source="$1"
    local target="$2"

    # Create directory if it doesn't exist
    mkdir -p "$(dirname "$target")"

    # Compress the man page
    gzip -c "$source" > "$target"
    echo -e "${GREEN}Compressed man page created at $target${NC}"
}

# Function to install man page
install_man() {
    local install_path="$1"
    local target="$install_path/gradebook.1.gz"

    echo "Installing man page..."

    # Check if man page source exists
    if [ ! -f "$MAN_SOURCE" ]; then
        echo -e "${RED}Error: Man page source not found: $MAN_SOURCE${NC}"
        exit 1
    fi

    # Create man directory if it doesn't exist
    mkdir -p "$install_path"

    # Compress and install
    compress_man "$MAN_SOURCE" "$target"

    # Update man database if mandb exists
    if command -v mandb >/dev/null 2>&1; then
        echo "Updating man database..."
        if [ "$install_path" = "$SYSTEM_MAN_PATH" ]; then
            sudo mandb >/dev/null 2>&1
        else
            mandb -q "$HOME/.local/share/man" >/dev/null 2>&1
        fi
    fi

    echo -e "${GREEN}Man page installed successfully!${NC}"
    echo "Try 'man gradebook' to view the documentation"
}

# Function to uninstall man page
uninstall_man() {
    local system_page="$SYSTEM_MAN_PATH/gradebook.1.gz"
    local user_page="$USER_MAN_PATH/gradebook.1.gz"
    local removed=0

    if [ -f "$system_page" ]; then
        echo "Removing system man page..."
        sudo rm "$system_page"
        removed=1
    fi

    if [ -f "$user_page" ]; then
        echo "Removing user man page..."
        rm "$user_page"
        removed=1
    fi

    if [ $removed -eq 1 ]; then
        echo -e "${GREEN}Man page(s) removed successfully${NC}"
    else
        echo -e "${YELLOW}No man pages found to remove${NC}"
    fi
}

# Main script logic
main() {
    local install_type="user"
    local uninstall=0

    # Process arguments
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --help)
                show_help
                exit 0
                ;;
            --system)
                install_type="system"
                shift
                ;;
            --user)
                install_type="user"
                shift
                ;;
            --uninstall)
                uninstall=1
                shift
                ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                show_help
                exit 1
                ;;
        esac
    done

    if [ $uninstall -eq 1 ]; then
        uninstall_man
    else
        if [ "$install_type" = "system" ]; then
            if [ "$(id -u)" -ne 0 ]; then
                echo -e "${YELLOW}System-wide installation requires sudo privileges${NC}"
                exit 1
            fi
            install_man "$SYSTEM_MAN_PATH"
        else
            install_man "$USER_MAN_PATH"
        fi
    fi
}

# Execute main function
main "$@"