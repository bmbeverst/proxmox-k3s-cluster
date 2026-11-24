#!/bin/sh
# Upstream mosquitto image has no env-based auth: build the password file, then exec.
# Credentials arrive as Secret-backed env vars, so this script stays static.
set -eu

: "${MQTT_USER:?MQTT_USER is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD is required}"

# Rewritten each start so a rotated Secret takes effect; broker drops to uid 1883.
mosquitto_passwd -b -c /mosquitto/data/passwd "$MQTT_USER" "$MQTT_PASSWORD"
chown 1883:1883 /mosquitto/data/passwd

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
