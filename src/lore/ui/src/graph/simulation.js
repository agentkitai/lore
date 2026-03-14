// D3 Force simulation setup

import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force';

export function createSimulation(nodes, edges, width, height) {
  const sim = forceSimulation(nodes)
    .force('link', forceLink(edges)
      .id(d => d.id)
      .distance(d => 60 + (1 - (d.weight || 0.5)) * 80)
      .strength(d => 0.3 + (d.weight || 0.5) * 0.4)
    )
    .force('charge', forceManyBody()
      .strength(-30)
      .distanceMax(400)
    )
    .force('center', forceCenter(width / 2, height / 2))
    .force('collide', forceCollide()
      .radius(d => getNodeRadius(d) + 4)
      .strength(0.7)
    )
    .alphaDecay(0.02)
    .velocityDecay(0.4);

  return sim;
}

export function getNodeRadius(node) {
  if (node.kind === 'memory') {
    const imp = node.importance || 0.5;
    return 8 + imp * 16; // 8-24px
  }
  if (node.kind === 'entity') {
    const mc = node.mention_count || 1;
    return 6 + Math.min(mc / 50, 1) * 24; // 6-30px
  }
  return 8;
}
