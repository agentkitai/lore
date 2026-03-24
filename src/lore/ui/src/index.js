// Lore Graph Visualization — Main entry point

import { AppState } from './state.js';
import { createSimulation } from './graph/simulation.js';
import { GraphRenderer } from './graph/renderer.js';
import { InteractionManager } from './graph/interaction.js';
import { LayoutManager } from './graph/layout.js';
import { DetailPanel } from './panels/detail.js';
import { FilterPanel } from './panels/filters.js';
import { StatsPanel } from './panels/stats.js';
import { ReviewPanel } from './panels/review.js';
import { SloPanel } from './panels/slo.js';
import { SearchBar } from './components/search.js';
import { TimelineScrubber } from './components/timeline.js';
import { Minimap } from './components/minimap.js';
import { fetchGraph } from './api.js';
import { debounce } from './utils.js';

function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

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
  const showAllToggle = document.getElementById('show-all-toggle');

  // Smart defaults
  const DEFAULT_IMPORTANCE = 0;
  const DEFAULT_SINCE = null;
  const DEFAULT_LIMIT = 100;
  let showAll = false;

  // Loading state
  statusEl.textContent = 'Loading graph data...';

  try {
    // Smart defaults: top 100 most important memories
    const defaultParams = { limit: DEFAULT_LIMIT };
    const data = await fetchGraph(defaultParams);

    if (data.nodes.length === 0) {
      statusEl.textContent = 'Your brain is empty. Run `lore remember` to get started.';
      return;
    }

    const mc = data.nodes.filter(n => n.kind === 'memory').length;
    const ec = data.nodes.filter(n => n.kind === 'entity').length;
    statusEl.textContent = `Showing top ${mc} memories, ${ec} entities, ${data.edges.length} connections (${data.stats.total_memories} total)`;

    // Restore URL state (overrides defaults if present)
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
    renderer.layoutManager = layout;
    interaction.layoutManager = layout;

    // Simulation tick — schedule redraws while sim is active
    sim.on('tick', () => {
      interaction.rebuildQuadtree();
      scheduleRedraw();
    });

    // Event-driven rendering — only run RAF when needed
    let needsRedraw = true;
    let rafId = null;

    const scheduleRedraw = () => {
      needsRedraw = true;
      if (rafId === null) {
        rafId = requestAnimationFrame(animate);
      }
    };

    const animate = () => {
      rafId = null;
      if (needsRedraw || sim.alpha() >= 0.001) {
        renderer.render();
        needsRedraw = false;
      }
      // Keep looping only while simulation is still active
      if (sim.alpha() >= 0.001) {
        rafId = requestAnimationFrame(animate);
      }
    };

    // Kick off initial loop
    scheduleRedraw();

    // Panels
    const detail = new DetailPanel(detailContainer, state, interaction);
    const filters = new FilterPanel(filterContainer, state);
    const search = new SearchBar(searchContainer, state, interaction);
    const stats = new StatsPanel(statsContainer, state, interaction);
    const timeline = new TimelineScrubber(timelineContainer, state);
    const minimap = new Minimap(minimapContainer, state, renderer);

    // Review panel (E6)
    const reviewContainer = document.getElementById('review-panel');
    if (reviewContainer) {
      const reviewPanel = new ReviewPanel(reviewContainer, state);
    }

    // SLO panel (F3)
    const sloContainer = document.getElementById('slo-panel');
    let sloPanel = null;
    if (sloContainer) {
      sloPanel = new SloPanel(sloContainer, state);
    }

    // Render minimap periodically
    setInterval(() => minimap.render(), 500);

    // Stats button
    if (statsBtn) {
      statsBtn.onclick = () => stats.toggle();
    }

    // SLO button
    const sloBtn = document.getElementById('slo-btn');
    if (sloBtn && sloPanel) {
      sloBtn.onclick = () => sloPanel.toggle();
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

    // Show All toggle — reload with no date/importance filters
    async function reloadGraph(params) {
      statusEl.textContent = 'Reloading...';
      try {
        const newData = await fetchGraph(params);
        state.setGraphData(newData.nodes, newData.edges, newData.stats);
        sim.nodes(newData.nodes);
        sim.force('link').links(newData.edges);
        sim.alpha(0.5).restart();
        const mc = newData.nodes.filter(n => n.kind === 'memory').length;
        const ec = newData.nodes.filter(n => n.kind === 'entity').length;
        statusEl.textContent = mc + ' memories, ' + ec + ' entities, ' + newData.edges.length + ' edges';
      } catch (err) {
        statusEl.textContent = 'Reload failed: ' + err.message;
      }
    }

    if (showAllToggle) {
      showAllToggle.onclick = () => {
        showAll = !showAll;
        showAllToggle.classList.toggle('active', showAll);
        showAllToggle.textContent = showAll ? 'Top 100' : 'Show All';
        if (showAll) {
          reloadGraph({ limit: 5000 });
        } else {
          reloadGraph(defaultParams);
        }
        state.dispatchEvent(new CustomEvent('filterChange'));
      };
    }

    // Default to force layout
    layout.switchMode('force');
    for (const b of viewBtns) {
      b.classList.toggle('active', b.dataset.mode === 'force');
    }

    // Back to clusters button (shown when a cluster is expanded)
    const backBtn = document.getElementById('back-to-clusters');
    if (backBtn) {
      backBtn.onclick = () => {
        layout.collapseBack();
        backBtn.style.display = 'none';
      };
      // Show back button when a cluster is expanded
      state.addEventListener('viewModeChange', () => {
        if (layout.getExpandedCluster()) {
          backBtn.style.display = 'block';
        } else if (layout.isCollapsed()) {
          backBtn.style.display = 'none';
        }
      });
      // Also show when layout expands a cluster via click
      const origExpand = layout.expandCluster.bind(layout);
      layout.expandCluster = (label) => {
        origExpand(label);
        backBtn.style.display = 'block';
      };
    }

    // Responsive resize
    const handleResize = debounce(() => {
      const w = canvas.parentElement.clientWidth;
      const h = canvas.parentElement.clientHeight;
      renderer.resize(w, h);
      sim.force('center').x(w / 2).y(h / 2);
      sim.alpha(0.1).restart();
      scheduleRedraw();
    }, 200);
    window.addEventListener('resize', handleResize);

    // Filter/search changes trigger redraw
    state.addEventListener('filterChange', () => {
      scheduleRedraw();
    });
    state.addEventListener('searchChange', () => {
      scheduleRedraw();
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
