#!/usr/bin/env python3
"""
Plot test data from camera test rig.

This script plots light and dark histogram data from camera test captures.
Takes two parameters: serial number and revision number.

Usage:
    python plot_test_data.py <serial_number> <revision>

Arguments:
    serial_number: Serial number of the camera
    revision: Revision number (0 = no suffix, 1 = _1, 2 = _2, etc.)

Examples:
    python plot_test_data.py 1 0
    python plot_test_data.py 1 1
    python plot_test_data.py 1 2
"""

import sys
import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path


def load_histogram_data(filepath):
    """
    Load histogram data from CSV file.
    
    Args:
        filepath (str): Path to the CSV file
        
    Returns:
        tuple: (histogram_values, temperature, weighted_mean)
    """
    try:
        df = pd.read_csv(filepath)
        
        # Extract histogram data (columns 2 to 1025, which are bins 0-1023)
        histogram_cols = [str(i) for i in range(1024)]
        histogram_values = df[histogram_cols].iloc[0].values
        
        # Extract temperature
        temperature = df['temperature'].iloc[0]
        
        # Calculate weighted mean using the same method as motion_connector.py
        weighted_mean = calculate_weighted_mean(histogram_values)
        
        return histogram_values, temperature, weighted_mean
        
    except Exception as e:
        print(f"Error loading data from {filepath}: {e}")
        return None, None, None


def calculate_weighted_mean(histogram_data):
    """
    Calculate the weighted mean of histogram data.
    Uses the same calculation as _calculate_weighted_mean in motion_connector.py
    """
    try:
        if histogram_data is None or len(histogram_data) == 0:
            return 0.0
        
        # Calculate weighted mean: sum(bin_value * bin_index) / sum(bin_values)
        weighted_sum = 0.0
        total_count = 0.0
        
        for bin_index, bin_value in enumerate(histogram_data):
            weighted_sum += bin_value * bin_index
            total_count += bin_value
        
        if total_count == 0:
            return 0.0
        
        return weighted_sum / total_count
        
    except Exception as e:
        print(f"Error calculating weighted mean: {e}")
        return 0.0


def plot_histograms(serial_number, revision=0):
    """
    Plot light and dark histograms for the given serial number and revision.
    
    Args:
        serial_number (str): Serial number of the camera
        revision (int): Revision number (0 = no suffix, 1 = _1, 2 = _2, etc.)
    """
    # Define file paths
    script_dir = Path(__file__).parent
    pylib_dir = script_dir.parent
    captures_dir = pylib_dir / "qisda_data"
    
    # Construct filename suffix based on revision
    if revision == 0:
        suffix = ""
    else:
        suffix = f"_{revision}"
    
    light_file = captures_dir / f"{serial_number}_histogram_light{suffix}.csv"
    dark_file = captures_dir / f"{serial_number}_histogram_dark{suffix}.csv"
    
    # Check if files exist
    if not light_file.exists():
        print(f"Error: Light histogram file not found: {light_file}")
        return
        
    if not dark_file.exists():
        print(f"Error: Dark histogram file not found: {dark_file}")
        return
    
    # Load data
    print(f"Loading light histogram from: {light_file}")
    light_hist, light_temp, light_mean = load_histogram_data(light_file)
    
    print(f"Loading dark histogram from: {dark_file}")
    dark_hist, dark_temp, dark_mean = load_histogram_data(dark_file)
    
    if light_hist is None or dark_hist is None:
        print("Error: Failed to load histogram data")
        return
    
    # Create the plot
    plt.figure(figsize=(12, 8))
    
    # Create x-axis (bin numbers)
    bins = np.arange(1024)
    
    # Handle log scale - add small value to avoid log(0)
    light_hist_log = light_hist + 1e-6
    dark_hist_log = dark_hist + 1e-6
    
    # Plot both histograms with shaded areas
    plt.semilogy(bins, light_hist_log, 'b-', linewidth=1.5, label='Light', alpha=0.8)
    plt.fill_between(bins, light_hist_log, alpha=0.3, color='blue')
    
    plt.semilogy(bins, dark_hist_log, 'r-', linewidth=1.5, label='Dark', alpha=0.8)
    plt.fill_between(bins, dark_hist_log, alpha=0.3, color='red')
    
    # Customize the plot
    plt.xlabel('Pixel Value (Bin)', fontsize=12)
    plt.ylabel('Count (Log Scale)', fontsize=12)
    title_serial = f"{serial_number}_{revision}" if revision > 0 else serial_number
    plt.title(f'Camera {title_serial} Test Data\n'
              f'Light Mean: {light_mean:.1f}, Dark Mean: {dark_mean:.1f}\n'
              f'Light Temp: {light_temp:.1f}°C, Dark Temp: {dark_temp:.1f}°C', 
              fontsize=14)
    plt.legend(fontsize=12)
    plt.grid(True, alpha=0.3)
    
    # Set axis limits for better visualization
    plt.xlim(0, 1023)
    
    # Set y-axis limits to handle log scale properly
    plt.ylim(bottom=1.0)
    
    # Add some styling
    plt.tight_layout()
    
    # Save the plot
    output_file = captures_dir / f"{serial_number}_test_plot{suffix}.png"
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"Plot saved to: {output_file}")
    
    # Show the plot
    plt.show()
    
    # Print summary statistics
    summary_serial = f"{serial_number}_{revision}" if revision > 0 else serial_number
    print(f"\nSummary for Camera {summary_serial}:")
    print(f"  Light histogram - Weighted Mean: {light_mean:.1f}, Temp: {light_temp:.1f}°C")
    print(f"  Dark histogram  - Weighted Mean: {dark_mean:.1f}, Temp: {dark_temp:.1f}°C")
    print(f"  Dynamic range: {light_mean - dark_mean:.1f}")


def main():
    """Main function to handle command line arguments and run the plotting."""
    if len(sys.argv) != 3:
        print("Usage: python plot_test_data.py <serial_number> <revision>")
        print("Example: python plot_test_data.py 1 0")
        print("Example: python plot_test_data.py 1 1")
        sys.exit(1)
    
    serial_number = sys.argv[1]
    
    try:
        revision = int(sys.argv[2])
    except ValueError:
        print(f"Error: Revision must be an integer, got: {sys.argv[2]}")
        sys.exit(1)
    
    if revision < 0:
        print(f"Error: Revision must be >= 0, got: {revision}")
        sys.exit(1)
    
    revision_str = f"_{revision}" if revision > 0 else "(no suffix)"
    print(f"Plotting test data for camera serial number: {serial_number}, revision: {revision_str}")
    plot_histograms(serial_number, revision)


if __name__ == "__main__":
    main()
