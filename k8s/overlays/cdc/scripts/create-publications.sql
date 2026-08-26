DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'dbz_tenant_accounts_publication') THEN
        CREATE PUBLICATION dbz_tenant_accounts_publication FOR TABLE public.tenant_accounts;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'dbz_events_publication') THEN
        CREATE PUBLICATION dbz_events_publication FOR TABLE public.events;
    END IF;
END
$$;
