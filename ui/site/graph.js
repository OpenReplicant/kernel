// The explorer's graph view: Cytoscape.js over nodes and edges the page already loaded.
// Node type is shape plus colour (three validated hues and a neutral for events); belief is
// line style plus colour (solid, dashed for contested, dotted for retracted). Colours come
// from the stylesheet's tokens, so light and dark mode follow the page.

window.WmkGraph = (() => {
  let cy = null;
  const SHAPES = { Entity: "round-rectangle", Agent: "ellipse", Claim: "diamond", Event: "hexagon" };

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function style() {
    const fg = token("--fg");
    return [
      {
        selector: "node",
        style: {
          label: "data(label)",
          shape: (n) => SHAPES[n.data("type")] || "ellipse",
          "background-color": (n) => token(`--t-${(n.data("type") || "entity").toLowerCase()}`),
          width: 18,
          height: 18,
          color: fg,
          "font-size": 10,
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-wrap": "ellipsis",
          "text-max-width": 140,
          "text-outline-color": token("--panel"),
          "text-outline-width": 2,
          "min-zoomed-font-size": 7,
          "border-width": 2,
          "border-color": token("--panel"),
        },
      },
      { selector: "node.center", style: { width: 26, height: 26, "border-color": token("--link"), "border-width": 3 } },
      { selector: "node:selected", style: { "border-color": fg, "border-width": 3 } },
      {
        selector: "edge",
        style: {
          width: 1.5,
          "curve-style": "bezier",
          "line-color": token("--edge"),
          "target-arrow-color": token("--edge"),
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.8,
          label: "data(label)",
          "font-size": 8,
          color: token("--muted"),
          "text-rotation": "autorotate",
          "text-outline-color": token("--panel"),
          "text-outline-width": 2,
          "min-zoomed-font-size": 8,
        },
      },
      {
        selector: 'edge[belief = "contested"]',
        style: { "line-color": token("--warn"), "target-arrow-color": token("--warn"), "line-style": "dashed", width: 2 },
      },
      {
        selector: 'edge[belief = "rejected"]',
        style: { "line-color": token("--bad"), "target-arrow-color": token("--bad"), "line-style": "dotted" },
      },
      { selector: "edge:selected", style: { width: 3 } },
    ];
  }

  // Around a node: rings by distance from it. Otherwise a force-directed layout.
  function layout(graph, center) {
    if (graph.nodes.length < 2) return { name: "grid", padding: 24 };
    if (center) {
      return { name: "breadthfirst", roots: [center], circle: true, spacingFactor: 1.1, animate: false, padding: 24 };
    }
    return {
      name: "cose",
      animate: false,
      nodeDimensionsIncludeLabels: true,
      nodeRepulsion: () => 6000,
      idealEdgeLength: () => 60,
      gravity: 1.5,
      numIter: 2500,
      padding: 24,
    };
  }

  // Draw `graph` ({nodes, edges}) into `container`. `onPick(element)` gets {kind: "node"|"edge", id}.
  function draw(container, graph, { center, onPick, onHover }) {
    if (cy) cy.destroy();
    const elements = [
      ...graph.nodes.map((n) => ({
        data: { id: n.id, label: n.name, type: n.type, kind: n.kind },
        classes: n.id === center ? "center" : "",
      })),
      ...graph.edges.map((e) => ({
        data: { id: e.id, source: e.from.id, target: e.to.id, label: e.label, belief: e.belief_status || "unknown" },
      })),
    ];
    cy = cytoscape({
      container,
      elements,
      style: style(),
      minZoom: 0.2,
      maxZoom: 3,
      layout: layout(graph, center),
    });
    cy.on("tap", "node, edge", (evt) => onPick({ kind: evt.target.isNode() ? "node" : "edge", id: evt.target.id() }));
    cy.on("mouseover", "node, edge", (evt) => {
      const p = evt.renderedPosition || evt.target.renderedMidpoint?.() || { x: 0, y: 0 };
      onHover({ kind: evt.target.isNode() ? "node" : "edge", id: evt.target.id(), x: p.x, y: p.y });
    });
    cy.on("mouseout", "node, edge", () => onHover(null));
    if (center && cy.getElementById(center).length) cy.getElementById(center).select();
  }

  function fit() {
    if (cy) cy.fit(undefined, 24);
  }

  function destroy() {
    if (cy) cy.destroy();
    cy = null;
  }

  return { draw, fit, destroy };
})();
