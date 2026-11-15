#!/bin/sh
set -eu

export PGPASSWORD="${POSTGRES_PASSWORD}"

attempt=0
until psql \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --command="SELECT 1" >/dev/null 2>&1
do
  attempt=$((attempt + 1))
  if [ "${attempt}" -gt 30 ]; then
    echo "postgres did not accept queries" >&2
    exit 1
  fi
  sleep 1
done

psql \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --set=ON_ERROR_STOP=1 \
  --file=/schema.sql

echo "schema is ready"
