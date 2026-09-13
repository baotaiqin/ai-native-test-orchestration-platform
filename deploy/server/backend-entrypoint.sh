#!/bin/sh
set -eu

umask 027

required_variables="
APP_SECRET_KEY
APP_DATABASE_URL
APP_REDIS_URL
APP_RABBITMQ_URL
APP_MINIO_ACCESS_KEY
APP_MINIO_SECRET_KEY
APP_DEMO_ENABLED
APP_DEMO_PUBLIC_URL
APP_CORS_ORIGINS
APP_DEV_ADMIN_PASSWORD
"

for variable_name in $required_variables; do
    variable_value="$(printenv "$variable_name" 2>/dev/null || true)"
    if [ -z "$variable_value" ]; then
        echo "Required production variable is missing: $variable_name" >&2
        exit 1
    fi
done

if [ "${APP_ENV:-}" != "production" ] || [ "${APP_DEBUG:-}" != "false" ]; then
    echo "Backend container requires APP_ENV=production and APP_DEBUG=false" >&2
    exit 1
fi

if [ "${APP_SECRET_PROVIDER:-}" != "fernet" ]; then
    echo "Backend container requires APP_SECRET_PROVIDER=fernet" >&2
    exit 1
fi

key_file="${APP_SECRET_FERNET_KEY_FILE:-}"
if [ -z "$key_file" ] || [ ! -f "$key_file" ] || [ ! -r "$key_file" ]; then
    echo "Fernet key file is missing or unreadable" >&2
    exit 1
fi

exec "$@"
