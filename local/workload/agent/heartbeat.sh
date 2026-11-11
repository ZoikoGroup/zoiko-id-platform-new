#!/bin/sh
# Stand-in agent: one log line a minute so it is visibly alive.
while true; do
  echo "invoice-agent 1.0.0 alive $(date -u +%FT%TZ)"
  sleep 60
done
