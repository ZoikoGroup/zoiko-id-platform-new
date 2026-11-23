#!/usr/bin/env bash
# A restore drill for the rehearsal database: a backup nobody has restored is not a backup.
#
#   tools/scripts/restore_drill.sh            # take a backup, restore it into a scratch database, check it, write the record, drop the scratch database
#   tools/scripts/restore_drill.sh --keep     # keep the scratch database for inspection (drop it yourself)
#
# 1. counts the rows of the tables that matter, takes a consistent backup (pg_dump -Fc, streamed to ~/.zoiko/backups), and counts again;
# 2. restores it into a NEW database (zid_restore_drill) beside the live one, timing it (the measured restore time is the drill's RTO figure);
# 3. checks the restore against the source: every counted table's restored row count lies between the count before and after the backup (rows only move
#    forward while it runs), the migration versions are identical, and the number of tables, indexes, triggers and row-level-security policies match;
# 4. runs the platform's own evidence verifier on the RESTORED copy (the hash chain, every entry signature, every checkpoint's root, link and signature, every
#    timestamp token) inside the platform image, with the signing keys' PUBLIC half and the timestamp authority's root;
# 5. writes docs/runbooks/drills/restore-drill-<date>.md with what it found, and exits 0 only if every check passed.
#
# It never writes to the live database and never prints a secret. The live database is read twice (counts) and dumped once. Docker, and the rehearsal stack running.
set -euo pipefail

KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="${COMPOSE_PROJECT_NAME:-zoiko-id-rehearsal}"
PG="${PROJECT}-postgres-1"; CELL="${PROJECT}-cell-1"
LIVE=zid_cell; DRILL=zid_restore_drill
STAMP="$(date -u +%Y%m%d-%H%M%S)"; DAY="$(date -u +%Y-%m-%d)"
BACKUPS="${HOME}/.zoiko/backups"; mkdir -p "$BACKUPS"
DUMP="${BACKUPS}/zid_cell-drill-${STAMP}.dump"
OUTDIR="${ROOT}/docs/runbooks/drills"; mkdir -p "$OUTDIR"
RECORD="${OUTDIR}/restore-drill-${DAY}.md"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
# Docker Desktop on Windows mounts a Windows path (C:/...), not Git Bash's /tmp or /c/... form
win() { cygpath -m "$1" 2>/dev/null || printf '%s' "$1"; }

live() { docker exec "$PG" psql -U postgres -d "$LIVE" -At -v ON_ERROR_STOP=1 "$@"; }
drill() { docker exec "$PG" psql -U postgres -d "$DRILL" -At -v ON_ERROR_STOP=1 "$@"; }
count() { local db="$1" t="$2"; docker exec "$PG" psql -U postgres -d "$db" -At -c "select count(*) from $t"; }
FAIL=0; LINES=()
note() { LINES+=("$1"); }

TABLES=(platform.tenant core.principal authz.authority_grant agent.agent_profile agent.mandate agent.build_artifact agent.workload_binding agent.api_client_binding
        agent.governance_event agent.run_ledger evidence.ledger_entry evidence.checkpoint evidence.audit_record evidence.decision_receipt evidence.evidence_object keys.key_version)

echo "restore drill ${STAMP}: counting the live tables"
declare -A BEFORE AFTER RESTORED
for t in "${TABLES[@]}"; do BEFORE[$t]="$(count "$LIVE" "$t" 2>/dev/null || echo -1)"; done

echo "backing up ${LIVE} (consistent snapshot) to ${DUMP}"
T0=$(date +%s)
docker exec "$PG" pg_dump -U postgres -d "$LIVE" -Fc > "$DUMP"
T1=$(date +%s)
BYTES="$(wc -c < "$DUMP" | tr -d ' ')"
for t in "${TABLES[@]}"; do AFTER[$t]="$(count "$LIVE" "$t" 2>/dev/null || echo -1)"; done

