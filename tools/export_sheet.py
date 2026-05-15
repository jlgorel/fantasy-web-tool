import pandas as pd
import os
import sys

def excel_sheet_to_csv(excel_path, sheet_name, csv_path):
    """
    Reads a specific sheet from an Excel file and exports it as a CSV file.
    
    :param excel_path: Path to the Excel file (.xls or .xlsx)
    :param sheet_name: Name of the sheet to export
    :param csv_path: Path to save the CSV file
    """
    try:
        # Validate file existence
        if not os.path.isfile(excel_path):
            raise FileNotFoundError(f"Excel file not found: {excel_path}")

        # Read the specified sheet
        df = pd.read_excel(excel_path, sheet_name=sheet_name)

        # Export to CSV without index
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')

        print(f"✅ Sheet '{sheet_name}' exported successfully to '{csv_path}'")

    except ValueError as ve:
        print(f"❌ Error: {ve} (Check if the sheet name exists in the file.)")
    except FileNotFoundError as fe:
        print(f"❌ {fe}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    # Example usage:
    # python script.py input.xlsx "Sheet1" output.csv
    if len(sys.argv) != 4:
        print("Usage: python script.py <excel_path> <sheet_name> <csv_path>")
        sys.exit(1)

    excel_file = sys.argv[1]
    sheet = sys.argv[2]
    csv_file = sys.argv[3]

    excel_sheet_to_csv(excel_file, sheet, csv_file)
