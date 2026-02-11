/**
 * Esprit Dashboard — WebSocket client and rendering logic.
 * Uses safe DOM methods (textContent, createElement) — no innerHTML.
 */

class EspritDashboard {
  constructor() {
    this.agents = [];
    this.tools = [];
    this.chatMessages = [];
    this.vulnerabilities = [];
    this.streaming = {};
    this.stats = {};
    this.selectedAgentId = null;
    this.screenshotAgents = new Set();
    this.scanConfig = null;
    this.finalReport = null;
    this.activeTab = 'terminal';
    this._terminalRenderedCount = 0;
    this._toolsRenderedCount = 0;

    this._initTabs();
    this._initBrowserZoom();
    this._showGhostLoader(true);
    this.connect();
  }

  // ------- WebSocket -------

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(proto + '://' + location.host + '/ws');
    this.ws.onopen = () => {
      this._setStatus('connected', 'running');
    };
    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        this._handleMessage(msg);
      } catch (err) {
        console.warn('Failed to parse WS message', err);
      }
    };
    this.ws.onclose = () => {
      this._setStatus('reconnecting...', 'disconnected');
      setTimeout(() => this.connect(), 2000);
    };
    this.ws.onerror = (err) => {
      console.warn('WebSocket error', err);
    };
  }

  _handleMessage(msg) {
    switch (msg.type) {
      case 'full_state':
        this.agents = msg.agents || [];
        this.tools = msg.tools || [];
        this.chatMessages = msg.chat || [];
        this.vulnerabilities = msg.vulnerabilities || [];
        this.streaming = msg.streaming || {};
        this.stats = msg.stats || {};
        this.screenshotAgents = new Set(msg.screenshot_agents || []);
        this.scanConfig = msg.scan_config || null;
        this.finalReport = msg.final_report || null;
        this._renderAll();
        break;
      case 'delta_batch':
        (msg.deltas || []).forEach(d => this._applyDelta(d));
        break;
      case 'heartbeat':
        break;
    }
  }

  _applyDelta(delta) {
    switch (delta.type) {
      case 'agents_update':
        this.agents = delta.agents || [];
        this._renderAgents();
        this._renderTerminal();
        break;
      case 'tools_update':
        this.tools = this.tools.concat(delta.tools || []);
        this._renderTerminal();
        this._renderToolsTimeline();
        break;
      case 'chat_update':
        this.chatMessages = this.chatMessages.concat(delta.messages || []);
        this._renderTerminal();
        break;
      case 'vulnerability_update':
        this.vulnerabilities = this.vulnerabilities.concat(delta.vulnerabilities || []);
        this._renderVulns();
        this._renderTerminal();
        break;
      case 'streaming_update':
        this.streaming = delta.streaming || {};
        this._terminalDirty = true;
        this._renderTerminal();
        break;
      case 'screenshot_update':
        this.screenshotAgents.add(delta.agent_id);
        document.getElementById('browser-viewer').classList.add('visible');
        if (!this.selectedAgentId || this.selectedAgentId === delta.agent_id) {
          this._fetchScreenshot(delta.agent_id);
        }
        this._renderAgents();
        this._renderBrowserTabs();
        break;
      case 'stats_update':
        this.stats = delta.stats || {};
        this._renderStats();
        break;
      case 'scan_config_update':
        this.scanConfig = delta.scan_config || null;
        this._renderScanConfig();
        break;
      case 'scan_complete':
        this.finalReport = delta.final_report || null;
        this._renderReport();
        this._setStatus('completed', 'completed');
        break;
    }
  }

  // ------- DOM helpers (safe, no innerHTML) -------

  _el(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === 'className') el.className = v;
        else if (k === 'textContent') el.textContent = v;
        else if (k === 'title') el.title = v;
        else if (k === 'onclick') el.addEventListener('click', v);
        else if (k.startsWith('data-')) el.setAttribute(k, v);
        else if (k === 'style' && typeof v === 'string') el.setAttribute('style', v);
        else if (k === 'href') el.setAttribute('href', v);
        else if (k === 'target') el.setAttribute('target', v);
        else if (k === 'rel') el.setAttribute('rel', v);
        else el.setAttribute(k, v);
      }
    }
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(c => {
        if (typeof c === 'string') el.appendChild(document.createTextNode(c));
        else if (c) el.appendChild(c);
      });
    }
    return el;
  }

  _clearEl(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  _icon(name, cls) {
    const i = document.createElement('i');
    i.setAttribute('data-lucide', name);
    if (cls) i.className = cls;
    return i;
  }

  _refreshIcons() {
    if (typeof lucide !== 'undefined' && lucide.createIcons) {
      lucide.createIcons();
    }
  }

  // ------- Tabs -------

  _initTabs() {
    document.querySelectorAll('.center-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        this._switchTab(tab);
      });
    });
  }

  _switchTab(tab) {
    this.activeTab = tab;
    document.querySelectorAll('.center-tab').forEach(b => {
      b.classList.toggle('active', b.getAttribute('data-tab') === tab);
    });
    document.querySelectorAll('.center-tab-content').forEach(p => {
      p.classList.toggle('active', p.getAttribute('data-tab') === tab);
    });
    if (tab === 'terminal') this._renderTerminal();
    if (tab === 'tools') this._renderToolsTimeline();
    if (tab === 'report') this._renderReport();
  }

  // ------- Browser zoom -------

  _initBrowserZoom() {
    const container = document.getElementById('browser-container');
    if (container) {
      container.addEventListener('click', () => {
        const img = document.getElementById('browser-img');
        if (img && img.style.display !== 'none' && img.src) {
          const modal = document.getElementById('screenshot-modal');
          const modalImg = document.getElementById('screenshot-modal-img');
          modalImg.src = img.src;
          modal.style.display = 'flex';
          this._refreshIcons();
        }
      });
    }
  }

  // ------- Ghost loader -------

  _showGhostLoader(show) {
    const ghost = document.getElementById('ghost-loader');
    const main = document.getElementById('main');
    if (show) {
      ghost.classList.add('visible');
      main.style.display = 'none';
    } else {
      ghost.classList.remove('visible');
      main.style.display = 'flex';
    }
  }

  // ------- Rendering -------

  _renderAll() {
    this._showGhostLoader(false);
    this._renderAgents();
    this._renderTerminal();
    this._renderToolsTimeline();
    this._renderVulns();
    this._renderStats();
    this._renderScanConfig();
    this._renderBrowserTabs();
    this._renderStatusBar();

    if (this.finalReport) {
      this._renderReport();
    }

    if (!this.selectedAgentId && this.screenshotAgents.size > 0) {
      this._selectAgent([...this.screenshotAgents][0]);
    } else if (!this.selectedAgentId && this.agents.length > 0) {
      this._selectAgent(this.agents[0].id);
    }

    if (this.screenshotAgents.size > 0) {
      document.getElementById('browser-viewer').classList.add('visible');
    }

    this._refreshIcons();
  }

  // ------- Agents Panel -------

  _renderAgents() {
    const container = document.getElementById('agents-tree');
    this._clearEl(container);

    const roots = [];
    const byId = {};
    this.agents.forEach(a => { byId[a.id] = { ...a, children: [] }; });
    this.agents.forEach(a => {
      if (a.parent_id && byId[a.parent_id]) {
        byId[a.parent_id].children.push(byId[a.id]);
      } else {
        roots.push(byId[a.id]);
      }
    });

    const renderNode = (node, depth) => {
      const el = this._el('div', {
        className: 'agent-node' + (node.id === this.selectedAgentId ? ' selected' : ''),
        style: 'padding-left:' + (12 + depth * 16) + 'px',
        onclick: () => this._selectAgent(node.id),
      });

      el.appendChild(this._createStatusDot(node.status));

      const info = this._el('div', { className: 'agent-info' });
      info.appendChild(this._el('span', { className: 'agent-name', textContent: node.name || node.id }));
      if (node.task) {
        info.appendChild(this._el('span', {
          className: 'agent-task',
          textContent: node.task.length > 60 ? node.task.substring(0, 57) + '...' : node.task,
          title: node.task,
        }));
      }
      el.appendChild(info);

      const badges = this._el('div', { className: 'agent-badges' });

      if (node.compacting) {
        const cb = this._el('span', { className: 'agent-badge compacting' });
        cb.appendChild(this._icon('loader', 'icon-xs spin-icon'));
        cb.appendChild(document.createTextNode('compact'));
        badges.appendChild(cb);
      }

      if (node.tool_count > 0) {
        badges.appendChild(this._el('span', {
          className: 'agent-badge',
          textContent: node.tool_count + ' tools',
        }));
      }

      if (this.screenshotAgents.has(node.id)) {
        const sb = this._el('span', { className: 'agent-badge', title: 'Has browser' });
        sb.appendChild(this._icon('monitor', 'icon-xs'));
        badges.appendChild(sb);
      }

      el.appendChild(badges);
      container.appendChild(el);
      node.children.forEach(c => renderNode(c, depth + 1));
    };

    if (roots.length === 0) {
      const empty = this._el('div', { className: 'empty-state' });
      empty.appendChild(this._icon('bot', 'icon-lg ghost-icon'));
      empty.appendChild(this._el('div', { textContent: 'Waiting for agents...' }));
      container.appendChild(empty);
    } else {
      roots.forEach(r => renderNode(r, 0));
    }

    this._updateStatCard('stat-agents', this.agents.length);
    this._refreshIcons();
  }

  // ------- Terminal Feed (unified chronological) -------

  _renderTerminal() {
    if (this.activeTab !== 'terminal') return;

    const container = document.getElementById('terminal-feed');
    if (!container) return;

    // Build merged timeline
    const entries = [];

    // Chat messages
    this.chatMessages.forEach(m => {
      entries.push({
        type: 'chat',
        timestamp: m.timestamp || '',
        agent_id: m.agent_id || '',
        data: m,
      });
    });

    // Tool executions
    this.tools.forEach(t => {
      entries.push({
        type: 'tool',
        timestamp: t.timestamp || '',
        agent_id: t.agent_id || '',
        data: t,
      });
    });

    // Vulnerability reports (use index as tiebreaker)
    this.vulnerabilities.forEach((v, i) => {
      entries.push({
        type: 'vuln',
        timestamp: v.timestamp || v.discovered_at || '',
        agent_id: v.agent_id || '',
        data: v,
        _idx: i,
      });
    });

    // Sort by timestamp
    entries.sort((a, b) => {
      if (a.timestamp && b.timestamp) return a.timestamp.localeCompare(b.timestamp);
      if (a.timestamp) return -1;
      if (b.timestamp) return 1;
      return 0;
    });

    // Check if we need to re-render
    const totalCount = entries.length + Object.keys(this.streaming).length;
    if (totalCount === this._terminalRenderedCount && !this._terminalDirty) return;
    this._terminalRenderedCount = totalCount;
    this._terminalDirty = false;

    const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;
    this._clearEl(container);

    // Render each entry
    entries.forEach(entry => {
      switch (entry.type) {
        case 'chat':
          container.appendChild(this._renderTermChatEntry(entry.data));
          break;
        case 'tool':
          container.appendChild(this._renderTermToolEntry(entry.data));
          break;
        case 'vuln':
          container.appendChild(this._renderTermVulnEntry(entry.data));
          break;
      }
    });

    // Streaming/thinking indicator
    const streamingAgents = Object.keys(this.streaming);
    streamingAgents.forEach(agentId => {
      const content = this.streaming[agentId];
      if (content) {
        container.appendChild(this._renderTermThinkingEntry(agentId, content));
      }
    });

    // Cursor at end
    if (this.stats && this.stats.status !== 'completed') {
      const cursor = this._el('div', { className: 'term-cursor' });
      cursor.appendChild(this._el('span', { className: 'term-cursor-char' }));
      container.appendChild(cursor);
    }

    if (wasAtBottom) {
      container.scrollTop = container.scrollHeight;
    }

    this._refreshIcons();
  }

  _renderTermChatEntry(msg) {
    const entry = this._el('div', { className: 'term-entry term-chat' });

    const iconWrap = this._el('span', { className: 'term-entry-icon' });
    const iconName = msg.role === 'assistant' ? 'bot' : msg.role === 'user' ? 'user' : 'info';
    iconWrap.appendChild(this._icon(iconName, 'icon-xs'));
    entry.appendChild(iconWrap);

    const contentWrap = this._el('div', { className: 'term-entry-content' });

    const agentName = msg.agent_id ? this._getAgentName(msg.agent_id) : '';
    const roleLabel = msg.role === 'assistant' ? 'Agent' : msg.role === 'user' ? 'User' : 'System';
    const roleText = agentName ? roleLabel + ' (' + agentName + ')' : roleLabel;

    contentWrap.appendChild(this._el('div', {
      className: 'term-chat-role ' + (msg.role || 'system'),
      textContent: roleText,
    }));

    const chatContent = this._el('div', { className: 'term-chat-content' });
    chatContent.appendChild(this._renderMarkdown(msg.content || ''));
    contentWrap.appendChild(chatContent);

    entry.appendChild(contentWrap);

    if (msg.timestamp) {
      entry.appendChild(this._el('span', {
        className: 'term-timestamp',
        textContent: this._fmtTime(msg.timestamp),
      }));
    }

    return entry;
  }

  _renderTermToolEntry(tool) {
    const entry = this._el('div', { className: 'term-entry term-tool' });

    const iconWrap = this._el('span', { className: 'term-entry-icon' });
    const iconName = this._getToolIcon(tool.tool_name);
    iconWrap.appendChild(this._icon(iconName, 'icon-xs'));
    entry.appendChild(iconWrap);

    const contentWrap = this._el('div', { className: 'term-entry-content' });

    const header = this._el('div', { style: 'display:flex;align-items:center;gap:6px' });
    header.appendChild(this._el('span', { className: 'term-tool-name', textContent: tool.tool_name }));
    header.appendChild(this._createStatusDot(tool.status));

    // Agent name
    if (tool.agent_id) {
      header.appendChild(this._el('span', {
        className: 'tool-agent-name',
        textContent: this._getAgentName(tool.agent_id),
      }));
    }

    contentWrap.appendChild(header);

    // Tool args summary
    const argsSummary = this._getToolArgsSummary(tool);
    if (argsSummary) {
      const argsEl = this._el('div', { className: 'term-tool-args' });
      argsEl.appendChild(document.createTextNode(argsSummary));
      contentWrap.appendChild(argsEl);
    }

    // Result summary (truncated)
    if (tool.result_summary && tool.status === 'completed') {
      let resultText = '';
      if (typeof tool.result_summary === 'string') {
        resultText = tool.result_summary;
      } else if (typeof tool.result_summary === 'object') {
        const keys = Object.keys(tool.result_summary).filter(k => k !== 'screenshot');
        if (keys.length > 0) {
          resultText = keys.map(k => k + ': ' + String(tool.result_summary[k]).substring(0, 80)).join(', ');
        }
      }
      if (resultText && resultText.length > 0) {
        contentWrap.appendChild(this._el('div', {
          className: 'term-tool-result',
          textContent: resultText.length > 150 ? resultText.substring(0, 147) + '...' : resultText,
        }));
      }
    }

    entry.appendChild(contentWrap);

    // Duration
    if (tool.timestamp && tool.completed_at) {
      const dur = this._calcDuration(tool.timestamp, tool.completed_at);
      if (dur) {
        entry.appendChild(this._el('span', { className: 'term-tool-duration', textContent: dur }));
      }
    }

    if (tool.timestamp) {
      entry.appendChild(this._el('span', {
        className: 'term-timestamp',
        textContent: this._fmtTime(tool.timestamp),
      }));
    }

    return entry;
  }

  _renderTermVulnEntry(vuln) {
    const card = this._el('div', { className: 'term-vuln', onclick: () => this._showVulnDetail(vuln) });

    const header = this._el('div', { className: 'term-vuln-header' });
    header.appendChild(this._icon('shield-alert', 'icon-xs'));
    header.appendChild(this._el('span', { className: 'term-vuln-title', textContent: vuln.title || 'Vulnerability Found' }));

    const sevBadge = this._el('span', {
      className: 'term-vuln-severity ' + (vuln.severity || 'info'),
      textContent: (vuln.severity || 'INFO').toUpperCase(),
    });
    header.appendChild(sevBadge);

    if (vuln.cvss) {
      header.appendChild(this._el('span', {
        className: 'vuln-cvss-score',
        textContent: 'CVSS ' + vuln.cvss,
      }));
    }

    card.appendChild(header);

    if (vuln.description) {
      card.appendChild(this._el('div', {
        className: 'term-vuln-desc',
        textContent: vuln.description.length > 200 ? vuln.description.substring(0, 197) + '...' : vuln.description,
      }));
    }

    return card;
  }

  _renderTermThinkingEntry(agentId, content) {
    const wrap = this._el('div', { className: 'term-thinking' });

    const header = this._el('div', { className: 'term-thinking-header' });
    header.appendChild(this._el('span', { className: 'term-thinking-dot' }));
    header.appendChild(this._el('span', {
      className: 'term-thinking-label',
      textContent: 'Thinking',
    }));
    const agentName = this._getAgentName(agentId);
    if (agentName && agentName !== agentId) {
      header.appendChild(this._el('span', {
        className: 'tool-agent-name',
        textContent: agentName,
      }));
    }
    wrap.appendChild(header);

    const truncated = content.length > 500 ? '...' + content.slice(-500) : content;
    const textEl = this._el('div', { className: 'term-thinking-text' });
    textEl.appendChild(this._renderMarkdown(truncated));
    wrap.appendChild(textEl);

    return wrap;
  }

  // ------- Tools Timeline -------

  _renderToolsTimeline() {
    if (this.activeTab !== 'tools') return;

    const container = document.getElementById('tools-timeline');
    if (!container) return;

    this._clearEl(container);

    if (this.tools.length === 0) {
      const empty = this._el('div', { className: 'empty-state' });
      empty.appendChild(this._icon('wrench', 'icon-lg ghost-icon'));
      empty.appendChild(this._el('div', { textContent: 'No tool executions yet' }));
      container.appendChild(empty);
      this._refreshIcons();
      return;
    }

    this.tools.forEach(tool => {
      const entry = this._el('div', {
        className: 'tool-entry',
        onclick: (e) => {
          const el = e.currentTarget;
          el.classList.toggle('expanded');
        },
      });

      // Timeline dot
      const dotClass = this._getToolCategory(tool.tool_name);
      const statusClass = tool.status === 'running' ? ' running' : '';
      entry.appendChild(this._el('span', { className: 'tool-dot ' + dotClass + statusClass }));

      const info = this._el('div', { className: 'tool-info' });

      // Header row
      const header = this._el('div', { className: 'tool-header' });
      header.appendChild(this._icon(this._getToolIcon(tool.tool_name), 'icon-xs'));
      header.appendChild(this._el('span', { className: 'tool-name', textContent: tool.tool_name }));
      header.appendChild(this._el('span', { className: 'tool-status-badge ' + (tool.status || '') }));

      if (tool.agent_id) {
        header.appendChild(this._el('span', {
          className: 'tool-agent-name',
          textContent: this._getAgentName(tool.agent_id),
        }));
      }

      if (tool.timestamp && tool.completed_at) {
        const dur = this._calcDuration(tool.timestamp, tool.completed_at);
        if (dur) {
          header.appendChild(this._el('span', { className: 'tool-duration', textContent: dur }));
        }
      }

      info.appendChild(header);

      // Args summary
      const argsSummary = this._getToolArgsSummary(tool);
      if (argsSummary) {
        info.appendChild(this._el('div', { className: 'tool-args-summary', textContent: argsSummary }));
      }

      // Expandable detail
      const detail = this._el('div', { className: 'tool-detail' });

      if (tool.args && Object.keys(tool.args).length > 0) {
        detail.appendChild(this._el('div', { className: 'tool-detail-label', textContent: 'Arguments' }));
        detail.appendChild(this._el('pre', { textContent: JSON.stringify(tool.args, null, 2) }));
      }

      if (tool.result_summary) {
        detail.appendChild(this._el('div', { className: 'tool-detail-label', textContent: 'Result' }));
        const resultText = typeof tool.result_summary === 'string'
          ? tool.result_summary
          : JSON.stringify(tool.result_summary, null, 2);
        detail.appendChild(this._el('pre', { textContent: resultText }));
      }

      info.appendChild(detail);
      entry.appendChild(info);
      container.appendChild(entry);
    });

    this._refreshIcons();
  }

  // ------- Stats -------

  _renderStats() {
    const s = this.stats;
    if (!s || !s.llm) return;

    const llm = s.llm.total || {};
    this._updateStatCard('stat-agents', s.agent_count || 0);
    this._updateStatCard('stat-tools', s.tool_count || 0);
    this._updateStatCard('stat-tokens', this._fmtNum(s.llm.total_tokens || 0));
    this._updateStatCard('stat-cost', '$' + (llm.cost || 0).toFixed(2));
    this._updateStatCard('stat-tps', s.tokens_per_second || 0);

    if (s.start_time) {
      const start = new Date(s.start_time);
      const end = s.end_time ? new Date(s.end_time) : new Date();
      const elapsed = Math.floor((end - start) / 1000);
      const mins = Math.floor(elapsed / 60);
      const secs = elapsed % 60;
      this._updateStatCard('stat-time', mins + ':' + secs.toString().padStart(2, '0'));
    }

    // Context bar
    if (s.max_context_tokens && s.context_limit) {
      const pct = Math.min(100, Math.round((s.max_context_tokens / s.context_limit) * 100));
      const fill = document.getElementById('context-bar-fill');
      const label = document.getElementById('context-bar-label');
      if (fill) fill.style.width = pct + '%';
      if (label) {
        label.textContent = this._fmtNum(s.max_context_tokens) + ' / ' + this._fmtNum(s.context_limit) + ' tokens (' + pct + '%)';
      }
      if (pct > 80) {
        if (fill) fill.style.background = pct > 95 ? 'var(--red)' : 'var(--amber)';
      }
    }

    // Status bar
    this._renderStatusBar();

    this._setStatus(s.status || 'running', s.status || 'running');
  }

  _updateStatCard(id, value) {
    const card = document.getElementById(id);
    if (!card) return;
    const valEl = card.querySelector('.stat-value');
    if (valEl) {
      valEl.textContent = value;
    }
  }

  // ------- Scan Config -------

  _renderScanConfig() {
    const bar = document.getElementById('scan-config-bar');
    if (!bar) return;
    if (!this.scanConfig) return;

    const sc = this.scanConfig;
    bar.style.display = 'flex';

    const target = document.getElementById('scan-config-target');
    const mode = document.getElementById('scan-config-mode');
    const name = document.getElementById('scan-config-name');

    if (target) {
      this._clearEl(target);
      target.appendChild(this._el('span', { className: 'config-label', textContent: 'Target: ' }));
      const targetVal = (sc.targets && sc.targets.length > 0) ? sc.targets.join(', ') : (sc.target || '');
      target.appendChild(this._el('span', { className: 'config-value', textContent: targetVal }));
    }

    if (mode && sc.scan_mode) {
      this._clearEl(mode);
      mode.appendChild(this._el('span', { className: 'config-label', textContent: 'Mode: ' }));
      mode.appendChild(this._el('span', { className: 'config-value', textContent: sc.scan_mode }));
    }

    if (name && this.stats && this.stats.run_name) {
      this._clearEl(name);
      name.appendChild(this._el('span', { className: 'config-label', textContent: 'Run: ' }));
      name.appendChild(this._el('span', { className: 'config-value', textContent: this.stats.run_name }));
    }
  }

  // ------- Status Bar -------

  _renderStatusBar() {
    const s = this.stats;
    if (!s) return;

    const runIdEl = document.getElementById('status-run-id');
    const durationEl = document.getElementById('status-duration');

    if (runIdEl && s.run_id) {
      const textNodes = runIdEl.querySelectorAll('.status-text');
      textNodes.forEach(n => n.remove());
      const t = this._el('span', { className: 'status-text', textContent: ' ' + s.run_id.substring(0, 8) });
      runIdEl.appendChild(t);
    }

    if (durationEl && s.start_time) {
      const start = new Date(s.start_time);
      const end = s.end_time ? new Date(s.end_time) : new Date();
      const elapsed = Math.floor((end - start) / 1000);
      const mins = Math.floor(elapsed / 60);
      const secs = elapsed % 60;
      const textNodes = durationEl.querySelectorAll('.status-text');
      textNodes.forEach(n => n.remove());
      const t = this._el('span', {
        className: 'status-text',
        textContent: ' ' + mins + ':' + secs.toString().padStart(2, '0'),
      });
      durationEl.appendChild(t);
    }

    // Terminal title
    const titleEl = document.getElementById('terminal-title');
    if (titleEl) {
      const runName = (s.run_name) ? 'esprit-cli \u2014 ' + s.run_name : 'esprit-cli';
      titleEl.textContent = runName;
    }
  }

  // ------- Report -------

  _renderReport() {
    if (!this.finalReport) return;

    const btn = document.getElementById('report-tab-btn');
    if (btn) btn.style.display = 'flex';

    if (this.activeTab !== 'report') return;

    const container = document.getElementById('report-content');
    if (!container) return;

    this._clearEl(container);
    container.appendChild(this._renderMarkdown(this.finalReport));
    this._refreshIcons();
  }

  // ------- Browser Tabs -------

  _renderBrowserTabs() {
    const tabsContainer = document.getElementById('browser-tabs');
    if (!tabsContainer) return;
    this._clearEl(tabsContainer);

    const agents = [...this.screenshotAgents];
    if (agents.length <= 1) return;

    agents.forEach(agentId => {
      const btn = this._el('button', {
        className: 'browser-tab' + (agentId === this.selectedAgentId ? ' active' : ''),
        textContent: this._getAgentName(agentId),
        onclick: () => {
          this._selectAgent(agentId);
          this._fetchScreenshot(agentId);
        },
      });
      tabsContainer.appendChild(btn);
    });
  }

  // ------- Vulnerabilities -------

  _renderVulns() {
    const container = document.getElementById('vulns-list');
    const countEl = document.getElementById('vuln-count');
    if (countEl) countEl.textContent = this.vulnerabilities.length;

    this._clearEl(container);

    if (this.vulnerabilities.length === 0) {
      const empty = this._el('div', { className: 'empty-state' });
      empty.appendChild(this._icon('shield', 'icon-lg ghost-icon'));
      empty.appendChild(this._el('div', { textContent: 'No vulnerabilities found yet' }));
      container.appendChild(empty);
      this._refreshIcons();
      return;
    }

    const sevOrder = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
    const sorted = [...this.vulnerabilities].sort((a, b) =>
      (sevOrder[a.severity] ?? 5) - (sevOrder[b.severity] ?? 5)
    );

    sorted.forEach(v => {
      const el = this._el('div', {
        className: 'vuln-item vuln-' + (v.severity || 'info'),
        onclick: () => this._showVulnDetail(v),
      });

      el.appendChild(this._el('span', { className: 'vuln-dot' }));
      el.appendChild(this._el('span', { className: 'vuln-title', textContent: v.title || 'Unknown' }));

      if (v.cvss) {
        el.appendChild(this._el('span', { className: 'vuln-cvss-score', textContent: v.cvss }));
      }

      el.appendChild(this._el('span', {
        className: 'vuln-severity',
        textContent: (v.severity || '').toUpperCase(),
      }));

      container.appendChild(el);
    });

    this._refreshIcons();
  }

  // ------- Agent selection -------

  _selectAgent(agentId) {
    this.selectedAgentId = agentId;
    this._terminalDirty = true;
    this._renderAgents();
    this._renderTerminal();
    this._renderBrowserTabs();

    if (this.screenshotAgents.has(agentId)) {
      this._fetchScreenshot(agentId);
    }
  }

  // ------- Screenshot fetching -------

  _fetchScreenshot(agentId) {
    const refreshEl = document.getElementById('browser-refresh-indicator');
    if (refreshEl) refreshEl.style.display = 'inline-flex';

    fetch('/api/screenshot/' + agentId)
      .then(r => r.json())
      .then(data => {
        if (refreshEl) refreshEl.style.display = 'none';
        const viewer = document.getElementById('browser-viewer');
        const img = document.getElementById('browser-img');
        const placeholder = document.getElementById('browser-placeholder');
        const urlEl = document.getElementById('browser-url');

        if (data.screenshot) {
          img.src = 'data:image/png;base64,' + data.screenshot;
          img.style.display = 'block';
          placeholder.style.display = 'none';
          viewer.classList.add('visible');
          urlEl.textContent = data.url || '';
        }
      })
      .catch(() => {
        if (refreshEl) refreshEl.style.display = 'none';
      });
  }

  // ------- Vulnerability modal -------

  _showVulnDetail(vuln) {
    const modal = document.getElementById('vuln-modal');
    document.getElementById('vuln-modal-title').textContent = vuln.title || 'Vulnerability Detail';

    const body = document.getElementById('vuln-modal-body');
    this._clearEl(body);

    // Severity badge
    body.appendChild(this._el('div', {
      className: 'vuln-detail-severity vuln-' + (vuln.severity || 'info'),
      textContent: (vuln.severity || '').toUpperCase(),
    }));

    // CVE link
    if (vuln.cve) {
      const cveLink = this._el('a', {
        className: 'cve-link',
        textContent: vuln.cve,
        href: 'https://nvd.nist.gov/vuln/detail/' + vuln.cve,
        target: '_blank',
        rel: 'noopener',
      });
      const cveDiv = this._el('div', { className: 'vuln-detail-field', style: 'margin-bottom:8px' });
      cveDiv.appendChild(this._el('strong', { textContent: 'CVE: ' }));
      cveDiv.appendChild(cveLink);
      body.appendChild(cveDiv);
    }

    const fields = [
      ['CVSS', vuln.cvss],
      ['Target', vuln.target],
      ['Endpoint', vuln.endpoint],
      ['Method', vuln.method],
    ];
    fields.forEach(([label, val]) => {
      if (val) {
        const f = this._el('div', { className: 'vuln-detail-field' }, [
          this._el('strong', { textContent: label + ': ' }),
          document.createTextNode(String(val)),
        ]);
        body.appendChild(f);
      }
    });

    // CVSS breakdown gauge
    if (vuln.cvss_breakdown && typeof vuln.cvss_breakdown === 'object') {
      const gauge = this._el('div', { className: 'cvss-gauge' });
      const metrics = { AV: 'Vector', AC: 'Complexity', PR: 'Privileges', UI: 'User', S: 'Scope', C: 'Conf', I: 'Integrity', A: 'Avail' };
      const cvssColors = { N: 'var(--red)', L: 'var(--amber)', H: 'var(--green)', P: 'var(--orange)', R: 'var(--green)', U: 'var(--red)', C: 'var(--red)', S: 'var(--amber)' };

      for (const [key, label] of Object.entries(metrics)) {
        const val = vuln.cvss_breakdown[key];
        if (val !== undefined) {
          const metric = this._el('div', { className: 'cvss-metric' });
          metric.appendChild(this._el('span', { className: 'cvss-metric-label', textContent: key }));
          metric.appendChild(this._el('span', { className: 'cvss-metric-value', textContent: val }));
          const bar = this._el('div', { className: 'cvss-metric-bar' });
          const barVal = val === 'N' ? 100 : val === 'L' ? 40 : val === 'H' ? 80 : 60;
          const barFill = this._el('div', {
            className: 'cvss-metric-bar-fill',
            style: 'width:' + barVal + '%;background:' + (cvssColors[val] || 'var(--text-dim)'),
          });
          bar.appendChild(barFill);
          metric.appendChild(bar);
          gauge.appendChild(metric);
        }
      }
      body.appendChild(gauge);
    }

    const sections = [
      ['Description', vuln.description],
      ['Impact', vuln.impact],
      ['Technical Analysis', vuln.technical_analysis],
      ['Proof of Concept', vuln.poc_description],
      ['Remediation', vuln.remediation_steps],
    ];
    sections.forEach(([title, content]) => {
      if (content) {
        const sec = this._el('div', { className: 'vuln-detail-section' });
        const h3 = this._el('h3', { textContent: title });
        sec.appendChild(h3);
        sec.appendChild(this._renderMarkdown(content));
        body.appendChild(sec);
      }
    });

    // PoC code with copy button
    if (vuln.poc_script_code) {
      const sec = this._el('div', { className: 'vuln-detail-section' });
      const h3Row = this._el('h3');
      h3Row.appendChild(document.createTextNode('PoC Code'));
      const copyBtn = this._el('button', {
        className: 'copy-btn',
        onclick: (e) => {
          e.stopPropagation();
          navigator.clipboard.writeText(vuln.poc_script_code).then(() => {
            copyBtn.classList.add('copied');
            this._clearEl(copyBtn);
            copyBtn.appendChild(this._icon('check', 'icon-xs'));
            copyBtn.appendChild(document.createTextNode('Copied'));
            this._refreshIcons();
            setTimeout(() => {
              copyBtn.classList.remove('copied');
              this._clearEl(copyBtn);
              copyBtn.appendChild(this._icon('copy', 'icon-xs'));
              copyBtn.appendChild(document.createTextNode('Copy'));
              this._refreshIcons();
            }, 2000);
          });
        },
      });
      copyBtn.appendChild(this._icon('copy', 'icon-xs'));
      copyBtn.appendChild(document.createTextNode('Copy'));
      h3Row.appendChild(copyBtn);
      sec.appendChild(h3Row);
      sec.appendChild(this._el('pre', { textContent: vuln.poc_script_code }));
      body.appendChild(sec);
    }

    // Code diff
    if (vuln.code_diff) {
      const sec = this._el('div', { className: 'vuln-detail-section' });
      sec.appendChild(this._el('h3', { textContent: 'Code Diff' }));
      const diffPre = this._el('div');
      vuln.code_diff.split('\n').forEach(line => {
        let cls = 'diff-line context';
        if (line.startsWith('+')) cls = 'diff-line added';
        else if (line.startsWith('-')) cls = 'diff-line removed';
        diffPre.appendChild(this._el('div', { className: cls, textContent: line }));
      });
      sec.appendChild(diffPre);
      body.appendChild(sec);
    }

    // Copy curl button for endpoint
    if (vuln.endpoint && vuln.method) {
      const curlCmd = 'curl -X ' + (vuln.method || 'GET') + ' ' + (vuln.endpoint || '');
      const sec = this._el('div', { className: 'vuln-detail-section' });
      const curlBtn = this._el('button', {
        className: 'copy-btn',
        onclick: () => {
          navigator.clipboard.writeText(curlCmd).then(() => {
            this._clearEl(curlBtn);
            curlBtn.appendChild(this._icon('check', 'icon-xs'));
            curlBtn.appendChild(document.createTextNode('Copied'));
            curlBtn.classList.add('copied');
            this._refreshIcons();
            setTimeout(() => {
              curlBtn.classList.remove('copied');
              this._clearEl(curlBtn);
              curlBtn.appendChild(this._icon('terminal', 'icon-xs'));
              curlBtn.appendChild(document.createTextNode('Copy Curl'));
              this._refreshIcons();
            }, 2000);
          });
        },
      });
      curlBtn.appendChild(this._icon('terminal', 'icon-xs'));
      curlBtn.appendChild(document.createTextNode('Copy Curl'));
      sec.appendChild(curlBtn);
      body.appendChild(sec);
    }

    modal.style.display = 'flex';
    this._refreshIcons();
  }

  // ------- Markdown rendering (safe DOM) -------

  _renderMarkdown(text) {
    const container = this._el('div');
    if (!text) return container;

    // Split into blocks by code fences
    const segments = [];
    let remaining = text;
    const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/;

    while (remaining.length > 0) {
      const match = remaining.match(codeBlockRegex);
      if (!match) {
        segments.push({ type: 'text', content: remaining });
        break;
      }
      const idx = remaining.indexOf(match[0]);
      if (idx > 0) {
        segments.push({ type: 'text', content: remaining.substring(0, idx) });
      }
      segments.push({ type: 'code', lang: match[1], content: match[2] });
      remaining = remaining.substring(idx + match[0].length);
    }

    segments.forEach(seg => {
      if (seg.type === 'code') {
        const pre = this._el('div', { className: 'chat-code-block' });
        pre.appendChild(document.createTextNode(seg.content));
        container.appendChild(pre);
      } else {
        this._renderInlineMarkdown(seg.content, container);
      }
    });

    return container;
  }

  _renderInlineMarkdown(text, container) {
    const lines = text.split('\n');
    let inList = false;
    let listEl = null;

    lines.forEach((line, lineIdx) => {
      // Headers
      if (line.startsWith('## ')) {
        if (inList) { container.appendChild(listEl); inList = false; listEl = null; }
        container.appendChild(this._el('span', { className: 'chat-heading chat-heading-2', textContent: line.substring(3) }));
        return;
      }
      if (line.startsWith('### ')) {
        if (inList) { container.appendChild(listEl); inList = false; listEl = null; }
        container.appendChild(this._el('span', { className: 'chat-heading chat-heading-3', textContent: line.substring(4) }));
        return;
      }

      // List items
      if (/^[\-\*] /.test(line)) {
        if (!inList) {
          listEl = this._el('ul', { className: 'chat-list' });
          inList = true;
        }
        const li = this._el('li', { className: 'chat-list-item' });
        this._renderInlineText(line.substring(2), li);
        listEl.appendChild(li);
        return;
      }

      // End list
      if (inList && line.trim() === '') {
        container.appendChild(listEl);
        inList = false;
        listEl = null;
      }

      if (inList) {
        container.appendChild(listEl);
        inList = false;
        listEl = null;
      }

      // Regular text line
      if (line.trim().length > 0) {
        const span = this._el('span');
        this._renderInlineText(line, span);
        container.appendChild(span);
      }

      if (lineIdx < lines.length - 1) {
        container.appendChild(document.createElement('br'));
      }
    });

    if (inList && listEl) {
      container.appendChild(listEl);
    }
  }

  _renderInlineText(text, container) {
    // Process inline code, bold, links
    const parts = [];
    let remaining = text;

    while (remaining.length > 0) {
      // Inline code
      const codeMatch = remaining.match(/`([^`]+)`/);
      // URL pattern
      const urlMatch = remaining.match(/https?:\/\/[^\s<>)\]]+/);
      // Bold
      const boldMatch = remaining.match(/\*\*([^*]+)\*\*/);

      const matches = [
        codeMatch ? { type: 'code', match: codeMatch, idx: remaining.indexOf(codeMatch[0]) } : null,
        urlMatch ? { type: 'url', match: urlMatch, idx: remaining.indexOf(urlMatch[0]) } : null,
        boldMatch ? { type: 'bold', match: boldMatch, idx: remaining.indexOf(boldMatch[0]) } : null,
      ].filter(Boolean).sort((a, b) => a.idx - b.idx);

      if (matches.length === 0) {
        container.appendChild(document.createTextNode(remaining));
        break;
      }

      const first = matches[0];
      if (first.idx > 0) {
        container.appendChild(document.createTextNode(remaining.substring(0, first.idx)));
      }

      if (first.type === 'code') {
        container.appendChild(this._el('span', { className: 'chat-inline-code', textContent: first.match[1] }));
        remaining = remaining.substring(first.idx + first.match[0].length);
      } else if (first.type === 'url') {
        const link = this._el('a', {
          className: 'chat-link',
          textContent: first.match[0],
          href: first.match[0],
          target: '_blank',
          rel: 'noopener',
        });
        container.appendChild(link);
        remaining = remaining.substring(first.idx + first.match[0].length);
      } else if (first.type === 'bold') {
        container.appendChild(this._el('strong', { textContent: first.match[1] }));
        remaining = remaining.substring(first.idx + first.match[0].length);
      }
    }
  }

  // ------- Helpers -------

  _createStatusDot(status) {
    const colors = {
      running: '#22d3ee',
      completed: '#22c55e',
      failed: '#dc2626',
      stopped: '#eab308',
    };
    const color = colors[status] || '#6b7280';
    const pulse = status === 'running' ? ' pulsing' : '';
    return this._el('span', {
      className: 'status-dot' + pulse,
      style: 'background:' + color,
    });
  }

  _getAgentName(agentId) {
    const a = this.agents.find(x => x.id === agentId);
    return a ? (a.name || agentId) : agentId;
  }

  _setStatus(text, state) {
    const el = document.getElementById('scan-status');
    if (!el) return;
    // Preserve the icon element
    const icon = el.querySelector('[data-lucide]');
    const span = el.querySelector('span:not([data-lucide])');
    if (span) span.textContent = text;
    else {
      this._clearEl(el);
      if (icon) el.appendChild(icon);
      el.appendChild(this._el('span', { textContent: text }));
    }
    el.className = 'status-badge status-' + state;
    if (state === 'running') el.classList.add('glow-pulse');
  }

  _fmtNum(n) {
    if (typeof n === 'string') return n;
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  }

  _fmtTime(isoString) {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (e) {
      return '';
    }
  }

  _calcDuration(start, end) {
    try {
      const s = new Date(start);
      const e = new Date(end);
      const ms = e - s;
      if (ms < 0) return null;
      if (ms < 1000) return ms + 'ms';
      if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
      return Math.floor(ms / 60000) + 'm ' + Math.floor((ms % 60000) / 1000) + 's';
    } catch (e) {
      return null;
    }
  }

  _getToolIcon(toolName) {
    if (!toolName) return 'wrench';
    const name = toolName.toLowerCase();
    if (name.includes('browser') || name.includes('navigate') || name.includes('click') || name.includes('screenshot')) return 'monitor';
    if (name.includes('report') || name.includes('vuln')) return 'shield-alert';
    if (name.includes('file') || name.includes('read') || name.includes('write')) return 'file';
    if (name.includes('search') || name.includes('find') || name.includes('grep')) return 'search';
    if (name.includes('think')) return 'brain';
    if (name.includes('agent') || name.includes('delegate') || name.includes('spawn')) return 'git-branch';
    if (name.includes('bash') || name.includes('terminal') || name.includes('command') || name.includes('shell')) return 'terminal';
    if (name.includes('http') || name.includes('request') || name.includes('curl') || name.includes('fetch') || name.includes('api')) return 'globe';
    if (name.includes('scan') || name.includes('crawl') || name.includes('spider')) return 'radar';
    if (name.includes('type') || name.includes('fill') || name.includes('input')) return 'keyboard';
    return 'wrench';
  }

  _getToolCategory(toolName) {
    if (!toolName) return 'default';
    const name = toolName.toLowerCase();
    if (name.includes('browser') || name.includes('navigate') || name.includes('click') || name.includes('screenshot') || name.includes('type') || name.includes('key')) return 'browser';
    if (name.includes('report') || name.includes('vuln')) return 'reporting';
    if (name.includes('file') || name.includes('read') || name.includes('write')) return 'file_ops';
    if (name.includes('agent') || name.includes('delegate') || name.includes('spawn')) return 'agent';
    if (name.includes('think')) return 'thinking';
    return 'default';
  }

  _getToolArgsSummary(tool) {
    if (!tool.args || typeof tool.args !== 'object') return '';
    const args = tool.args;
    const name = (tool.tool_name || '').toLowerCase();

    if (name.includes('browser_action') || name.includes('browser')) {
      const action = args.action || args.type || '';
      const url = args.url || '';
      const selector = args.selector || args.element || '';
      const text = args.text || args.value || '';
      const parts = [action];
      if (url) parts.push(url);
      if (selector) parts.push(selector);
      if (text) parts.push('"' + (text.length > 30 ? text.substring(0, 27) + '...' : text) + '"');
      return parts.filter(Boolean).join(' ');
    }

    if (name.includes('navigate')) return args.url || '';
    if (name.includes('click')) return args.selector || args.element || args.coordinate || '';
    if (name.includes('type') || name.includes('fill')) return (args.selector || '') + ' "' + (args.text || '').substring(0, 30) + '"';
    if (name.includes('think')) return (args.thought || '').substring(0, 80);

    // Generic: show first non-empty arg
    const keys = Object.keys(args).slice(0, 2);
    return keys.map(k => {
      const v = String(args[k]);
      return v.length > 60 ? v.substring(0, 57) + '...' : v;
    }).join(', ');
  }
}

// ------- Global functions -------

function closeVulnModal() {
  document.getElementById('vuln-modal').style.display = 'none';
}

function closeScreenshotModal() {
  document.getElementById('screenshot-modal').style.display = 'none';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeVulnModal();
    closeScreenshotModal();
  }
});

document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-backdrop')) {
    closeVulnModal();
    closeScreenshotModal();
  }
});

// Auto-update elapsed time
setInterval(() => {
  if (window._dashboard && window._dashboard.stats && window._dashboard.stats.start_time) {
    window._dashboard._renderStats();
  }
}, 1000);

// Init
window._dashboard = new EspritDashboard();
