FROM alpine:3.21
RUN apk add --no-cache pgbouncer postgresql-client \
    && mkdir -p /etc/pgbouncer \
    && chown -R nobody:nogroup /etc/pgbouncer
COPY pgbouncer.ini userlist.txt /etc/pgbouncer/
USER nobody
EXPOSE 6432
CMD ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]
