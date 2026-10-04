"""The software pack's adapter: maps a repository into the World Model Kernel.

It reads the files that define how the repository is built and run (compose files,
Dockerfiles, `pyproject.toml`, CI workflows) and its git history, and turns each into a
kernel source plus observed claims with the operations they justify. The plan is
independent of any database (`plan.Plan`); `apply` plays it through the kernel's gateway
as an MCP client, so every write goes through `kernel.write` and the adapter holds no
database credentials.
"""
