import pandas as pd
import sys
from collections import Counter

def analyze_csv(file_path):
    # Read the CSV file
    df = pd.read_csv(file_path)
    # print(df)
    # Ensure there is at least one column
    if df.shape[1] == 0:
        print("Error: CSV file is empty or has no columns.")
        return
    
    # Get the first column as a list
    values = df.iloc[:, 0].tolist()
    
    # Count occurrences of each unique value
    counts = Counter(values)
    
    # Calculate the average count per unique value
    avg_occurrence = sum(counts.values()) / len(counts)
    
    # Identify inconsistencies
    inconsistent_values = {key: value for key, value in counts.items() if value != round(avg_occurrence)}
    
    # Print results
    # print("Occurrences per unique value:")
    # for key, value in counts.items():
    #     print(f"{key}: {value}")
    
    print("\nExpected average occurrence count:", round(avg_occurrence))
    
    if inconsistent_values:
        print("\nInconsistencies found:")
        for key, value in inconsistent_values.items():
            print(f"{key}: {value} (expected {round(avg_occurrence)})")
    else:
        print("\nNo inconsistencies found.")


    column_name = 'total'
    expected_value = 1920*1280 + 6
    non_matching_rows = df[df[column_name] != expected_value]
        
    if not non_matching_rows.empty:
        print("Rows with unexpected values:")
        print(non_matching_rows)
    else:
        print(f"All rows in column '{column_name}' match the expected value {expected_value}.")


if __name__ == "__main__":
    analyze_csv("histo_data.csv")
