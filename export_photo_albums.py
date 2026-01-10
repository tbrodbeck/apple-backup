#!/usr/bin/env python3
"""
Export iCloud Photos album structure from Photos.app database.
Creates a JSON mapping and optionally copies/symlinks files into album folders.
"""

import sqlite3
import json
import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# Photos library paths
PHOTOS_LIBRARY = os.path.expanduser("~/Pictures/Photos Library.photoslibrary")
PHOTOS_DB = os.path.join(PHOTOS_LIBRARY, "database", "Photos.sqlite")
ORIGINALS_DIR = os.path.join(PHOTOS_LIBRARY, "originals")

# icloudpd backup location
ICLOUDPD_BACKUP = os.path.expanduser("~/icloud-photos-backup")


def _get_albums_with_photos(db_path: str) -> dict:
    """Extract all user albums and their photo filenames from Photos.sqlite.

    Args:
        db_path: path to Photos.sqlite database

    Returns:
        dict mapping album names to album data (id, photos, folder path)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get folder hierarchy first
    cursor.execute("""
        SELECT Z_PK, ZTITLE, ZPARENTFOLDER
        FROM ZGENERICALBUM
        WHERE ZKIND = 4000 AND ZTITLE IS NOT NULL
    """)
    folders = {row[0]: {"title": row[1], "parent": row[2]} for row in cursor.fetchall()}

    def get_folder_path(album_parent_id):
        """Build folder path for an album."""
        path_parts = []
        current = album_parent_id
        while current and current in folders:
            path_parts.insert(0, folders[current]["title"])
            current = folders[current]["parent"]
        return "/".join(path_parts) if path_parts else None

    # Get all user-created albums (ZKIND=2) with their photos, including original filename and folder
    cursor.execute("""
        SELECT
            alb.ZTITLE,
            alb.Z_PK,
            alb.ZCACHEDCOUNT,
            alb.ZPARENTFOLDER,
            asset.ZDIRECTORY,
            asset.ZFILENAME,
            asset.ZUUID,
            attr.ZORIGINALFILENAME
        FROM ZGENERICALBUM alb
        LEFT JOIN Z_32ASSETS j ON j.Z_32ALBUMS = alb.Z_PK
        LEFT JOIN ZASSET asset ON asset.Z_PK = j.Z_3ASSETS
        LEFT JOIN ZADDITIONALASSETATTRIBUTES attr ON attr.ZASSET = asset.Z_PK
        WHERE alb.ZTITLE IS NOT NULL
            AND alb.ZKIND = 2
        ORDER BY alb.ZTITLE, asset.ZFILENAME
    """)

    albums = {}
    for title, album_pk, cached_count, parent_folder, directory, filename, uuid, original_filename in cursor.fetchall():
        if title not in albums:
            folder_path = get_folder_path(parent_folder)
            albums[title] = {
                "id": album_pk,
                "expected_count": cached_count,
                "folder": folder_path,
                "photos": []
            }

        if filename:
            photo_info = {
                "filename": filename,
                "uuid": uuid,
                "relative_path": f"{directory}/{filename}" if directory else filename,
                "original_filename": original_filename
            }
            albums[title]["photos"].append(photo_info)

    conn.close()
    return albums


def _get_favorites(db_path: str) -> list:
    """Extract all favorite photos from Photos.sqlite.

    Args:
        db_path: path to Photos.sqlite database

    Returns:
        list of favorite photo dicts (filename, uuid, path, original_filename)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            asset.ZFILENAME,
            asset.ZUUID,
            asset.ZDIRECTORY,
            attr.ZORIGINALFILENAME
        FROM ZASSET asset
        LEFT JOIN ZADDITIONALASSETATTRIBUTES attr ON attr.ZASSET = asset.Z_PK
        WHERE asset.ZFAVORITE = 1
        ORDER BY asset.ZFILENAME
    """)

    favorites = []
    for filename, uuid, directory, original_filename in cursor.fetchall():
        if filename:
            favorites.append({
                "filename": filename,
                "uuid": uuid,
                "relative_path": f"{directory}/{filename}" if directory else filename,
                "original_filename": original_filename
            })

    conn.close()
    return favorites


def _export_album_mapping(albums: dict, favorites: list, output_path: str) -> None:
    """Export album structure as JSON.

    Args:
        albums: dict of album data from _get_albums_with_photos
        favorites: list of favorites from _get_favorites
        output_path: path to write JSON file
    """
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "photos_library": PHOTOS_LIBRARY,
        "total_albums": len(albums),
        "total_favorites": len(favorites),
        "albums": {},
        "favorites": {
            "photo_count": len(favorites),
            "photos": [
                {
                    "uuid_filename": p["filename"],
                    "original_filename": p["original_filename"]
                }
                for p in favorites
            ]
        }
    }

    for album_name, album_data in albums.items():
        export_data["albums"][album_name] = {
            "photo_count": len(album_data["photos"]),
            "expected_count": album_data["expected_count"],
            "folder": album_data.get("folder"),
            "photos": [
                {
                    "uuid_filename": p["filename"],
                    "original_filename": p["original_filename"]
                }
                for p in album_data["photos"]
            ]
        }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)

    print(f"Exported album mapping to: {output_path}")


def _build_filename_index(backup_dir: str) -> dict:
    """Build an index of original filenames to their paths in the backup.

    Args:
        backup_dir: root directory of icloudpd backup

    Returns:
        dict mapping uppercase filenames to full paths
    """
    print(f"Building filename index from {backup_dir}...")
    index = {}
    for root, dirs, files in os.walk(backup_dir):
        for f in files:
            if f.startswith('.'):
                continue
            full_path = os.path.join(root, f)
            # Index by filename (case-insensitive)
            index[f.upper()] = full_path
    print(f"  Indexed {len(index)} files")
    return index


def _copy_albums_to_folders(albums: dict, favorites: list, output_dir: str, source_dir: str, use_symlinks: bool = True) -> None:
    """Copy or symlink photos into album folders from icloudpd backup.

    Args:
        albums: dict of album data from _get_albums_with_photos
        favorites: list of favorites from _get_favorites
        output_dir: destination directory for album folders
        source_dir: source directory with photos (icloudpd backup)
        use_symlinks: if True, create symlinks; if False, copy files
    """
    os.makedirs(output_dir, exist_ok=True)

    # Build index of files in source directory
    file_index = _build_filename_index(source_dir)

    total_found = 0
    total_missing = 0

    def sanitize_name(name):
        return "".join(c if c.isalnum() or c in ' -_' else '_' for c in name)

    def copy_photos(photos, dest_dir):
        """Copy or symlink photos to destination directory."""
        os.makedirs(dest_dir, exist_ok=True)
        copied = 0
        for photo in photos:
            original_filename = photo.get("original_filename")
            if not original_filename:
                continue

            src = file_index.get(original_filename.upper())
            if src:
                dst = os.path.join(dest_dir, original_filename)
                if not os.path.exists(dst):
                    try:
                        if use_symlinks:
                            os.symlink(src, dst)
                        else:
                            shutil.copy2(src, dst)
                        copied += 1
                    except Exception as e:
                        print(f"    Error: {e}")
        return copied

    # Export albums with folder hierarchy
    for album_name, album_data in albums.items():
        if not album_data["photos"]:
            continue

        # Build path with folder hierarchy
        folder_path = album_data.get("folder")
        if folder_path:
            # Sanitize each folder part
            folder_parts = [sanitize_name(p) for p in folder_path.split("/")]
            album_dir = os.path.join(output_dir, *folder_parts, sanitize_name(album_name))
        else:
            album_dir = os.path.join(output_dir, sanitize_name(album_name))

        copied = copy_photos(album_data["photos"], album_dir)
        total_found += copied
        total_missing += len(album_data["photos"]) - copied

        display_path = f"{folder_path}/{album_name}" if folder_path else album_name
        print(f"  {display_path}: {copied}/{len(album_data['photos'])} photos")

    # Export favorites
    if favorites:
        favorites_dir = os.path.join(output_dir, "_Favorites")
        copied = copy_photos(favorites, favorites_dir)
        total_found += copied
        total_missing += len(favorites) - copied
        print(f"  _Favorites: {copied}/{len(favorites)} photos")

    print(f"\nTotal: {total_found} found, {total_missing} missing")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Export Photos.app album structure")
    parser.add_argument("--output-json", "-j", help="Output JSON mapping file")
    parser.add_argument("--output-dir", "-d", help="Output directory for album folders")
    parser.add_argument("--source-dir", "-s", default=ICLOUDPD_BACKUP, help="Source directory with photos (icloudpd backup)")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of symlinking")
    parser.add_argument("--list", "-l", action="store_true", help="Just list albums")
    args = parser.parse_args()

    if not os.path.exists(PHOTOS_DB):
        print(f"Error: Photos database not found at {PHOTOS_DB}")
        sys.exit(1)

    print("Reading Photos.app database...")
    albums = _get_albums_with_photos(PHOTOS_DB)
    favorites = _get_favorites(PHOTOS_DB)

    print(f"\nFound {len(albums)} albums and {len(favorites)} favorites:")
    for name, data in sorted(albums.items()):
        folder = data.get("folder")
        path = f"{folder}/{name}" if folder else name
        print(f"  {path}: {len(data['photos'])} photos")
    print(f"  _Favorites: {len(favorites)} photos")

    if args.list:
        return

    if args.output_json:
        _export_album_mapping(albums, favorites, args.output_json)

    if args.output_dir:
        print(f"\nExporting to folders: {args.output_dir}")
        print(f"Source: {args.source_dir}")
        _copy_albums_to_folders(albums, favorites, args.output_dir, args.source_dir, use_symlinks=not args.copy)

    if not args.output_json and not args.output_dir:
        print("\nUse --output-json or --output-dir to export")
        print("Use --list to just list albums")


if __name__ == "__main__":
    main()
