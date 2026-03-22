// SLO Dashboard Panel — status cards, latency chart, hit-rate chart, alert timeline

async function _fetch(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error('API error: ' + res.status);
  return res.json();
}

export async function fetchSloDashboard() {
  return _fetch('/v1/slo/status');
}

export async function fetchSloAlerts(limit = 50) {
  return _fetch('/v1/slo/alerts?limit=' + limit);
}

export async function fetchSloTimeseries(metric = 'p99_latency', hours = 24) {
  return _fetch('/v1/slo/timeseries?metric=' + metric + '&window_hours=' + hours);
}

export class SloPanel {
  constructor(container, state) {
    this.container = container;
    this.state = state;
    this.visible = false;
    this._render();
  }

  toggle() {
    this.visible = !this.visible;
    if (this.visible) {
      this.container.classList.add('open');
      this.refresh();
    } else {
      this.container.classList.remove('open');
    }
  }

  async refresh() {
    try {
      const [status, alerts] = await Promise.all([
        fetchSloDashboard(),
        fetchSloAlerts(20),
      ]);
      this._renderStatus(status);
      this._renderAlerts(alerts);
    } catch (err) {
      const errorDiv = this.container.querySelector('.slo-panel');
      if (errorDiv) errorDiv.textContent = 'Failed to load SLO data: ' + err.message;
    }
  }

  _render() {
    // Build DOM safely
    const panel = document.createElement('div');
    panel.className = 'slo-panel';

    const h3 = document.createElement('h3');
    h3.textContent = 'SLO Dashboard';
    panel.appendChild(h3);

    const cards = document.createElement('div');
    cards.id = 'slo-status-cards';
    cards.className = 'slo-cards';
    panel.appendChild(cards);

    const chart = document.createElement('div');
    chart.id = 'slo-chart';
    chart.className = 'slo-chart';
    panel.appendChild(chart);

    const h4 = document.createElement('h4');
    h4.textContent = 'Recent Alerts';
    panel.appendChild(h4);

    const alertsDiv = document.createElement('div');
    alertsDiv.id = 'slo-alerts';
    alertsDiv.className = 'slo-alerts';
    panel.appendChild(alertsDiv);

    this.container.replaceChildren(panel);
  }

  _renderStatus(slos) {
    const cardsEl = this.container.querySelector('#slo-status-cards');
    cardsEl.replaceChildren();

    if (!slos || slos.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'slo-empty';
      empty.textContent = 'No SLOs configured';
      cardsEl.appendChild(empty);
      return;
    }

    for (const slo of slos) {
      const card = document.createElement('div');
      card.className = 'slo-card ' + (slo.passing ? 'slo-pass' : 'slo-fail');

      const icon = document.createElement('span');
      icon.className = 'slo-icon';
      icon.textContent = slo.passing ? '\u2713' : '\u2717';
      card.appendChild(icon);

      const info = document.createElement('div');
      info.className = 'slo-info';

      const name = document.createElement('div');
      name.className = 'slo-name';
      name.textContent = slo.name;
      info.appendChild(name);

      const metric = document.createElement('div');
      metric.className = 'slo-metric';
      const value = slo.current_value != null ? slo.current_value.toFixed(2) : 'N/A';
      metric.textContent = slo.metric + ': ' + value + ' (' + slo.operator + ' ' + slo.threshold + ')';
      info.appendChild(metric);

      card.appendChild(info);
      cardsEl.appendChild(card);
    }
  }

  _renderAlerts(alerts) {
    const alertsEl = this.container.querySelector('#slo-alerts');
    alertsEl.replaceChildren();

    if (!alerts || alerts.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'slo-empty';
      empty.textContent = 'No recent alerts';
      alertsEl.appendChild(empty);
      return;
    }

    for (const a of alerts) {
      const row = document.createElement('div');
      row.className = 'slo-alert ' + (a.status === 'firing' ? 'alert-firing' : 'alert-resolved');

      const status = document.createElement('span');
      status.className = 'alert-status';
      status.textContent = a.status;
      row.appendChild(status);

      const val = document.createElement('span');
      val.className = 'alert-value';
      val.textContent = a.metric_value.toFixed(2) + ' / ' + a.threshold.toFixed(2);
      row.appendChild(val);

      const time = document.createElement('span');
      time.className = 'alert-time';
      time.textContent = a.created_at ? new Date(a.created_at).toLocaleString() : 'unknown';
      row.appendChild(time);

      alertsEl.appendChild(row);
    }
  }
}
