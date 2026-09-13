#!/bin/sh
set -eu

umask 027

usage() {
    cat >&2 <<'EOF'
Usage:
  sudo bash ./deploy-release.sh first-install
  sudo bash ./deploy-release.sh upgrade --backup-confirmed

Optional environment variables:
  AI_TEST_ENV_FILE       Shared production env file (default: /opt/ai-test/shared/production.env)
  AI_TEST_CURRENT_LINK   Current release symlink (default: /opt/ai-test/current)
EOF
    exit 2
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command is unavailable: $1"
}

read_env_value() {
    key="$1"
    file="$2"
    awk -v wanted="$key" '
        index($0, wanted "=") == 1 {
            value = substr($0, length(wanted) + 2)
            gsub(/\r$/, "", value)
            print value
            found = 1
            exit
        }
        END { if (!found) exit 1 }
    ' "$file"
}

[ "$#" -ge 1 ] || usage
mode="$1"
shift

case "$mode" in
    first-install)
        [ "$#" -eq 0 ] || usage
        ;;
    upgrade)
        [ "$#" -eq 1 ] || usage
        [ "$1" = "--backup-confirmed" ] || fail "Upgrade requires --backup-confirmed after you have verified the backup/snapshot."
        ;;
    *)
        usage
        ;;
esac

[ "$(id -u)" -eq 0 ] || fail 'Run this script with sudo.'

need_command docker
need_command sha256sum
need_command awk

docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is unavailable.'

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
compose_file="$script_dir/compose.release.yml"
release_env="$script_dir/release.env"
checksum_file="$script_dir/SHA256SUMS"
image_archive="$script_dir/app-images.tar"
env_file=${AI_TEST_ENV_FILE:-/opt/ai-test/shared/production.env}
current_link=${AI_TEST_CURRENT_LINK:-/opt/ai-test/current}

for required_file in "$compose_file" "$release_env" "$checksum_file" "$image_archive" "$script_dir/prometheus.server.yml"; do
    [ -f "$required_file" ] || fail "Release file is missing: $required_file"
done

echo '[1/7] Verifying release checksums'
(
    cd "$script_dir"
    sha256sum -c SHA256SUMS
)

if [ ! -f "$env_file" ]; then
    env_directory=$(dirname -- "$env_file")
    mkdir -p "$env_directory"
    cp "$script_dir/.env.server.example" "$env_file"
    chmod 600 "$env_file"
    fail "Created $env_file. Fill every blank production value, create the Fernet key file, then rerun this command."
fi

[ -r "$env_file" ] || fail "Production env file is not readable: $env_file"

backend_image=$(read_env_value BACKEND_IMAGE "$release_env") || fail 'BACKEND_IMAGE is missing from release.env.'
frontend_image=$(read_env_value FRONTEND_IMAGE "$release_env") || fail 'FRONTEND_IMAGE is missing from release.env.'
demo_image=$(read_env_value DEMO_IMAGE "$release_env") || fail 'DEMO_IMAGE is missing from release.env.'
release_tag=$(read_env_value RELEASE_TAG "$release_env") || fail 'RELEASE_TAG is missing from release.env.'
[ -n "$backend_image" ] || fail 'BACKEND_IMAGE is blank in release.env.'
[ -n "$frontend_image" ] || fail 'FRONTEND_IMAGE is blank in release.env.'
[ -n "$demo_image" ] || fail 'DEMO_IMAGE is blank in release.env.'
[ -n "$release_tag" ] || fail 'RELEASE_TAG is blank in release.env.'

compose() {
    docker compose \
        --project-directory "$script_dir" \
        --env-file "$env_file" \
        --env-file "$release_env" \
        --file "$compose_file" \
        "$@"
}

echo '[2/7] Validating production configuration'
compose config --quiet

database_url=$(read_env_value APP_DATABASE_URL "$env_file") || fail 'APP_DATABASE_URL is missing from the production env file.'
case "$database_url" in
    mysql+pymysql://*) ;;
    *) fail 'APP_DATABASE_URL must use the installed mysql+pymysql:// driver.' ;;
esac

fernet_key_path=$(read_env_value APP_SECRET_FERNET_KEY_FILE_HOST "$env_file") || fail 'APP_SECRET_FERNET_KEY_FILE_HOST is missing from the production env file.'
[ -n "$fernet_key_path" ] || fail 'APP_SECRET_FERNET_KEY_FILE_HOST is blank in the production env file.'
[ -f "$fernet_key_path" ] || fail "Fernet key file does not exist: $fernet_key_path"
[ -r "$fernet_key_path" ] || fail "Fernet key file is not readable: $fernet_key_path"

echo '[3/7] Loading application images'
docker image load --input "$image_archive"

docker_architecture=$(docker info --format '{{.Architecture}}')
case "$docker_architecture" in
    x86_64) docker_architecture=amd64 ;;
    aarch64) docker_architecture=arm64 ;;
esac

for image_name in "$backend_image" "$frontend_image" "$demo_image"; do
    image_architecture=$(docker image inspect --format '{{.Architecture}}' "$image_name") || fail "Loaded image is unavailable: $image_name"
    [ "$image_architecture" = "$docker_architecture" ] || fail "Image architecture mismatch for $image_name: image=$image_architecture, Docker=$docker_architecture"
done

if [ "$mode" = 'upgrade' ]; then
    echo '[4/7] Entering application maintenance window'
    compose stop frontend backend prometheus
else
    echo '[4/7] Starting stateful dependencies'
    compose up -d --wait --wait-timeout 300 mysql redis rabbitmq minio
fi

echo '[5/7] Applying database migrations'
compose run --rm backend alembic heads
compose run --rm backend alembic upgrade head
compose run --rm backend alembic current

if [ "$mode" = 'first-install' ]; then
    echo '[6/7] Creating the initial administrator if absent'
    compose run --rm backend python -m app.modules.auth.bootstrap --confirm-bootstrap
else
    echo '[6/7] Upgrade mode: preserving the existing administrator and data'
fi

echo '[7/7] Starting and checking the complete platform'
compose up -d --wait --wait-timeout 300
compose ps

compose exec -T frontend wget -q -O /dev/null http://127.0.0.1:8080/healthz || fail 'Frontend health check failed inside the container.'
compose exec -T demo python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).read()" || fail 'Demo health check failed inside the container.'

current_parent=$(dirname -- "$current_link")
mkdir -p "$current_parent"
if [ -e "$current_link" ] && [ ! -L "$current_link" ]; then
    fail "Current release path exists but is not a symlink: $current_link"
fi
ln -sfn "$script_dir" "$current_link"

echo "Deployment completed: $release_tag"
echo "Current release: $current_link -> $script_dir"
demo_public_url=$(read_env_value DEMO_PUBLIC_URL "$env_file") || fail 'DEMO_PUBLIC_URL is missing from the production env file.'
echo "Customer Demo: $demo_public_url"
echo 'Next: verify browser login, Runner connectivity, one real test execution, Evidence, report, and configured AI Provider.'
