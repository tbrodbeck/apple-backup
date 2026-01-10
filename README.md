# apple-backup

Scripts for backing up iCloud data on macOS.

## iCloud Photos

Use [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader) to download photos:

```bash
while true; do
  icloudpd -u "your@icloud.com" -d ~/icloud-photos-backup --keep-icloud-recent-days 0 --log-level info
  if [ $? -eq 0 ]; then
    break
  fi
  echo "Failed, retrying in 10 seconds..."
  sleep 10
done
```

### export_photo_albums.py

Export album structure from Photos.app (albums are not preserved by icloudpd).

```bash
# List all albums
python export_photo_albums.py --list

# Export album mapping as JSON
python export_photo_albums.py --output-json albums.json

# Create album folders with symlinks to photos from icloudpd backup
python export_photo_albums.py --output-dir ./albums --source-dir ~/icloud-photos-backup

# Copy files instead of symlinking
python export_photo_albums.py --output-dir ./albums --source-dir ~/icloud-photos-backup --copy
```

## Voice Memos

### extract_voice_memos.py

Extract Voice Memos from the local iCloud-synced Voice Memos app.

```bash
python extract_voice_memos.py <output_dir>
```

- Extracts recordings with their display names (locations or custom labels)
- Preserves folder organization
- Sets file modification times from recording dates
- Requires Voice Memos iCloud sync to be enabled
