#!/usr/bin/env python3

import sqlite3
import shutil
import os
import sys
from pathlib import Path

VOICE_MEMOS_PATH = os.path.expanduser(
    "~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
)

def _sanitize_filename(name: str) -> str:
    """Remove invalid characters from filename.

    Args:
        name: filename to sanitize

    Returns:
        sanitized filename
    """
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name.strip()

def extract_voice_memos(output_dir: str) -> None:
    """Extract Voice Memos from local iCloud sync to output directory.

    Args:
        output_dir: destination directory for extracted memos

    Raises:
        FileNotFoundError: if CloudRecordings.db not found (iCloud sync not enabled)
    """
    db_path = os.path.join(VOICE_MEMOS_PATH, "CloudRecordings.db")

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"CloudRecordings.db not found at {db_path}. Make sure Voice Memos iCloud sync is enabled."
        )

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get folders
    cursor.execute("SELECT Z_PK, ZENCRYPTEDNAME FROM ZFOLDER")
    folders = {row[0]: row[1] for row in cursor.fetchall()}
    if folders:
        print(f"Found {len(folders)} folders: {', '.join(folders.values())}")

    # Get metadata: path, label, date, and folder
    # Skip Recently Deleted items and empty/ghost recordings
    cursor.execute("""
        SELECT ZPATH, ZCUSTOMLABELFORSORTING, ZDATE, ZFOLDER
        FROM ZCLOUDRECORDING
        WHERE ZEVICTIONDATE IS NULL AND ZPATH IS NOT NULL AND ZPATH != ''
    """)

    rows = cursor.fetchall()
    print(f"Found {len(rows)} Voice Memos")

    extracted = 0
    for path, label, zdate, folder_id in rows:
        source = os.path.join(VOICE_MEMOS_PATH, path)

        if os.path.exists(source):
            # Use label if available
            if label:
                filename = f"{_sanitize_filename(label)}.m4a"
            else:
                filename = path

            # Determine output directory (subfolder if in a folder)
            if folder_id and folder_id in folders:
                folder_name = _sanitize_filename(folders[folder_id])
                dest_dir = os.path.join(output_dir, folder_name)
                os.makedirs(dest_dir, exist_ok=True)
            else:
                dest_dir = output_dir

            dest = os.path.join(dest_dir, filename)

            # Handle duplicates
            counter = 1
            base, ext = os.path.splitext(dest)
            while os.path.exists(dest):
                dest = f"{base}_{counter}{ext}"
                counter += 1

            shutil.copy2(source, dest)

            # Set file modification time from database
            if zdate:
                unix_timestamp = zdate + 978307200
                os.utime(dest, (unix_timestamp, unix_timestamp))

            print(f"Extracted: {os.path.basename(dest)}")
            extracted += 1
        else:
            print(f"Warning: Source file not found: {source}")

    conn.close()
    print(f"\nDone! Extracted {extracted} Voice Memos to: {output_dir}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <output_dir>")
        sys.exit(1)

    output_dir = sys.argv[1]
    os.makedirs(output_dir, exist_ok=True)
    extract_voice_memos(output_dir)
