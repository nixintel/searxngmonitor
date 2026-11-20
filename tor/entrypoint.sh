#!/bin/sh
# Runs Tor with the torrc the web app writes (shared volume) and restarts it when that file changes.
# Once Tor has fully connected it creates $READY; searxng-tor waits for that file before it starts, because
# SearXNG refuses to start with using_tor_proxy if Tor isn't working.
CONF=/etc/tor-config/torrc
READY=/var/run/tor-state/ready
CONNECTED=/tmp/tor-connected   # private to this container: set once this Tor process has fully connected

while true; do
  rm -f "$READY" "$CONNECTED"
  if [ ! -f "$CONF" ]; then
    echo "Waiting for $CONF"
    sleep 3
    continue
  fi
  start=$(md5sum "$CONF")
  if grep -q '^DisableNetwork 1' "$CONF"; then
    echo "Tor is turned off in Settings: not connecting to the Tor network."
  fi
  (
    tor -f "$CONF" 2>&1 | while IFS= read -r line; do
      echo "$line"
      case "$line" in *"Bootstrapped 100%"*) touch "$CONNECTED" "$READY" ;; esac
    done
  ) &
  pid=$!
  while kill -0 "$pid" 2>/dev/null && [ "$(md5sum "$CONF" 2>/dev/null)" = "$start" ]; do
    # Restore the ready flag if something removed it while Tor is still connected.
    if [ -f "$CONNECTED" ] && [ ! -f "$READY" ]; then
      echo "Tor is still connected: restoring the ready flag"
      touch "$READY"
    fi
    sleep 3
  done
  rm -f "$READY" "$CONNECTED"
  pkill -x tor 2>/dev/null
  wait "$pid" 2>/dev/null
  echo "Tor settings changed (or Tor exited): restarting Tor"
  sleep 2
done
