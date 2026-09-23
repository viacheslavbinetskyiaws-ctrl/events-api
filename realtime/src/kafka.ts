import { randomUUID } from "node:crypto";
// kafkajs is CommonJS; Node's static cjs-module-lexer detects `Kafka` as a
// named export but not `KafkaJSProtocolError` (crashed every pod on import
// with "SyntaxError: Named export 'KafkaJSProtocolError' not found" —
// tsc/tsdown both stayed clean since neither actually executes the compiled
// output, only Node itself catches this). Importing the whole module as the
// default and destructuring at runtime sidesteps that static analysis
// entirely, exactly as Node's own error message suggests.
import kafkajsPkg, { type EachMessagePayload } from "kafkajs";

const { Kafka, KafkaJSProtocolError } = kafkajsPkg;

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

// On a genuinely fresh database, Debezium's initial snapshot produces
// nothing (no pre-existing rows to snapshot), so these topics don't exist
// yet — Kafka only creates a topic once something actually produces to it,
// which only happens once the seed/bootstrap sequence writes the first row.
// kafkajs surfaces subscribing to a not-yet-created topic as a
// KafkaJSProtocolError (type UNKNOWN_TOPIC_OR_PARTITION) rather than
// retrying it internally, so this loop absorbs exactly that one error until
// the topic exists. Observed live on a real from-zero `cluster-up`: this
// resolved in under 2 minutes (4 kubelet restarts before this fix existed);
// 10 minutes of headroom at 5s intervals is a generous multiple of that.
// Any other error (bad broker address, auth failure, ...) still propagates
// immediately — this is not a blanket retry-anything.
async function subscribeWhenTopicsExist(topics: string[]): Promise<void> {
    const maxAttempts = 120;
    const delayMs = 5000;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            await consumer.subscribe({ topics, fromBeginning: false });
            return;
        } catch (err) {
            const topicNotCreatedYet = err instanceof KafkaJSProtocolError && err.type === "UNKNOWN_TOPIC_OR_PARTITION";
            if (!topicNotCreatedYet || attempt === maxAttempts) {
                throw err;
            }
            console.warn(
                `topics not created yet (attempt ${attempt}/${maxAttempts}) — Debezium hasn't produced to ` +
                `${topics.join(", ")} on this database yet, retrying in ${delayMs}ms`,
            );
            await new Promise((resolve) => setTimeout(resolve, delayMs));
        }
    }
}

export async function startConsumer(onChange: ChangeHandler): Promise<void> {
    await consumer.connect();
    // fromBeginning: false — always a brand-new consumer group (random ID
    // above), so there's no committed offset to resume from anyway. This is
    // a live feed, not a durable projection: a newly connected pod should
    // only see events going forward, not replay history — GET /events
    // already answers "what happened before."
    await subscribeWhenTopicsExist(TOPICS);

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
