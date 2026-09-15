#!/usr/bin/env bash
# PyXie Manager restore. Two modes:
#
#   ops/restore.sh --verify-only <backup-dir>
#       Checksum-verifies a backup and loads its DB dump into a THROWAWAY
#       database (pyxie_manager_restore_test) on the SAME running Postgres,
#       comparing row counts against the live DB. Never touches the live
#       app, live DB, or live .env. This is the mode used to actually TEST
#       a backup without any risk -- run it after every backup.sh, and
#       definitely before relying on a backup for real.
#
#   ops/restore.sh --full <backup-dir> <target-app-dir>
#       Full recovery: unpacks source+.env into target-app-dir and loads the
#       DB dump into the live 'pyxie_manager' database. Destructive to
#       whatever is currently in target-app-dir/live DB -- only for actual
#       disaster recovery, never run casually.

set -euo pipefail

MODE="${1:-}"
BACKUP_DIR="${2:-}"

if [[ -z "$MODE" || -z "$BACKUP_DIR" || ! -d "$BACKUP_DIR" ]]; then
    echo "usage: $0 --verify-only <backup-dir>" >&2
    echo "       $0 --full <backup-dir> <target-app-dir>" >&2
    exit 1
fi

echo "[restore] verifying checksums against manifest..."
(cd "$BACKUP_DIR" && awk '/^---$/{found=1;next} found' manifest.txt | sha256sum -c -)
echo "[restore] checksums OK"

if [[ "$MODE" == "--verify-only" ]]; then
    echo "[restore] loading dump into throwaway DB pyxie_manager_restore_test..."
    docker exec pyxie-manager-db psql -U pyxie_manager -d postgres \
        -c "DROP DATABASE IF EXISTS pyxie_manager_restore_test;" \
        -c "CREATE DATABASE pyxie_manager_restore_test OWNER pyxie_manager;"
    gunzip -c "$BACKUP_DIR/pyxie.sql.gz" | docker exec -i pyxie-manager-db \
        psql -U pyxie_manager -d pyxie_manager_restore_test -q

    echo "[restore] comparing row counts: live vs restored-test"
    for table in organizations sites clusters nodes workloads audit_events users operation_types operations; do
        live=$(docker exec pyxie-manager-db psql -U pyxie_manager -d pyxie_manager -tAc "SELECT count(*) FROM $table" 2>/dev/null || echo "n/a")
        restored=$(docker exec pyxie-manager-db psql -U pyxie_manager -d pyxie_manager_restore_test -tAc "SELECT count(*) FROM $table" 2>/dev/null || echo "n/a")
        printf "  %-20s live=%-6s restored=%-6s %s\n" "$table" "$live" "$restored" "$([[ "$live" == "$restored" ]] && echo OK || echo MISMATCH)"
    done

    docker exec pyxie-manager-db psql -U pyxie_manager -d postgres -c "DROP DATABASE pyxie_manager_restore_test;"
    echo "[restore] verify-only complete -- throwaway DB dropped, live system untouched"
    exit 0
fi

if [[ "$MODE" == "--full" ]]; then
    TARGET="${3:?target-app-dir required for --full}"
    echo "[restore] FULL RECOVERY into $TARGET -- this will overwrite what's there."
    read -r -p "Type YES to continue: " confirm
    [[ "$confirm" == "YES" ]] || { echo "aborted"; exit 1; }

    mkdir -p "$TARGET"
    tar -x --zstd -f "$BACKUP_DIR/source.tar.zst" -C "$TARGET"
    cp "$BACKUP_DIR/env.backup" "$TARGET/.env"
    chmod 600 "$TARGET/.env"

    echo "[restore] source + .env restored to $TARGET. Bring up db+redis, then:"
    echo "  gunzip -c $BACKUP_DIR/pyxie.sql.gz | docker exec -i pyxie-manager-db psql -U pyxie_manager -d pyxie_manager"
    echo "  cd $TARGET && docker compose up -d"
    exit 0
fi

echo "unknown mode $MODE" >&2
exit 1
