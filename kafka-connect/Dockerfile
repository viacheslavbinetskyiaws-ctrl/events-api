FROM debezium/connect:3.0.0.Final AS debezium-plugins

FROM quay.io/strimzi/kafka:1.2.0-kafka-4.3.1
USER root:root
COPY --from=debezium-plugins --chown=1001:0 \
    /kafka/connect/debezium-connector-postgres \
    /opt/kafka/plugins/debezium-connector-postgres
USER 1001
