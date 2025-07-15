import pandas as pd

CSV_FILE = "histogram.csv"
EXPECTED_SUM = 2457606
MAX_FRAME_ID = 255

def check_csv_integrity(csv_path):
    df = pd.read_csv(csv_path)

    df['frame_id'] = df['frame_id'].astype(int)
    df['cam_id'] = df['cam_id'].astype(int)
    df['sum'] = df['sum'].astype(int)

    errors_found = False
    error_counts = {
        'bad_sum': 0,
        'frame_id_skipped': 0,
        'bad_frame_cam_count': 0,
    }
    expected_fids = []

    # --- Add frame cycle index based on rollover detection ---
    frame_cycles = []
    last_frame_id = None
    cycle = 0
    for fid in df['frame_id']:
        if last_frame_id is not None and fid < last_frame_id:
            cycle += 1
        frame_cycles.append(cycle)
        last_frame_id = fid
    df['frame_cycle'] = frame_cycles

    # --- Constraint 1: Check sum column ---
    bad_sums = df[df['sum'] != EXPECTED_SUM]
    if not bad_sums.empty:
        print(f"[ERROR] {len(bad_sums)} rows have incorrect 'sum' values.")
        error_counts['bad_sum'] += len(bad_sums)
        errors_found = True

    # --- Constraint 2 + 3: Verify frame_id sequencing and cam count ---
    grouped = df.groupby(['frame_cycle', 'frame_id'], sort=True)
    expected_fid = None
    expected_cam_count = None

    for (cycle, fid), group in grouped:
        row_idx = group.index.min()  # First row index of this frame group
        cam_count = len(group)

        if expected_cam_count is None:
            expected_cam_count = cam_count
            print(f"[INFO] Setting expected cam_id count per frame to {expected_cam_count}")

        if cam_count != expected_cam_count:
            print(f"[WARN] Row {row_idx}: frame_id {fid} (cycle {cycle}) has {cam_count} cam_ids, expected {expected_cam_count}")
            error_counts['bad_frame_cam_count'] += 1
            errors_found = True

        if expected_fid is None:
            expected_fid = fid
        elif fid != expected_fid:
            print(f"[WARN] Row {row_idx}: frame_id skipped — expected {expected_fid}, got {fid} (cycle {cycle})")
            num_skipped = (fid - expected_fid) % 256
            error_counts['frame_id_skipped'] += num_skipped
            expected_fids.append((str(cycle), str(expected_fid)))
            expected_fid = fid

        expected_fid = (expected_fid + 1) % 256

    # --- Summary ---
    print("\n[INFO] Histogram count per cam_id:")
    print(df['cam_id'].value_counts().sort_index())

    print("\n[INFO] Error type counts:")
    for k, v in error_counts.items():
        print(f"  {k}: {v}")
    
    # if expected_fids:
    #     print("\n[INFO] Expected frame_ids that were skipped:")
    #     print(expected_fids)

    if not errors_found:
        print("\n[PASS] CSV passed all integrity checks.")
    else:
        print("\n[FAIL] One or more checks failed.")

if __name__ == "__main__":
    check_csv_integrity(CSV_FILE)
