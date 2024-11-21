#!/bin/bash

# gradebook-utils.sh
# Utility script for batch operations with the gradebook CLI

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Help text
show_help() {
    cat << EOF
Gradebook Utility Script

Usage: ./gradebook-utils.sh [command] [options]

Commands:
    batch-add-assignments <csv_file>     Add multiple assignments from a CSV file
                                        Format: course_code,category,title,max_points,earned_points

    batch-export [--format=<fmt>]        Export all courses to CSV/TXT files
                                        Default format: txt

    analyze-progress <course_code>       Generate progress report for a course

    backup                               Create timestamped backup of gradebook database

    validate                             Run validation checks on database

Options:
    --help                              Show this help message
    --verbose                           Show detailed output
    --db-path=<path>                    Specify alternative database path

Example:
    ./gradebook-utils.sh batch-add-assignments assignments.csv
    ./gradebook-utils.sh batch-export --format=csv
    ./gradebook-utils.sh analyze-progress CHM343
EOF
}

# Function to validate CSV format
validate_csv() {
    local file="$1"
    local line_num=1
    local errors=0

    while IFS=, read -r course category title max earned; do
        # Skip header line
        if [ $line_num -eq 1 ]; then
            line_num=$((line_num + 1))
            continue
        fi

        # Validate each field
        if [ -z "$course" ] || [ -z "$category" ] || [ -z "$title" ] || \
           [ -z "$max" ] || [ -z "$earned" ]; then
            echo -e "${RED}Error on line $line_num: Missing required fields${NC}"
            errors=$((errors + 1))
        fi

        # Validate numeric values
        if ! [[ "$max" =~ ^[0-9]+(\.[0-9]+)?$ ]] || \
           ! [[ "$earned" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
            echo -e "${RED}Error on line $line_num: Invalid numeric values${NC}"
            errors=$((errors + 1))
        fi

        line_num=$((line_num + 1))
    done < "$file"

    return $errors
}

# Function to batch add assignments
batch_add_assignments() {
    local csv_file="$1"
    local success=0
    local failed=0

    # Validate file exists
    if [ ! -f "$csv_file" ]; then
        echo -e "${RED}Error: File not found: $csv_file${NC}"
        exit 1
    }

    # Validate CSV format
    echo "Validating CSV format..."
    if ! validate_csv "$csv_file"; then
        echo -e "${RED}CSV validation failed. Please fix errors and try again.${NC}"
        exit 1
    }

    # Skip header line and process each row
    tail -n +2 "$csv_file" | while IFS=, read -r course category title max earned; do
        echo -e "${YELLOW}Adding assignment: $title to $course${NC}"

        if gradebook add assignment "$course" "$category" "$title" "$max" "$earned"; then
            echo -e "${GREEN}Successfully added: $title${NC}"
            success=$((success + 1))
        else
            echo -e "${RED}Failed to add: $title${NC}"
            failed=$((failed + 1))
        fi
    done

    echo -e "\nSummary:"
    echo -e "${GREEN}Successfully added: $success${NC}"
    echo -e "${RED}Failed: $failed${NC}"
}

# Function to create database backup
create_backup() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local db_path="${DB_PATH:-$HOME/.gradebook/gradebook.db}"
    local backup_dir="$HOME/.gradebook/backups"
    local backup_file="$backup_dir/gradebook_$timestamp.db"

    mkdir -p "$backup_dir"

    if [ -f "$db_path" ]; then
        cp "$db_path" "$backup_file"
        echo -e "${GREEN}Backup created: $backup_file${NC}"
    else
        echo -e "${RED}Error: Database file not found: $db_path${NC}"
        exit 1
    fi
}

# Function to analyze course progress
analyze_progress() {
    local course_code="$1"
    local output_file="progress_${course_code}_$(date +%Y%m%d).txt"

    echo "Analyzing progress for $course_code..."

    # Get course summary
    gradebook view details "$course_code" > "$output_file"

    # Get grade distribution
    echo -e "\nGrade Distribution:" >> "$output_file"
    gradebook view distribution "$course_code" >> "$output_file"

    # Get grade trends
    echo -e "\nGrade Trends:" >> "$output_file"
    gradebook view trends "$course_code" >> "$output_file"

    echo -e "${GREEN}Analysis complete! Results saved to: $output_file${NC}"
}

# Function to validate database
validate_database() {
    echo "Running database validation..."

    # Check database structure
    echo "Checking database structure..."
    if ! sqlite3 "$DB_PATH" ".tables" &>/dev/null; then
        echo -e "${RED}Error: Database structure validation failed${NC}"
        return 1
    fi

    # Run the validation script
    if python -m gradebook.validate_migration; then
        echo -e "${GREEN}Database validation passed${NC}"
    else
        echo -e "${RED}Database validation failed${NC}"
        return 1
    fi
}

# Main script logic
main() {
    # Process global options
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --help)
                show_help
                exit 0
                ;;
            --verbose)
                VERBOSE=1
                shift
                ;;
            --db-path=*)
                DB_PATH="${1#*=}"
                shift
                ;;
            *)
                break
                ;;
        esac
    done

    # Process commands
    case "$1" in
        batch-add-assignments)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: CSV file required${NC}"
                exit 1
            fi
            batch_add_assignments "$2"
            ;;
        batch-export)
            format="${2:-txt}"
            gradebook export all --format="$format"
            ;;
        analyze-progress)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Course code required${NC}"
                exit 1
            fi
            analyze_progress "$2"
            ;;
        backup)
            create_backup
            ;;
        validate)
            validate_database
            ;;
        *)
            echo -e "${RED}Unknown command: $1${NC}"
            show_help
            exit 1
            ;;
    esac
}

# Execute main function
main "$@"