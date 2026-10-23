#!/bin/sh
set -eu
if [ "$NGINX_KEEPALIVE" -gt 0 ]; then
    export LAB_KEEPALIVE="keepalive $NGINX_KEEPALIVE;"
    export LAB_CONNECTION=''
else
    export LAB_KEEPALIVE=''
    export LAB_CONNECTION='close'
fi
envsubst '${NGINX_CONNECTIONS} ${NGINX_PROXY_TIMEOUT} ${LAB_KEEPALIVE} ${LAB_CONNECTION}' \
    < /etc/nginx/lab.conf.template > /etc/nginx/nginx.conf