echo "restoring into a new database ${DRILL}"
docker exec "$PG" psql -U postgres -d postgres -At -c "DROP DATABASE IF EXISTS ${DRILL}" >/dev/null
docker exec "$PG" psql -U postgres -d postgres -At -c "CREATE DATABASE ${DRILL}" >/dev/null
T2=$(date +%s)
docker exec -i "$PG" pg_restore -U postgres -d "$DRILL" --no-owner --no-privileges --exit-on-error < "$DUMP" 2> "$WORK/restore.err" || { echo "RESTORE FAILED"; head -5 "$WORK/restore.err"; FAIL=1; }
T3=$(date +%s)

BACKUP_S=$((T1-T0)); RESTORE_S=$((T3-T2))
note "Backup: ${BYTES} bytes in ${BACKUP_S}s. Restore into a new database: ${RESTORE_S}s (this is the drill's measured restore time on this machine)."
if [ "$FAIL" -eq 0 ]; then
  ROWS=""
  for t in "${TABLES[@]}"; do
    RESTORED[$t]="$(count "$DRILL" "$t" 2>/dev/null || echo -1)"
    b="${BEFORE[$t]}"; a="${AFTER[$t]}"; r="${RESTORED[$t]}"
    if [ "$b" = "-1" ] || [ "$r" = "-1" ]; then verdict="MISSING"; FAIL=1
    elif [ "$r" -ge "$b" ] && [ "$r" -le "$a" ]; then verdict="match"
    else verdict="DIFFERENT"; FAIL=1; fi
    ROWS="${ROWS}| \`${t}\` | ${b} | ${r} | ${a} | ${verdict} |"$'\n'
  done
  MIG_LIVE="$(live -c "select string_agg(table_name||'='||(xpath('/row/v/text()', query_to_xml('select version_num v from zid_migrations.'||table_name, true, true, '')))[1]::text, ',' order by table_name) from information_schema.tables where table_schema='zid_migrations'")"
  MIG_DRILL="$(drill -c "select string_agg(table_name||'='||(xpath('/row/v/text()', query_to_xml('select version_num v from zid_migrations.'||table_name, true, true, '')))[1]::text, ',' order by table_name) from information_schema.tables where table_schema='zid_migrations'")"
  [ "$MIG_LIVE" = "$MIG_DRILL" ] && MIG="identical" || { MIG="DIFFERENT"; FAIL=1; }
  shape() { "$1" -c "select (select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema'))||' tables, '||(select count(*) from pg_indexes where schemaname not in ('pg_catalog','information_schema'))||' indexes, '||(select count(*) from pg_policies)||' row-level-security policies, '||(select count(*) from pg_trigger where not tgisinternal)||' triggers'"; }
  S_LIVE="$(shape live)"; S_DRILL="$(shape drill)"
  [ "$S_LIVE" = "$S_DRILL" ] && SHAPE="identical (${S_DRILL})" || { SHAPE="DIFFERENT (live: ${S_LIVE}; restored: ${S_DRILL})"; FAIL=1; }
fi

# The platform's own evidence verifier, on the restored copy, in the platform image on the stack's network.
VERIFY="not run"
if [ "$FAIL" -eq 0 ]; then
  docker exec "$CELL" python -m zid_platform.entrypoints.keys public > "$WORK/keys.json" 2>/dev/null || { VERIFY="could not read the public keys"; FAIL=1; }
