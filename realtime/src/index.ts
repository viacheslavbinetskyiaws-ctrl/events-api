import express, { type Request, type Response } from "express";
import { startConsumer, type DebeziumEvent} from "./kafka";

const app = express();
const port = Number(process.env.PORT ?? 3000);

// In-memory only, per pod — no database, no durable state. If this pod
// restarts, a reconnecting client just picks up new events going forward;
// there was never anything durable to lose.
const clientByTenant = new Map<string, Set<Response>>();

function tenantIdFor(table: string, event: DebeziumEvent): string | undefined {
    const row = event.after ?? event.before;
    if (row === null || row === undefined) {
        return undefined;
    }

    // events.tenant_id is the direct FK; tenant_accounts.id IS the tenant
    // id (see TenantAccountORM's own docstring — its primary key is the
    // canonical tenant identifier used everywhere else in this project).
    const key = table === "tenant_accounts" ? "id" : "tenant_id";
    const value = row[key];
    return typeof value === "string" ? value : undefined;
}

function broadcast(table: string, event: DebeziumEvent): void {
    const tenantId = tenantIdFor(table, event);
    if (tenantId === undefined) {
        return;
    }

    const clients = clientByTenant.get(tenantId);
    if (clients === undefined) {
        return;
    }

    const payload = JSON.stringify({ table, op: event.op, after: event.after});
    for (const res of clients) {
        res.write(`data: ${payload}\n\n`);
    }
}

app.get("/healthz", (_req: Request, res: Response) => {
    res.json({ status: "ok" });
});

app.get("/stream/events", (req: Request, res: Response) => {
    const tenantId = req.header("X-Tenant-ID");
    if (tenantId === undefined) {
        res.status(400).json({ error: { code: "missing_tenant", message: "X-Tenant-ID header is required" } });
        return;
    }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    let clients = clientByTenant.get(tenantId);
    if (clients === undefined) {
        clients = new Set();
        clientByTenant.set(tenantId, clients);
    }
    clients.add(res);

    req.on("close", () => {
        clients?.delete(res);
        if (clients?.size === 0) {
            clientByTenant.delete(tenantId);
        }
    });
});

async function main(): Promise<void> {
    await startConsumer(broadcast);
    app.listen(port, () => {
        console.log(`realtime relay listening on ${port}`);
    });
}

main().catch((err) => {
    console.error("fatal startup error", err);
    process.exit(1);
});
