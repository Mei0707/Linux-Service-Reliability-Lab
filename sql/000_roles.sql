-- Fixed credentials belong only to this loopback-bound, disposable lab.
CREATE ROLE lab LOGIN PASSWORD 'lab' NOSUPERUSER;
CREATE ROLE observer LOGIN PASSWORD 'observer' NOSUPERUSER;
GRANT pg_monitor TO observer;
GRANT CONNECT ON DATABASE lab TO lab, observer;
GRANT USAGE, CREATE ON SCHEMA public TO lab;
