#!/usr/bin/env bash
# Prepare the DATABASE VM (Debian 12): PostgreSQL 17 with TLS, scram passwords, auditing and WAL archiving. Run once, as root, ON the database VM.
#   sudo ./db-vm-setup.sh --listen 10.0.0.3 --allow 10.0.0.0/24 [--wal-archive /mnt/wal]
#
# --listen   the VM's private address (never a public one)
# --allow    the private network the app and services VMs live in; every other client is rejected
# --wal-archive  a directory on a SEPARATE disk for archived WAL (point-in-time recovery); default /var/lib/postgresql/wal_archive
#
# What it does not do: create the zid_* roles and databases (db-init.sh does, from a machine that reaches this one), take backups, or set up the
# standby. README.md covers those. This script was written from the PostgreSQL 17 documentation and has not run on a real VM yet: the first run
# is the test, so read the diff it prints and keep the backups it makes of the files it replaces.
set -euo pipefail

LISTEN="" ALLOW="" WAL="/var/lib/postgresql/wal_archive"
while [ $# -gt 0 ]; do
  case "$1" in
    --listen) LISTEN="$2"; shift 2 ;;
    --allow) ALLOW="$2"; shift 2 ;;
    --wal-archive) WAL="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ -n "$LISTEN" ] && [ -n "$ALLOW" ] || { echo "usage: $0 --listen <private ip> --allow <private cidr> [--wal-archive <dir>]" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 2; }
case "$LISTEN" in 127.*|0.0.0.0|"") echo "--listen must be the VM's private address" >&2; exit 2 ;; esac

echo "== packages (PostgreSQL 17 from the PGDG repository, pgaudit)"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg openssl lsb-release
install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list
apt-get update -qq
apt-get install -y -qq postgresql-17 postgresql-17-pgaudit

CONF=/etc/postgresql/17/main
TLS="$CONF/tls"
echo "== TLS: a private CA and a server certificate for $LISTEN (valid 2 years; renew before it lapses)"
install -d -m 750 -o postgres -g postgres "$TLS"
if [ ! -f "$TLS/server.crt" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 -subj "/CN=Zoiko iD database CA" \
    -keyout "$TLS/ca.key" -out "$TLS/ca.crt" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj "/CN=$LISTEN" -keyout "$TLS/server.key" -out "$TLS/server.csr" 2>/dev/null
  printf 'subjectAltName=IP:%s\nextendedKeyUsage=serverAuth\n' "$LISTEN" > "$TLS/san.cnf"
  openssl x509 -req -in "$TLS/server.csr" -CA "$TLS/ca.crt" -CAkey "$TLS/ca.key" -CAcreateserial -days 730 -extfile "$TLS/san.cnf" -out "$TLS/server.crt" 2>/dev/null
  chown postgres:postgres "$TLS"/*
  chmod 600 "$TLS/server.key" "$TLS/ca.key"
  chmod 644 "$TLS/ca.crt" "$TLS/server.crt"
fi

echo "== WAL archive directory: $WAL (put it on its own disk; it is what point-in-time recovery replays)"
install -d -m 700 -o postgres -g postgres "$WAL"

echo "== configuration (a drop-in file; the stock postgresql.conf is untouched)"
install -d "$CONF/conf.d"
cat > "$CONF/conf.d/zoiko.conf" <<EOF
# Zoiko iD database VM (db-vm-setup.sh). Do not listen on a public address.
listen_addresses = '$LISTEN,127.0.0.1'
ssl = on
ssl_cert_file = '$TLS/server.crt'
ssl_key_file = '$TLS/server.key'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = scram-sha-256
shared_preload_libraries = 'pgaudit'
pgaudit.log = 'ddl, role'
log_connections = on
log_disconnections = on
log_line_prefix = '%m [%p] %q%u@%d '
# point-in-time recovery and a streaming standby
wal_level = replica
archive_mode = on
archive_command = 'test ! -f $WAL/%f && cp %p $WAL/%f'
max_wal_senders = 5
wal_keep_size = '2GB'
EOF

echo "== who may connect (the old pg_hba.conf is kept as pg_hba.conf.orig)"
cp -n "$CONF/pg_hba.conf" "$CONF/pg_hba.conf.orig"
cat > "$CONF/pg_hba.conf" <<EOF
# Zoiko iD: local admin by OS user; the private network over TLS with scram; nothing else.
local   all          postgres                       peer
local   all          all                            scram-sha-256
hostssl all          all          $ALLOW            scram-sha-256
hostssl replication  replicator   $ALLOW            scram-sha-256
host    all          all          0.0.0.0/0         reject
EOF

systemctl restart postgresql
sleep 2
sudo -u postgres psql -Atc "select version()" | head -1
sudo -u postgres psql -Atc "show ssl; show listen_addresses; show archive_mode;"
echo
echo "Done. Copy $TLS/ca.crt to the app and services VMs if you want clients to verify the server (sslmode=verify-full);"
echo "the connection URLs in env.example use ssl=require, which encrypts but does not check who answers."
echo "Next, from a machine that can reach this VM: ./db-init.sh pre"
