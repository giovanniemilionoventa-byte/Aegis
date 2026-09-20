#!/usr/bin/env bash
# Back up the Aegis database, consistent while the service is running.
#
#   bash scripts/backup.sh
#
# Writes backups/aegis-<UTC date>.db.gz and deletes copies older than 14 days.
# Run it daily from cron, e.g. (crontab -e):
#   17 3 * * *  cd /path/to/repo && bash scripts/backup.sh >> backups/backup.log 2>&1
#
# Copy backups/ somewhere else too (another machine, object storage): a backup on
# the same disk is not a backup. The evidence chain's integrity depends on this
# file, and so does everything your customers approved.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.hosted.yml"
mkdir -p backups
stamp="$(date -u +%Y-%m-%d-%H%M)"

# SQLite's online backup API: safe with the service writing, unlike copying the file.
$COMPOSE exec -T control-plane python - <<'PY'
import sqlite3

source = sqlite3.connect("/data/aegis.db")
target = sqlite3.connect("/data/backup.tmp.db")
source.backup(target)
target.close()
source.close()
PY

$COMPOSE cp control-plane:/data/backup.tmp.db "backups/aegis-$stamp.db"
$COMPOSE exec -T control-plane rm -f /data/backup.tmp.db
gzip -f "backups/aegis-$stamp.db"
find backups -name 'aegis-*.db.gz' -mtime +14 -delete
echo "backup ok: backups/aegis-$stamp.db.gz"
