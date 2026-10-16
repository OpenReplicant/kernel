-- Runs once on first database start: identities for agents that work through MCP (harness agents).
-- Each gets its own role, so its reads are filtered by its clearance and its writes are attributed
-- to it. Token: python scripts/mint_jwt.py agent_claude_code   (sent as the bearer to /mcp)
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'agent_claude_code') then
    create role agent_claude_code nologin;
  end if;
end $$;
grant kk_agent to agent_claude_code;
grant agent_claude_code to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:claude-code', 'agent_claude_code', 'software', 'Claude Code (harness agent)')
on conflict (iri) do nothing;
reset role;
