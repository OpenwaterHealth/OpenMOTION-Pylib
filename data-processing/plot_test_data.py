#!/usr/bin/env python3
"""
Plot test data from camera test rig.

This script plots light and dark histogram data from camera test captures.
Scans a folder for CSV files matching the histogram format and processes them all.

Usage:
    python plot_test_data.py <folder_path>

Arguments:
    folder_path: Path to folder containing histogram CSV files

The script will:
    - Find all files matching *_histogram_light*.csv and *_histogram_dark*.csv
    - Process each matching pair
    - Save PNG plots to <folder_path>/output/
    - Save summary CSV to <folder_path>/output/summary.csv
"""

import sys
import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
import re
from collections import defaultdict


def load_histogram_data(filepath):
    """
    Load histogram data from CSV file.
    
    Args:
        filepath (Path): Path to the CSV file
        
    Returns:
        tuple: (histogram_values, temperature, cam_id, weighted_mean, weighted_std)
    """
    try:
        df = pd.read_csv(filepath)
        
        # Extract histogram data (columns 0 to 1023, which are bins 0-1023)
        histogram_cols = [str(i) for i in range(1024)]
        histogram_values = df[histogram_cols].iloc[0].values
        
        # Extract temperature
        temperature = df['temperature'].iloc[0]
        
        # Extract cam_id (position)
        cam_id = df['cam_id'].iloc[0]
        
        # Calculate weighted mean and standard deviation
        weighted_mean = calculate_weighted_mean(histogram_values)
        weighted_std = calculate_weighted_std(histogram_values, weighted_mean)
        
        return histogram_values, temperature, cam_id, weighted_mean, weighted_std
        
    except Exception as e:
        print(f"Error loading data from {filepath}: {e}")
        return None, None, None, None, None


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


def calculate_weighted_std(histogram_data, weighted_mean):
    """
    Calculate the weighted standard deviation of histogram data.
    
    Args:
        histogram_data: Array of histogram bin values
        weighted_mean: Pre-calculated weighted mean
        
    Returns:
        float: Weighted standard deviation
    """
    try:
        if histogram_data is None or len(histogram_data) == 0:
            return 0.0
        
        if weighted_mean is None or np.isnan(weighted_mean):
            return 0.0
        
        # Calculate weighted variance: sum(weight * (value - mean)^2) / sum(weights)
        weighted_variance_sum = 0.0
        total_count = 0.0
        
        for bin_index, bin_value in enumerate(histogram_data):
            diff = bin_index - weighted_mean
            weighted_variance_sum += bin_value * (diff * diff)
            total_count += bin_value
        
        if total_count == 0:
            return 0.0
        
        variance = weighted_variance_sum / total_count
        return np.sqrt(variance)
        
    except Exception as e:
        print(f"Error calculating weighted standard deviation: {e}")
        return 0.0


def find_histogram_files(folder_path):
    """
    Find all histogram light and dark CSV files in the folder.
    
    Args:
        folder_path (Path): Path to folder to search
        
    Returns:
        dict: Dictionary mapping base names to (light_file, dark_file) tuples
    """
    folder = Path(folder_path)
    if not folder.exists():
        print(f"Error: Folder does not exist: {folder_path}")
        return {}
    
    # Find all light histogram files
    light_files = {}
    for csv_file in folder.rglob("*_histogram_light*.csv"):
        # Extract base name (everything before _histogram_light)
        # Pattern: {base}_histogram_light{suffix}.csv
        match = re.match(r'(.+?)_histogram_light(.*)\.csv', csv_file.name)
        if match:
            base_name = match.group(1)
            suffix = match.group(2) if match.group(2) else ""
            key = (base_name, suffix, csv_file.parent)  # Include parent directory
            light_files[key] = csv_file
    
    # Find matching dark files (look in same directory as light file)
    file_pairs = {}
    for (base_name, suffix, parent_dir), light_file in light_files.items():
        dark_filename = f"{base_name}_histogram_dark{suffix}.csv"
        # Look for dark file in the same directory as the light file
        dark_file = parent_dir / dark_filename
        if not dark_file.exists():
            print(f"Warning: No matching dark file found for {light_file.name} (looked in {parent_dir})")
            continue
        
        file_pairs[(base_name, suffix)] = (light_file, dark_file)
    
    return file_pairs


def extract_serial_number(filename):
    """
    Extract serial number from filename.
    Assumes format is {serial}_histogram_{type}{suffix}.csv
    """
    match = re.match(r'(.+?)_histogram_', filename)
    if match:
        return match.group(1)
    return filename.replace('_histogram_light', '').replace('_histogram_dark', '').replace('.csv', '')


def extract_aperture_size(file_path):
    """
    Extract aperture size from file path.
    Looks for patterns like "3mm", "1.5mm", "0.75mm", etc.
    
    Args:
        file_path (Path or str): Path to the file or path string
        
    Returns:
        str: Aperture size (e.g., "3mm", "1.5mm", "0.75mm") or "Unknown"
    """
    path_str = str(file_path)
    
    # Look for patterns like Xmm, X.Xmm, X.XXmm in the path
    # Pattern: digits, optional decimal point, more digits, then "mm"
    match = re.search(r'(\d+\.?\d*)mm', path_str)
    if match:
        return match.group(1) + "mm"
    
    return "Unknown"