fi
if [ "$FAIL" -eq 0 ]; then
  IMAGE="$(docker inspect "$CELL" --format '{{.Config.Image}}')"; NET="$(docker inspect "$CELL" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')"
  PGPW="$(docker exec "$PG" printenv POSTGRES_PASSWORD)"
  TSAVOL="${PROJECT}_tsa-data"
  set +e
  MSYS_NO_PATHCONV=1 docker run --rm --network "$NET" -v "$(win "$WORK"):/drill:ro" -v "$(win "${ROOT}/tools/scripts"):/t:ro" -v "${TSAVOL}:/tsa:ro" \
    --entrypoint python "$IMAGE" /t/restore_drill_verify.py --db-url "postgresql+asyncpg://postgres:${PGPW}@postgres:5432/${DRILL}" --keys /drill/keys.json --tsa-ca /tsa/ca.pem \
    > "$WORK/verify.out" 2>"$WORK/verify.err"
  VCODE=$?
  set -e
  SUMMARY="$(grep '"summary"' "$WORK/verify.out" | tail -1 || true)"
  if [ "$VCODE" -eq 0 ]; then VERIFY="every partition verified: ${SUMMARY}"; else VERIFY="FAILED (exit ${VCODE}): ${SUMMARY:-see output}"; FAIL=1; fi
fi

[ "$KEEP" -eq 1 ] || docker exec "$PG" psql -U postgres -d postgres -At -c "DROP DATABASE IF EXISTS ${DRILL}" >/dev/null

RESULT="PASSED"; [ "$FAIL" -eq 0 ] || RESULT="FAILED"
PARTS=""
if [ -s "$WORK/verify.out" ]; then
  PARTS="$(python - "$WORK/verify.out" <<'PY'
import json, sys
rows = []
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if "partition_id" in d:
        rows.append(f"| `{d['partition_id']}` | {d.get('entries', '')} | {d.get('checkpoints', '')} | {d.get('timestamps_verified', '')} | {'verified' if d.get('ok') else 'PROBLEM: ' + str(d.get('error') or d.get('problems'))[:120]} |")
print("| Partition | Entries | Checkpoints | Timestamp tokens verified | |")
print("|---|---|---|---|---|")
print(chr(10).join(rows))
PY
)"
fi
if [ "$FAIL" -eq 0 ]; then
  SHOWS="It shows the backup restores completely and the restored evidence verifies."
else
  SHOWS="This drill FAILED, so it shows only what the checks above report; do not rely on this backup until the failure is explained."
fi
{
  echo "# Restore drill ${DAY}: ${RESULT}"
  echo
  echo "Run at ${STAMP} UTC by \`tools/scripts/restore_drill.sh\` against the rehearsal database \`${LIVE}\` (PostgreSQL 17, one machine, Docker). Engineering's drill, not an independent one."
  echo
  echo "${LINES[0]:-}"
  echo
  echo "## Row counts: live before the backup, restored, live after"
  echo
  echo "| Table | Before | Restored | After | |"
  echo "|---|---|---|---|---|"
  printf "%s" "${ROWS:-}"
  echo
  echo "## Shape"
  echo
  echo "- Migration versions: ${MIG:-not checked}"
  echo "- Schema: ${SHAPE:-not checked}"
  echo
  echo "## Evidence verification on the restored copy"
  echo
  echo "The platform's own verifier recomputed every partition's hash chain, entry signatures, checkpoint roots, links, signatures and timestamp tokens (against the rehearsal timestamp authority's root): ${VERIFY}."
  echo
  [ -z "$PARTS" ] || { echo "$PARTS"; echo; }
  echo "## What this does and does not show"
  echo
  echo "- ${SHOWS} The measured restore time is for this machine and this database size (the live database is $(docker exec "$PG" psql -U postgres -d "$LIVE" -At -c "select pg_size_pretty(pg_database_size('${LIVE}'))"))."
  echo "- It does not show recovery from the loss of the host, the master key file or the secrets volume: the backup holds wrapped keys, and is useless without the master key (CR-060). The drill that proves those is a restore on a different machine from the backup and the key custodians' copy."
  echo "- It is one backup of one point in time. The recovery point of a real deployment depends on how often backups run and whether write-ahead logs are shipped (\`deploy/gcp-vm/README.md\`)."
  echo "- The dump file is kept at \`~/.zoiko/backups/\` outside the repository."
} > "$RECORD"
echo "drill ${RESULT}; record written to ${RECORD}"
[ "$FAIL" -eq 0 ]
