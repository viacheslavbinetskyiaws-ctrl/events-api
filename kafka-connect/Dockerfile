FROM debezium/connect:3.0.0.Final AS debezium-plugins

FROM curlimages/curl:8.11.0 AS bigquery-plugin
RUN mkdir -p /tmp/plugin && curl -sSL \
    https://github.com/Aiven-Open/bigquery-connector-for-apache-kafka/releases/download/v2.15.0/bigquery-connector-for-apache-kafka-2.15.0.tar \
    | tar -x -C /tmp/plugin

FROM quay.io/strimzi/kafka:1.2.0-kafka-4.3.1
USER root:root
COPY --from=debezium-plugins --chown=1001:0 \
    /kafka/connect/debezium-connector-postgres \
    /opt/kafka/plugins/debezium-connector-postgres
COPY --from=bigquery-plugin --chown=1001:0 \
    /tmp/plugin \
    /opt/kafka/plugins/bigquery-sink
USER 1001