def plot_histograms(light_file, dark_file, output_dir, serial_number, suffix="", display_serial=None):
    """
    Plot light and dark histograms for the given files.
    
    Args:
        light_file (Path): Path to light histogram CSV
        dark_file (Path): Path to dark histogram CSV
        output_dir (Path): Directory to save output PNG
        serial_number (str): Serial number for naming
        suffix (str): Revision suffix (e.g., "_1", "_2", or empty string)
        display_serial (str): Serial number with suffix for display (optional)
        
    Returns:
        tuple: (light_data, dark_data) where each is (temperature, cam_id, weighted_mean, weighted_std)
    """
    # Load data
    print(f"Loading light histogram from: {light_file}")
    light_hist, light_temp, light_cam_id, light_mean, light_std = load_histogram_data(light_file)
    
    print(f"Loading dark histogram from: {dark_file}")
    dark_hist, dark_temp, dark_cam_id, dark_mean, dark_std = load_histogram_data(dark_file)
    
    if light_hist is None or dark_hist is None:
        print(f"Error: Failed to load histogram data for {serial_number}")
        return None, None
    
    # Use display_serial if provided, otherwise construct from serial_number and suffix
    if display_serial is None:
        display_serial = f"{serial_number}{suffix}" if suffix else serial_number
    
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
    plt.title(f'Camera {display_serial} Test Data\n'
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
    output_filename = f"{serial_number}_test_plot{suffix}.png"
    output_file = output_dir / output_filename
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"Plot saved to: {output_file}")
    
    plt.close()  # Close figure to free memory
    
    # Return data for summary CSV
    light_data = (light_temp, light_cam_id, light_mean, light_std)
    dark_data = (dark_temp, dark_cam_id, dark_mean, dark_std)
    return light_data, dark_data


def process_folder(folder_path):
    """
    Process all histogram files in the given folder.
    
    Args:
        folder_path (str): Path to folder containing CSV files
    """
    folder = Path(folder_path)
    output_dir = folder / "output"
    output_dir.mkdir(exist_ok=True)
    
    print(f"Scanning folder: {folder}")
    print(f"Output directory: {output_dir}")
    
    # Find all histogram file pairs
    file_pairs = find_histogram_files(folder)
    
    if not file_pairs:
        print("No matching histogram files found!")
        return
    
    print(f"\nFound {len(file_pairs)} file pairs to process\n")
    
    # Store results for summary CSV
    results = []
    
    # Process each pair
    for (base_name, suffix), (light_file, dark_file) in file_pairs.items():
        serial_number = base_name
        
        # Add suffix to serial number for display if present
        if suffix:
            display_serial = f"{serial_number}{suffix}"
        else:
            display_serial = serial_number
        
        print(f"\nProcessing: {display_serial}")
        print(f"  Light: {light_file.name}")
        print(f"  Dark:  {dark_file.name}")
        
        # Plot and get data
        light_data, dark_data = plot_histograms(light_file, dark_file, output_dir, serial_number, suffix, display_serial)
        
        if light_data is None or dark_data is None:
            continue
        
        light_temp, light_cam_id, light_mean, light_std = light_data
        dark_temp, dark_cam_id, dark_mean, dark_std = dark_data
        
        # Calculate relative paths
        light_relative_path = light_file.relative_to(folder)
        dark_relative_path = dark_file.relative_to(folder)
        
        # Extract aperture size from file path
        aperture_size = extract_aperture_size(light_file)
        
        # Add to results
        results.append({
            'filename': light_file.name,
            'relative_path': str(light_relative_path).replace('\\', '/'),  # Use forward slashes for consistency
            'histogram_type': 'light',
            'serial_number': serial_number,
            'aperture_size': aperture_size,
            'position': light_cam_id,
            'temperature': light_temp,
            'weighted_mean': light_mean,
            'weighted_std': light_std
        })
        
        results.append({
            'filename': dark_file.name,
            'relative_path': str(dark_relative_path).replace('\\', '/'),  # Use forward slashes for consistency
            'histogram_type': 'dark',
            'serial_number': serial_number,
            'aperture_size': aperture_size,
            'position': dark_cam_id,
            'temperature': dark_temp,
            'weighted_mean': dark_mean,
            'weighted_std': dark_std
        })
    
    # Save summary CSV
    if results:
        summary_df = pd.DataFrame(results)
        summary_file = output_dir / "summary.csv"
        summary_df.to_csv(summary_file, index=False)
        print(f"\nSummary CSV saved to: {summary_file}")
        print(f"Processed {len(results)} files total ({len(results)//2} pairs)")
    else:
        print("\nNo valid results to save.")


def main():
    """Main function to handle command line arguments and run the processing."""
    if len(sys.argv) != 2:
        print("Usage: python plot_test_data.py <folder_path>")
        print("Example: python plot_test_data.py qisda_data")
        sys.exit(1)
    
    folder_path = sys.argv[1]
    
    print(f"Processing histogram files in: {folder_path}")
    process_folder(folder_path)


if __name__ == "__main__":
    main()
