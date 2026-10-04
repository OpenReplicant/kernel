"""Paper sources for the research pack: an MCP server over arXiv, OpenAlex and Crossref.

It finds papers and returns each as a record plus the exact `ingest_source` arguments the
research skill asks for. It reads public metadata only and holds no kernel credentials:
the agent decides what to ingest and writes through the kernel gateway.
"""
