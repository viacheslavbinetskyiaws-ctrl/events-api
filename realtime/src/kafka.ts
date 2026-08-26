import { randomUUID } from "node:crypto";
import { Kafka, type EachMessagePayload } from "kafkajs";

// A fresh, random group ID every startup — deliberately not shared across
// pods. Kafka only splits partitions across members of the SAME group, so
// giving each pod its own unique group means every pod receives every
// message from every partition directly — broadcast, not load-balanced.
// This is what avoids needing Redis pub/sub for cross-pod fan-out: the
// fan-out problem only exists if pods share a group in the first place.
const kafka = new Kafka({
    clientId: "realtime-relay",
    brokers: [process.env.KAFKA_BOOTSTRAP_SERVERS ?? "localhost:29092"]
});

const consumer = kafka.consumer({ groupId: `realtime-relay-${randomUUID()}` });

export interface DebeziumEvent {
    op: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
}

export type ChangeHandler = (table: string, event: DebeziumEvent) => void;

const TOPICS = ["cdc.public.events", "cdc.public.tenant_accounts"];

export async function startConsumer(onChange: ChangeHandler): Promise<void> {
    await consumer.connect();
    // fromBeginning: false — always a brand-new consumer group (random ID
    // above), so there's no committed offset to resume from anyway. This is
    // a live feed, not a durable projection: a newly connected pod should
    // only see events going forward, not replay history — GET /events
    // already answers "what happened before."
    await consumer.subscribe({ topics: TOPICS, fromBeginning: false })

    await consumer.run({
        eachMessage: async ({ topic, message }: EachMessagePayload) => {
            if (message.value === null) {
                // Debezium tombstone following a delete — not a change to react
                // to, the delete itself already arrived as a prior message.
                return;
            }

            const table = topic.split(".").pop();
            if (table === undefined) {
                return;
            }

            const event = JSON.parse(message.value.toString()) as DebeziumEvent;

            if (table === "events") {
                for (const row of [event.before, event.after]) {
                    if (row !== null && typeof row.properties === "string") {
                        row.properties = JSON.parse(row.properties);
                    }
                }
            }

            onChange(table, event);
        },
    });
}
