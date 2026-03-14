// Lore Graph Visualization — Main entry point

import { AppState } from './state.js';
import { createSimulation } from './graph/simulation.js';
import { GraphRenderer } from './graph/renderer.js';
import { InteractionManager } from './graph/interaction.js';
import { LayoutManager } from './graph/layout.js';
import { DetailPanel } from './panels/detail.js';
import { FilterPanel } from './panels/filters.js';
import { StatsPanel } from './panels/stats.js';
import { SearchBar } from './components/search.js';
import { TimelineScrubber } from './components/timeline.js';
import { Minimap } from './components/minimap.js';
import { fetchGraph } from './api.js';
import { debounce } from './utils.js';

async function init() {
  const state = new AppState();

  // DOM elements
  const canvas = document.getElementById('graph-canvas');
  const filterContainer = document.getElementById('filter-panel');
  const detailContainer = document.getElementById('detail-panel');
  const searchContainer = document.getElementById('search-container');
  const statsContainer = document.getElementById('stats-panel');
  const timelineContainer = document.getElementById('timeline-panel');
  const minimapContainer = document.getElementById('minimap');
  const statusEl = document.getElementById('status');
  const statsBtn = document.getElementById('stats-btn');
  const viewBtns = document.querySelectorAll('.view-btn');
  const contrastToggle = document.getElementById('contrast-toggle');

  // Loading state
  statusEl.textContent = 'Loading graph data...';

  try {
    const data = await fetchGraph();

    if (data.nodes.length === 0) {
      statusEl.textContent = 'Your brain is empty. Run `lore remember` to get started.';
      return;
    }

    statusEl.textContent = '';

    // Restore URL state
    state.restoreFromUrl();
    state.setGraphData(data.nodes, data.edges, data.stats);

    // Setup simulation
    const w = canvas.parentElement.clientWidth;
    const h = canvas.parentElement.clientHeight;
    const sim = createSimulation(data.nodes, data.edges, w, h);

    // Setup renderer
    const renderer = new GraphRenderer(canvas, state);
    renderer.resize(w, h);

    // Setup interactions
    const interaction = new InteractionManager(canvas, state, renderer, sim);

    // Layout manager
    const layout = new LayoutManager(state, sim, renderer);

    // Simulation tick
    sim.on('tick', () => {
      interaction.rebuildQuadtree();
      renderer.render();
    });

    // Start rendering loop (for search pulse animation etc.)
    let animating = true;
    const animate = () => {
      if (!animating) return;
      if (sim.alpha() < 0.001) {
        // Only re-render on state changes when sim is cooled
        renderer.render();
      }
      requestAnimationFrame(animate);
    };
    requestAnimationFrame(animate);

    // Panels
    const detail = new DetailPanel(detailContainer, state, interaction);
    const filters = new FilterPanel(filterContainer, state);
    const search = new SearchBar(searchContainer, state, interaction);
    const stats = new StatsPanel(statsContainer, state, interaction);
    const timeline = new TimelineScrubber(timelineContainer, state);
    const minimap = new Minimap(minimapContainer, state, renderer);

    // Render minimap periodically
    setInterval(() => minimap.render(), 500);

    // Stats button
    if (statsBtn) {
      statsBtn.onclick = () => stats.toggle();
    }

    // View mode buttons
    for (const btn of viewBtns) {
      btn.addEventListener('click', () => {
        for (const b of viewBtns) b.classList.remove('active');
        btn.classList.add('active');
        layout.switchMode(btn.dataset.mode);
      });
    }

    // High contrast toggle
    if (contrastToggle) {
      contrastToggle.onclick = () => {
        document.body.classList.toggle('high-contrast');
      };
    }

    // Responsive resize
    const handleResize = debounce(() => {
      const w = canvas.parentElement.clientWidth;
      const h = canvas.parentElement.clientHeight;
      renderer.resize(w, h);
      sim.force('center').x(w / 2).y(h / 2);
      sim.alpha(0.1).restart();
    }, 200);
    window.addEventListener('resize', handleResize);

    // Filter change re-renders
    state.addEventListener('filterChange', () => {
      renderer.render();
    });
    state.addEventListener('searchChange', () => {
      renderer.render();
    });

    // Update status bar
    const nodeCount = data.nodes.length;
    const edgeCount = data.edges.length;
    const memCount = data.nodes.filter(n => n.kind === 'memory').length;
    const entCount = data.nodes.filter(n => n.kind === 'entity').length;
    statusEl.textContent = memCount + ' memories, ' + entCount + ' entities, ' + edgeCount + ' edges';

  } catch (err) {
    statusEl.textContent = 'Failed to load graph: ' + err.message;
    console.error('Graph load error:', err);
  }
}

// Wait for DOM
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
