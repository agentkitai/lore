// Color palette from PRD Section 5

export const MEMORY_COLORS = {
  general: '#6b8afd',
  code: '#4ade80',
  lesson: '#fbbf24',
  fact: '#22d3ee',
  convention: '#a78bfa',
  preference: '#f472b6',
  debug: '#fb7185',
  pattern: '#2dd4bf',
  note: '#94a3b8',
};

export const ENTITY_COLORS = {
  person: '#fcd34d',
  tool: '#60a5fa',
  project: '#34d399',
  concept: '#c084fc',
  organization: '#fb923c',
  platform: '#818cf8',
  language: '#67e8f9',
  framework: '#fda4af',
  service: '#6ee7b7',
  other: '#9ca3af',
};

const FALLBACK_COLOR = '#9ca3af';

export function getNodeColor(node) {
  if (node.kind === 'memory') {
    return MEMORY_COLORS[node.type] || FALLBACK_COLOR;
  }
  if (node.kind === 'entity') {
    return ENTITY_COLORS[node.type] || FALLBACK_COLOR;
  }
  return FALLBACK_COLOR;
}
