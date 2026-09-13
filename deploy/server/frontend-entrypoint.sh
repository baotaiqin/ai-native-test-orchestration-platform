#!/bin/sh
set -eu

umask 027

mkdir -p \
  /tmp/nginx/runtime \
  /tmp/nginx/client_temp \
  /tmp/nginx/proxy_temp \
  /tmp/nginx/fastcgi_temp \
  /tmp/nginx/uwsgi_temp \
  /tmp/nginx/scgi_temp

test -r /usr/share/nginx/html/index.html
test -r /etc/nginx/nginx.conf

public_demo_username=${PUBLIC_DEMO_USERNAME:-}
public_demo_password=${PUBLIC_DEMO_PASSWORD:-}

if { [ -n "$public_demo_username" ] && [ -z "$public_demo_password" ]; } \
  || { [ -z "$public_demo_username" ] && [ -n "$public_demo_password" ]; }; then
  echo 'PUBLIC_DEMO_USERNAME and PUBLIC_DEMO_PASSWORD must both be set or both be blank.' >&2
  exit 1
fi

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

escaped_demo_username=$(json_escape "$public_demo_username")
escaped_demo_password=$(json_escape "$public_demo_password")
printf 'window.__AI_TEST_RUNTIME_CONFIG__ = Object.freeze({"publicDemoUsername":"%s","publicDemoPassword":"%s"});\n' \
  "$escaped_demo_username" \
  "$escaped_demo_password" \
  > /tmp/nginx/runtime/runtime-config.js
chmod 0440 /tmp/nginx/runtime/runtime-config.js

exec nginx -c /etc/nginx/nginx.conf -g 'daemon off;'
