"""Paper-compiler module (L3): papers as evidence, compiled into agent systems."""
from .spec_view import export_spec, import_spec

VIEW = "agent-spec"


def register(kernel):
    kernel.register_view(VIEW, lambda k, ctx, **kw: export_spec(k, ctx, **kw),
                         lambda k, doc, **kw: import_spec(k, doc, **kw))
