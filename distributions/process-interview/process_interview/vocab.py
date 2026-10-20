"""What an interview records: the kinds of thing and the relations between them (one home for both).

Each kind and relation maps to its standard term (W3C ORG, BBO, PROV), used when the maps are exported.
A relation is checked against its domain and range before it is stored, as the SHACL shapes did.
"""
ORG = "http://www.w3.org/ns/org#"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
PROV = "http://www.w3.org/ns/prov#"

KINDS = {
    "organisation": ORG + "FormalOrganization",
    "unit": ORG + "OrganizationalUnit",
    "post": ORG + "Post",
    "person": PROV + "Person",
    "process": BBO + "Process",
    "step": BBO + "Activity",
}

# relation: (domains, ranges, standard term, what it means, for the model)
RELATIONS = {
    "holds": ({"person"}, {"post"}, ORG + "holds", "a person holds a post"),
    "reports_to": ({"post"}, {"post"}, ORG + "reportsTo", "a post reports to another post"),
    "post_in": ({"post"}, {"unit", "organisation"}, ORG + "postIn", "a post is in a team or organisation"),
    "unit_of": ({"unit"}, {"organisation", "unit"}, ORG + "unitOf", "a team is part of an organisation"),
    "step_of": ({"step"}, {"process"}, BBO + "has_container", "a step is part of a process"),
    "next": ({"step"}, {"step"}, BBO + "has_nextFlowNode", "one step comes straight after another"),
    "performed_by": ({"step"}, {"post"}, BBO + "has_resource", "a step is done by a post"),
    "starts_with": ({"process"}, {"step"}, BBO + "has_nextFlowNode", "a process starts with a step"),
    "ends_after": ({"process"}, {"step"}, BBO + "has_nextFlowNode", "a process ends after a step"),
}
