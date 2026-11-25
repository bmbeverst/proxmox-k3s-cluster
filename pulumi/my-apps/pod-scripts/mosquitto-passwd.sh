#!/bin/sh
# Init container: build the broker's password file from the mounted Secret, then exit.
# The broker container mounts neither the Secret nor these variables, so the
# plaintext only ever exists in this short-lived container.
set -eu

SECRET_DIR=/run/secrets/mqtt
USERNAME=$(cat "$SECRET_DIR/username")
PASSWORD=$(cat "$SECRET_DIR/password")

# Fail loudly rather than write an empty-credential file.
[ -n "$USERNAME" ]
[ -n "$PASSWORD" ]

# Rewritten each start so a rotated Secret takes effect; broker drops to uid 1883.
# mosquitto_passwd refuses to overwrite, so remove the previous file first.
rm -f /mosquitto/data/passwd
mosquitto_passwd -b -c /mosquitto/data/passwd "$USERNAME" "$PASSWORD"
chown 1883:1883 /mosquitto/data/passwd
