// ChaseBase v0.2 — Alpine.js 前端

document.addEventListener('alpine:init', () => {

  // ── 全局工具 ──────────────────────────────────────────────────────
  window.toast = (msg, type = 'info') => {
    const c = document.getElementById('toast-container');
    const el = Object.assign(document.createElement('div'), { className: `toast ${type}`, textContent: msg });
    c.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  };

  window.api = async (method, path, body = null, isForm = false) => {
    const opts = { method };
    if (body && !isForm) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body    = JSON.stringify(body);
    } else if (body && isForm) {
      opts.body = body; // FormData
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  };

  // ── 根应用：项目选择 ─────────────────────────────────────────────
  Alpine.data('root', () => ({
    view:        'home',    // 'home' | 'app'
    projects:    [],
    activeProject: null,   // { id, name, description }
    showNewProject: false,
    newForm: { id: '', name: '', description: '' },
    loading: false,

    async init() {
      await this.loadProjects();
    },

    async loadProjects() {
      // Retry up to 5 times with increasing delay.
      // Needed because run.bat opens the browser before the server is ready,
      // and uvicorn --reload can briefly restart the worker on first start.
      const maxAttempts = 5;
      const delayMs = 800;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        try {
          this.projects = await api('GET', '/api/projects');
          return;
        } catch (e) {
          if (attempt < maxAttempts - 1) {
            await new Promise(r => setTimeout(r, delayMs * (attempt + 1)));
          } else {
            toast('无法加载项目列表：' + e.message, 'error');
          }
        }
      }
    },

    async createProject() {
      if (!this.newForm.id.trim() || !this.newForm.name.trim()) {
        toast('项目 ID 和名称不能为空', 'error'); return;
      }
      this.loading = true;
      try {
        await api('POST', '/api/projects', { ...this.newForm });
        toast('项目已创建', 'success');
        this.showNewProject = false;
        this.newForm = { id: '', name: '', description: '' };
        await this.loadProjects();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
    },

    selectProject(p) {
      this.activeProject = p;
      this.view = 'app';
    },

    backHome() {
      this.view = 'home';
      this.activeProject = null;
      this.loadProjects();
    },
  }));

  // ── 子应用页面状态（Alpine Store，全局共享）── ────────────────────
  Alpine.store('nav', {
    page: 'materials',
    setPage(p) { this.page = p; },
  });

  // subapp data 仅用于包裹，读取 store
  Alpine.data('subapp', () => ({
    get page()      { return Alpine.store('nav').page; },
    setPage(p)      { Alpine.store('nav').setPage(p); },
    projectId: null,
    init() {
      this.$nextTick(() => {
        this.projectId = this.$el.closest('[data-project-id]')?.dataset.projectId
          || this.$el.dataset.projectId;
      });
    },
  }));

  // ── 物料表 ───────────────────────────────────────────────────────
  Alpine.data('materials', (projectId) => ({
    pid: projectId,
    items: [], total: 0, page: 1, pageSize: 50, loading: false,
    selected: new Set(),
    filterOptions: { stations: [], purchasing_groups: [], suppliers: [], buyers: [] },
    keyDate: '',
    chaseView: { count: true, chaseDate: true, feedbackDate: true },

    filters: { search:'', po_number:'', material_state:'', supplier:'', station_no:'', purchasing_group:'', buyer_key:[], is_focus:'', overdue:false, no_eta:false },

    showDetail: false, detailItem: null, detailHistory: [],
    showChaseModal: false, chaseDrafts: [], chaseMode: 'draft', chaseLoading: false, chaseType: 'oc_confirmation',

    async init() {
      await this.loadFilterOptions();
      await this.loadKeyDate();
      await this.load();
    },

    purl(path) { return `/api/projects/${this.pid}${path}`; },

    get selectedIds() { return [...this.selected]; },
    get totalPages()  { return Math.ceil(this.total / this.pageSize) || 1; },
    get buyerFilterLabel() {
      const count = this.filters.buyer_key.length;
      return count ? `采购员 ${count}` : '全部采购员';
    },

    async loadFilterOptions() {
      try { this.filterOptions = await api('GET', this.purl('/materials/filter_options')); }
      catch (e) {}
    },

    async loadKeyDate() {
      try {
        const r = await api('GET', this.purl('/materials/key_date'));
        this.keyDate = r.key_date || '';
      } catch (e) {
        this.keyDate = new Date().toISOString().slice(0, 10);
      }
    },

    async saveKeyDate() {
      try {
        const r = await api('PUT', this.purl('/materials/key_date'), { key_date: this.keyDate });
        this.keyDate = r.key_date;
        await this.load();
        toast('KEY DATE 已更新', 'success');
      } catch (e) { toast(e.message, 'error'); }
    },

    async load() {
      this.loading = true;
      try {
        const p = new URLSearchParams({ page: this.page, page_size: this.pageSize });
        if (this.keyDate) p.set('key_date', this.keyDate);
        for (const [k, v] of Object.entries(this.filters)) {
          if (Array.isArray(v)) {
            v.forEach(item => { if (item) p.append(k, item); });
          } else if (v !== '' && v !== false && v !== null) {
            p.set(k, v);
          }
        }
        const data = await api('GET', this.purl('/materials?') + p);
        this.items = data.items; this.total = data.total;
        if (data.key_date) this.keyDate = data.key_date;
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
    },

    applyFilter() { this.page = 1; this.selected = new Set(); this.load(); },
    resetFilter()  { this.filters = { search:'', po_number:'', material_state:'', supplier:'', station_no:'', purchasing_group:'', buyer_key:[], is_focus:'', overdue:false, no_eta:false }; this.applyFilter(); },

    toggleSelect(id) {
      this.selected.has(id) ? this.selected.delete(id) : this.selected.add(id);
      this.selected = new Set(this.selected);
    },
    toggleAll() {
      this.selected = this.selected.size === this.items.length
        ? new Set()
        : new Set(this.items.map(i => i.id));
    },

    isOverdue(item) {
      return item.material_state === 'overdue';
    },
    noEta(item)  { return item.material_state === 'no_oc'; },
    statusBadge(item) { return item.material_state_badge || ({open:'badge-open',delivered:'badge-delivered',cancelled:'badge-cancelled',on_hold:'badge-on_hold'})[item.status] || 'badge-open'; },
    formatDate(value) {
      if (!value) return '';
      const match = String(value).match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
      if (!match) return '';
      return `${match[1]}/${match[2].padStart(2, '0')}/${match[3].padStart(2, '0')}`;
    },
    formatMmdd(value) {
      const d = this.formatDate(value);
      return d ? d.slice(5) : '';
    },
    chaseDisplay(item) {
      const count = Number(item.chase_count || 0);
      const feedbackDate = this.formatMmdd(item.supplier_feedback_time);
      const chasedDate = this.formatMmdd(item.last_chased_at || item.last_chase_time);
      if (item.supplier_feedback_time) {
        const feedbackCount = Number(item.last_feedback_chase_count || item.chase_count || 0);
        if (this.chaseView.feedbackDate && feedbackDate && this.chaseView.count && feedbackCount) {
          return `已于 ${feedbackDate} 第 ${feedbackCount} 次反馈`;
        }
        if (this.chaseView.feedbackDate && feedbackDate) return `已于 ${feedbackDate} 反馈`;
        if (this.chaseView.count && feedbackCount) return `已第 ${feedbackCount} 次反馈`;
        return '已反馈';
      }
      if (count > 0) {
        const countPart = this.chaseView.count ? `第 ${count} 次` : '';
        const datePart = this.chaseView.chaseDate && chasedDate ? `于 ${chasedDate}` : '';
        return `已${countPart}催${datePart} 未反馈`;
      }
      return '未催';
    },

    async openDetail(item) {
      this.detailItem = { ...item };
      this.showDetail = true;
      try { this.detailHistory = await api('GET', this.purl(`/materials/${item.id}/history`)); }
      catch (e) { this.detailHistory = []; }
    },

    async saveDetail() {
      try {
        await api('PATCH', this.purl(`/materials/${this.detailItem.id}`), {
          current_eta: this.detailItem.current_eta || null,
          supplier_eta: this.detailItem.supplier_eta || null,
          supplier_remarks: this.detailItem.supplier_remarks || null,
          status: this.detailItem.status,
        });
        toast('已保存', 'success'); this.showDetail = false; this.load();
      } catch (e) { toast(e.message, 'error'); }
    },

    async toggleFocus(item) {
      try {
        const r = await api('POST', this.purl(`/materials/${item.id}/toggle_focus`));
        item.is_focus = r.is_focus;
        toast(r.is_focus ? '已标记重点' : '已取消重点', 'success');
      } catch (e) { toast(e.message, 'error'); }
    },

    async openChaseModal() {
      if (!this.selected.size) { toast('请先勾选物料', 'info'); return; }
      this.chaseLoading = true; this.showChaseModal = true; this.chaseDrafts = [];
      try {
        const r = await api('POST', this.purl('/chase/generate'), { material_ids: this.selectedIds, chase_type: this.chaseType, mode: this.chaseMode });
        this.chaseDrafts = r.drafts;
      } catch (e) { toast(e.message, 'error'); }
      finally { this.chaseLoading = false; }
    },

    async sendChase() {
      this.chaseLoading = true;
      try {
        await api('POST', this.purl('/chase/send'), { material_ids: this.selectedIds, chase_type: this.chaseType, mode: this.chaseMode });
        toast(this.chaseMode === 'draft' ? '草稿已保存到 Outlook' : '邮件已发送', 'success');
        this.showChaseModal = false; this.selected = new Set(); this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.chaseLoading = false; }
    },

    prevPage() { if (this.page > 1) { this.page--; this.load(); } },
    nextPage() { if (this.page < this.totalPages) { this.page++; this.load(); } },
  }));

  // ── 收件审批 ─────────────────────────────────────────────────────
  Alpine.data('inbox', (projectId) => ({
    pid: projectId,
    items: [], total: 0, page: 1, statusFilter: '',
    loading: false, pulling: false,
    lastSentAt: null,
    pullDays: null,  // null = auto (from last sent)
    deepSearch: false,
    msgUploading: false,

    purl(p) { return `/api/projects/${this.pid}${p}`; },

    async init() {
      await this.loadLastSentAt();
      await this.load();
    },

    async loadLastSentAt() {
      try {
        const r = await api('GET', this.purl('/chase/last_sent_at'));
        this.lastSentAt = r.last_sent_at;
      } catch (e) {}
    },

    async load() {
      this.loading = true;
      try {
        const p = new URLSearchParams({ limit: 20, offset: (this.page - 1) * 20 });
        if (this.statusFilter) p.set('status', this.statusFilter);
        const data = await api('GET', this.purl('/inbox/list?') + p);
        this.items = data.items; this.total = data.total;
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
    },

    async pull() {
      this.pulling = true;
      try {
        const p = new URLSearchParams();
        if (this.deepSearch) p.set('deep', 'true');
        else if (this.pullDays) p.set('days', this.pullDays);
        const r = await api('POST', this.purl('/inbox/pull?') + p);
        toast(`拉取完成：新增 ${r.pulled} 封（查找 ${r.pulled_days} 天）`, 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.pulling = false; }
    },

    async uploadMsg(event) {
      const file = event.target.files[0];
      if (!file) return;
      this.msgUploading = true;
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await api('POST', this.purl('/inbox/upload_msg'), fd, true);
        toast(`已导入：${r.subject || '(无主题)'}`, 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.msgUploading = false; event.target.value = ''; }
    },

    async parse(item) {
      try {
        const r = await api('POST', this.purl(`/inbox/${item.id}/parse`));
        item.llm_extracted_json = r.extracted;
        item.status = 'pending_confirm';
        toast('解析完成，请确认', 'info');
      } catch (e) { toast(e.message, 'error'); }
    },

    async decide(item, decision) {
      try {
        const edits = {};
        if (item._eta_edit)     edits.new_eta  = item._eta_edit;
        if (item._remarks_edit) edits.remarks   = item._remarks_edit;
        await api('POST', this.purl(`/inbox/${item.id}/decide`), { decision, edits });
        toast({ apply:'已入库', reject:'已拒绝', escalate:'已升级' }[decision] || decision, 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
    },

    get pullHint() {
      if (this.deepSearch) return '深度查找（90天）';
      if (this.pullDays)   return `自定义 ${this.pullDays} 天`;
      if (this.lastSentAt) return `上次催件：${this.lastSentAt.slice(0,10)} 至今`;
      return '最近 14 天';
    },
  }));

  	// ── Dashboard ────────────────────────────────────────────────────
	Alpine.data('dashboard', (projectId) => ({
	    pid: projectId,
	    overview: {}, byStatus: [], overdueSuppliers: [], chaseStats: [],
	    loading: false, _charts: {},

	    // 时间节点
	    timeNodes: [],
	    timeNodeStats: [],
	    showAddTimeNode: false,
	    newTimeNode: { label: '', node_date: '', color: '#2563eb', sort_order: 0 },
	    colorPresets: ['#2563eb', '#16a34a', '#d97706', '#dc2626', '#7e22ce', '#0d9488', '#ca8a04', '#0891b2'],

	    // 钻取视图
	    tnView: 'overview',
	    drilldownData: [],
	    drilldownGroups: [],

	    purl(p) { return `/api/projects/${this.pid}${p}`; },

	    async init() {
	      this.loading = true;
	      try {
	        [this.overview, this.byStatus, this.overdueSuppliers, this.chaseStats] = await Promise.all([
	          api('GET', this.purl('/dashboard/overview')),
	          api('GET', this.purl('/dashboard/aggregates?group_by=status')),
	          api('GET', this.purl('/dashboard/overdue_by_supplier')),
	          api('GET', this.purl('/dashboard/chase_stats')),
	        ]);
	        this.$nextTick(() => this.renderCharts());
	      } catch (e) { toast(e.message, 'error'); }
	      finally { this.loading = false; }
	      await this.loadTimeNodes();
	    },

	    renderCharts() {
	      const statusCtx = document.getElementById('chart-status');
	      if (statusCtx && this.byStatus.length) {
	        if (this._charts.status) this._charts.status.destroy();
	        this._charts.status = new Chart(statusCtx, {
	          type: 'doughnut',
	          data: {
	            labels: this.byStatus.map(s => s.status || '未知'),
	            datasets: [{
	              data: this.byStatus.map(s => s.count || 0),
	              backgroundColor: ['#2563eb', '#16a34a', '#6b7280', '#d97706', '#dc2626'],
	            }],
	          },
	          options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
	        });
	      }
	      const overdueCtx = document.getElementById('chart-overdue');
	      if (overdueCtx && this.overdueSuppliers.length) {
	        if (this._charts.overdue) this._charts.overdue.destroy();
	        const top10 = this.overdueSuppliers.slice(0, 10);
	        this._charts.overdue = new Chart(overdueCtx, {
	          type: 'bar',
	          data: {
	            labels: top10.map(s => s.supplier || '未知'),
	            datasets: [{
	              label: '逾期数量',
	              data: top10.map(s => s.count || 0),
	              backgroundColor: '#dc2626',
	            }],
	          },
	          options: { responsive: true, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { stepSize: 1 } } } },
	        });
	      }
	    },

	    async loadTimeNodes() {
	      try {
	        [this.timeNodes, this.timeNodeStats] = await Promise.all([
	          api('GET', this.purl('/dashboard/time_nodes')),
	          api('GET', this.purl('/dashboard/time_node_stats')),
	        ]);
	        this.$nextTick(() => this.renderTimeNodeChart());
	      } catch (e) { toast(e.message, 'error'); }
	    },

	    async loadDrilldown(groupBy) {
	      try {
	        const data = await api('GET', this.purl(`/dashboard/time_node_drilldown?group_by=${groupBy}`));
	        this.drilldownData = data;
	        const nameSet = new Set();
	        data.forEach(n => (n.groups || []).forEach(g => nameSet.add(g.name)));
	        const totals = {};
	        data.forEach(n => (n.groups || []).forEach(g => { totals[g.name] = (totals[g.name]||0) + g.due_count; }));
	        this.drilldownGroups = [...nameSet].sort((a,b) => (totals[b]||0) - (totals[a]||0)).slice(0,15);
	        data.forEach(n => {
	          n.groupMap = {};
	          (n.groups || []).forEach(g => { n.groupMap[g.name] = g.due_count; });
	        });
	        this.$nextTick(() => this.renderDrilldownChart(groupBy));
	      } catch (e) { toast(e.message, 'error'); }
	    },

	    async createTimeNode() {
	      if (!this.newTimeNode.label || !this.newTimeNode.node_date) return;
	      try {
	        await api('POST', this.purl('/dashboard/time_nodes'), { ...this.newTimeNode });
	        toast('时间节点已添加', 'success');
	        this.newTimeNode = { label: '', node_date: '', color: '#2563eb', sort_order: 0 };
	        this.showAddTimeNode = false;
	        await this.loadTimeNodes();
	      } catch (e) { toast(e.message, 'error'); }
	    },

	    async editTimeNode(node) {
	      const newLabel = prompt('节点名称：', node.label);
	      if (newLabel === null) return;
	      const newDate = prompt('日期（YYYY-MM-DD）：', node.node_date);
	      if (newDate === null) return;
	      try {
	        await api('PUT', this.purl(`/dashboard/time_nodes/${node.id}`), { label: newLabel, node_date: newDate });
	        toast('已更新', 'success');
	        await this.loadTimeNodes();
	      } catch (e) { toast(e.message, 'error'); }
	    },

	    async deleteTimeNode(id) {
	      if (!confirm('确定删除此时间节点？')) return;
	      try {
	        await api('DELETE', this.purl(`/dashboard/time_nodes/${id}`));
	        toast('已删除', 'success');
	        await this.loadTimeNodes();
	      } catch (e) { toast(e.message, 'error'); }
	    },

	    renderTimeNodeChart() {
	      const ctx = document.getElementById('chart-time-nodes');
	      if (!ctx || !this.timeNodeStats.length) return;
	      if (this._charts.timeNodes) this._charts.timeNodes.destroy();

	      const colors = this.timeNodeStats.map(n => {
	        const matched = this.timeNodes.find(t => t.id === n.id);
	        return matched?.color || '#2563eb';
	      });

	      this._charts.timeNodes = new Chart(ctx, {
	        type: 'bar',
	        data: {
	          labels: this.timeNodeStats.map(n => n.label),
	          datasets: [
	            {
	              label: '到期物料',
	              data: this.timeNodeStats.map(n => n.due_count),
	              backgroundColor: colors,
	            },
	            {
	              label: '已逾期',
	              data: this.timeNodeStats.map(n => n.overdue_count),
	              backgroundColor: '#dc2626',
	            },
	          ],
	        },
	        options: {
	          responsive: true,
	          plugins: { legend: { position: 'bottom' } },
	          scales: {
	            x: { grid: { display: false } },
	            y: { beginAtZero: true, ticks: { stepSize: 1 } },
	          },
	        },
	      });
	    },

	    renderDrilldownChart(groupBy) {
	      const chartId = groupBy === 'supplier' ? 'chart-drilldown-supplier' : 'chart-drilldown';
	      const ctx = document.getElementById(chartId);
	      if (!ctx || !this.drilldownData.length) return;
	      const chartKey = groupBy === 'supplier' ? 'drilldownSupplier' : 'drilldown';
	      if (this._charts[chartKey]) this._charts[chartKey].destroy();

	      const labels = this.drilldownData.map(n => n.label);
	      const colors = this.colorPresets;
	      const datasets = this.drilldownGroups.map((name, i) => ({
	        label: name,
	        data: this.drilldownData.map(n => n.groupMap[name] || 0),
	        backgroundColor: colors[i % colors.length],
	      }));
	      this._charts[chartKey] = new Chart(ctx, {
	        type: 'bar',
	        data: { labels, datasets },
	        options: {
	          responsive: true,
	          plugins: {
	            legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } },
	          },
	          scales: {
	            x: { grid: { display: false } },
	            y: { beginAtZero: true, ticks: { stepSize: 1 } },
	          },
	        },
	      });
	    },
  }));// ── Chat ─────────────────────────────────────────────────────────
  Alpine.data('chat', (projectId) => ({
    pid: projectId,
    messages: [], input: '', loading: false, history: [],
    purl(p) { return `/api/projects/${this.pid}${p}`; },

    async send() {
      const text = this.input.trim();
      if (!text || this.loading) return;
      this.input = '';
      this.messages.push({ role: 'user', content: text });
      this.loading = true;
      try {
        const r = await api('POST', this.purl('/chat'), { message: text, history: this.history.slice(-8) });
        this.messages.push({ role: 'assistant', content: r.answer });
        this.history.push({ role: 'user', content: text }, { role: 'assistant', content: r.answer });
        if (r.tool_called) toast(`调用工具: ${r.tool_called}`, 'info');
      } catch (e) {
        this.messages.push({ role: 'assistant', content: '错误: ' + e.message });
      } finally {
        this.loading = false;
        this.$nextTick(() => { const el = document.getElementById('chat-scroll'); if (el) el.scrollTop = el.scrollHeight; });
      }
    },

    onKeydown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); } },
  }));

  // ── 导入 Excel ───────────────────────────────────────────────────
  Alpine.data('imports', (projectId) => ({
    pid: projectId,
    dragging: false, result: null, loading: false, history: [],
    purl(p) { return `/api/projects/${this.pid}${p}`; },

    async init() { await this.loadHistory(); },

    async loadHistory() {
      try { this.history = await api('GET', this.purl('/imports/history')); } catch (e) {}
    },

    async handleFile(file) {
      if (!file) return;
      if (!file.name.match(/\.(xlsx|xls)$/i)) { toast('仅支持 .xlsx / .xls', 'error'); return; }
      this.loading = true; this.result = null;
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await fetch(this.purl('/imports/upload'), { method: 'POST', body: fd });
        if (!r.ok) throw new Error((await r.json()).detail);
        this.result = await r.json();
        toast(`导入完成：新增 ${this.result.rows_added}，更新 ${this.result.rows_updated}，跳过 ${this.result.rows_skipped}`, 'success');
        this.loadHistory();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
    },

    onDrop(e)      { this.dragging = false; this.handleFile(e.dataTransfer.files[0]); },
    onFileInput(e) { this.handleFile(e.target.files[0]); },
  }));

  // ── 设置 ─────────────────────────────────────────────────────────
  Alpine.data('settings', () => ({
    envVars: {},        // 所有 env 键值对
    newKey: '', newVal: '',
    pgr: {},
    newPg: { key: '', name: '', email: '' },
    loading: false,

    async init() { await Promise.all([this.loadEnv(), this.loadPgr()]); },

    async loadEnv() {
      try { this.envVars = await api('GET', '/api/settings'); } catch (e) {}
    },

    async saveEnv() {
      this.loading = true;
      try {
        const updates = {};
        for (const [k, v] of Object.entries(this.envVars)) {
          if (v !== '***') updates[k] = v;
        }
        if (this.newKey.trim()) updates[this.newKey.trim().toUpperCase()] = this.newVal;
        await api('PATCH', '/api/settings', { updates });
        toast('设置已保存，重启生效', 'success');
        this.newKey = ''; this.newVal = '';
        await this.loadEnv();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
    },

    async loadPgr() {
      try { this.pgr = await api('GET', '/api/settings/pgr'); } catch (e) {}
    },

    async savePgr(key, entry) {
      try {
        await api('PUT', `/api/settings/pgr/${key}`, { name: entry.name, email: entry.email });
        toast(`PGR ${key} 已保存`, 'success');
      } catch (e) { toast(e.message, 'error'); }
    },

    async addPgr() {
      if (!this.newPg.key.trim()) { toast('采购组代码不能为空', 'error'); return; }
      try {
        await api('PUT', `/api/settings/pgr/${this.newPg.key}`, { name: this.newPg.name, email: this.newPg.email });
        toast('已添加', 'success');
        this.newPg = { key: '', name: '', email: '' };
        await this.loadPgr();
      } catch (e) { toast(e.message, 'error'); }
    },

    async deletePgr(key) {
      try {
        await api('DELETE', `/api/settings/pgr/${key}`);
        toast(`已删除 ${key}`, 'success');
        await this.loadPgr();
      } catch (e) { toast(e.message, 'error'); }
    },

    get envEntries() { return Object.entries(this.envVars); },
    get pgrEntries()  { return Object.entries(this.pgr); },
  }));

});
