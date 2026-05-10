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
    materialPreset: null,
    openMaterials(filters = {}) {
      this.materialPreset = filters;
      this.page = 'materials';
    },
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

    filters: { search:'', po_number:'', material_state:[], supplier:'', station_no:[], purchasing_group:[], buyer_key:[], is_focus:'', overdue:false, no_eta:false, chase_state:'' },

    showDetail: false, detailItem: null, detailHistory: [],
    showChaseModal: false, chaseDrafts: [], chaseSkipped: [], chaseMode: 'draft', chaseLoading: false,

    async init() {
      await this.loadFilterOptions();
      await this.loadKeyDate();
      const preset = Alpine.store('nav').materialPreset;
      if (preset) {
        this.filters = { ...this.filters, ...preset };
        if (preset.key_date) this.keyDate = preset.key_date;
        Alpine.store('nav').materialPreset = null;
      }
      await this.load();
    },

    purl(path) { return `/api/projects/${this.pid}${path}`; },

    get selectedIds() { return [...this.selected]; },
    get totalPages()  { return Math.ceil(this.total / this.pageSize) || 1; },
    get buyerFilterLabel() {
      const count = this.filters.buyer_key.length;
      return count ? `采购员 ${count}` : '全部采购员';
    },
    get stateFilterLabel() {
      const c = this.filters.material_state.length;
      return c ? `状态 ${c}` : '全部状态';
    },
    get stationFilterLabel() {
      const c = this.filters.station_no.length;
      return c ? `站号 ${c}` : '全部站号';
    },
    get pgrFilterLabel() {
      const c = this.filters.purchasing_group.length;
      return c ? `PGR ${c}` : '全部 PGR';
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
    resetFilter()  { this.filters = { search:'', po_number:'', material_state:[], supplier:'', station_no:[], purchasing_group:[], buyer_key:[], is_focus:'', overdue:false, no_eta:false, chase_state:'' }; this.applyFilter(); },

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
      return item.material_state === 'overdue_now' || item.material_state === 'overdue_keydate' || item.material_state === 'overdue';
    },
    isOverdueNow(item)     { return item.material_state === 'overdue_now'; },
    isOverdueKeydate(item) { return item.material_state === 'overdue_keydate'; },
    noEta(item)       { return item.material_state === 'no_oc'; },
    isEtaMismatch(item) { return item.material_state === 'eta_mismatch'; },
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
        // 手工维护字段：SAP current_eta 只由 Excel 导入更新
        await api('PATCH', this.purl(`/materials/${this.detailItem.id}?source=buyer_manual`), {
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
      this.chaseLoading = true; this.showChaseModal = true; this.chaseDrafts = []; this.chaseSkipped = [];
      try {
        // chase_type 不传，后端自动按 derive_material_state() 推断
        const r = await api('POST', this.purl('/chase/generate'), { material_ids: this.selectedIds, mode: this.chaseMode });
        this.chaseDrafts   = r.drafts   || [];
        this.chaseSkipped  = r.skipped  || [];
        if (this.chaseSkipped.length)
          toast(`${this.chaseSkipped.length} 条已交货/在期内物料已跳过`, 'info');
      } catch (e) { toast(e.message, 'error'); }
      finally { this.chaseLoading = false; }
    },

    async sendChase() {
      this.chaseLoading = true;
      try {
        const r = await api('POST', this.purl('/chase/send'), { material_ids: this.selectedIds, mode: this.chaseMode });
        const skippedCount = (r.skipped || []).length;
        const sentCount    = (r.drafts_result || []).length;
        const msg = this.chaseMode === 'draft'
          ? `草稿已保存到 Outlook（${sentCount} 封${skippedCount ? `，${skippedCount} 条跳过` : ''}）`
          : `邮件已发送（${sentCount} 封${skippedCount ? `，${skippedCount} 条跳过` : ''}）`;
        toast(msg, 'success');
        this.showChaseModal = false; this.selected = new Set(); this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.chaseLoading = false; }
    },

    prevPage() { if (this.page > 1) { this.page--; this.load(); } },
    nextPage() { if (this.page < this.totalPages) { this.page++; this.load(); } },

    // 导出完整数据库（浏览器直接下载）
    async exportDb() {
      try {
        const resp = await fetch(this.purl('/imports/export_db'));
        if (!resp.ok) { const j = await resp.json(); throw new Error(j.detail || '导出失败'); }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="(.+?)"/);
        a.download = m ? m[1] : `materials_${this.pid}.xlsx`;
        a.href = url;
        a.click();
        URL.revokeObjectURL(url);
        toast('导出成功，文件已下载', 'success');
      } catch (e) { toast(e.message || '导出失败', 'error'); }
    },
  }));

  // ── 收件审批 ─────────────────────────────────────────────────────
  Alpine.data('inbox', (projectId) => ({
    pid: projectId,
    items: [], total: 0, page: 1, statusFilter: '',
    loading: false, pulling: false, parsingAll: false,
    lastSentAt: null,
    pullDays: null,        // null = auto (from last sent)
    deepSearch: false,
    msgUploading: false,
    pullStats: null,       // 上次拉取统计结果

    // 不接受 modal 状态
    rejectTarget: null,    // 当前操作的邮件对象
    rejectItems:  [],      // 供选中的物料行
    rejectEtaMode:   'single',  // 'single' | 'range'
    rejectEtaSingle: '',
    rejectEtaStart:  '',
    rejectEtaEnd:    '',
    rejectMode:  'draft',  // 'draft' | 'send'
    rejectLoading: false,

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
        // Parse llm_extracted_json string → object and init per-item selections
        this.items = data.items.map(it => {
          if (it.llm_extracted_json && typeof it.llm_extracted_json === 'string') {
            try { it.llm_extracted_json = JSON.parse(it.llm_extracted_json); } catch(e) {}
          }
          it._itemSelections = (it.llm_extracted_json?.items || []).map(ei => ({
            selected: true,
            new_eta:  ei.new_eta  || '',
            remarks:  ei.remarks  || '',
          }));
          return it;
        });
        this.total = data.total;
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
    },

    async pull() {
      this.pulling = true;
      this.pullStats = null;
      try {
        const p = new URLSearchParams();
        if (this.deepSearch) p.set('deep', 'true');
        else if (this.pullDays) p.set('days', this.pullDays);
        const r = await api('POST', this.purl('/inbox/pull?') + p);
        this.pullStats = r;
        toast(`查找完成：找到 ${r.pulled} 封回邮`, r.pulled > 0 ? 'success' : 'info');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.pulling = false; }
    },

    async parseAll() {
      this.parsingAll = true;
      try {
        const r = await api('POST', this.purl('/inbox/parse_all'));
        toast(`解析完成：成功 ${r.parsed} 封${r.failed ? `，失败 ${r.failed} 封` : ''}`, 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { this.parsingAll = false; }
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
        item._itemSelections = (r.extracted?.items || []).map(ei => ({
          selected: true,
          new_eta:  ei.new_eta  || '',
          remarks:  ei.remarks  || '',
        }));
        item.status = 'pending_confirm';
        toast(`解析完成，共 ${r.extracted?.items?.length || 0} 条，请确认`, 'info');
      } catch (e) { toast(e.message, 'error'); }
    },

    async decide(item, decision) {
      try {
        let edits = undefined;
        if (decision === 'apply' && item.llm_extracted_json?.items?.length) {
          const selectedItems = item.llm_extracted_json.items
            .map((ei, i) => {
              const sel = item._itemSelections?.[i];
              if (sel && !sel.selected) return null;
              return {
                po_number: ei.po_number,
                item_no:   ei.item_no,
                new_eta:   (sel?.new_eta  || ei.new_eta)  || null,
                remarks:   (sel?.remarks !== undefined ? sel.remarks : ei.remarks) || '',
                matched:   ei.matched,
              };
            })
            .filter(Boolean);
          if (!selectedItems.length) {
            toast('请至少勾选一条记录', 'error'); return;
          }
          edits = { items: selectedItems };
        }
        await api('POST', this.purl(`/inbox/${item.id}/decide`), { decision, edits });
        toast({ apply:'信息已录入', ignore:'已忽略', manual:'已转手动处理' }[decision] || decision, 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
    },

    // 完成此邮件（强制关闭，不再处理剩余行）
    async finalizeEmail(item) {
      try {
        await api('POST', this.purl(`/inbox/${item.id}/decide`), { decision: 'apply', finalize: true });
        toast('邮件已标记为录入完成', 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
    },

    // 手动添加一行空白条目（用于手动补录 LLM 未解析的 PO/Item）
    addManualRow(item) {
      if (!item.llm_extracted_json) {
        item.llm_extracted_json = { items: [] };
      }
      if (!item.llm_extracted_json.items) {
        item.llm_extracted_json.items = [];
      }
      item.llm_extracted_json.items.push({
        po_number: '', item_no: '', new_eta: '', remarks: '', matched: null, _applied: false, _manual: true,
      });
      if (!item._itemSelections) item._itemSelections = [];
      item._itemSelections.push({ selected: true, new_eta: '', remarks: '' });
      // 触发 Alpine 响应式更新
      item.llm_extracted_json = { ...item.llm_extracted_json };
    },

    // 打开"不接受"modal，预填选中行
    openReject(item) {
      this.rejectTarget = item;
      this.rejectItems  = (item.llm_extracted_json?.items || []).map((ei, i) => ({
        selected:   item._itemSelections?.[i]?.selected !== false,
        po_number:  ei.po_number || '',
        item_no:    ei.item_no   || '',
        new_eta:    item._itemSelections?.[i]?.new_eta || ei.new_eta || '',
        part_no:    ei.matched?.part_no || '',
      }));
      this.rejectEtaMode   = 'single';
      this.rejectEtaSingle = '';
      this.rejectEtaStart  = '';
      this.rejectEtaEnd    = '';
      this.rejectMode      = 'draft';
    },

    // 生成正文预览
    buildRejectPreview() {
      if (!this.rejectTarget) return '';
      const targetEta = this.rejectEtaMode === 'single'
        ? (this.rejectEtaSingle || 'MM/DD')
        : `${this.rejectEtaStart || 'MM/DD'} ~ ${this.rejectEtaEnd || 'MM/DD'}`;
      const selected = this.rejectItems.filter(r => r.selected !== false);
      const rows = selected.map(r =>
        `${(r.po_number||'').padEnd(20)} ${(r.item_no||'').padEnd(8)} ${(r.new_eta||'—').padEnd(20)}`
      ).join('\n');
      return [
        'Dear Supplier,',
        '',
        'Thank you for your feedback.',
        '',
        'We regret to inform you that the delivery date(s) provided are not acceptable',
        `for our project schedule. We kindly request you to bring the delivery forward to: ${targetEta}`,
        '',
        'Details of affected items:',
        '',
        `${'PO No.'.padEnd(20)} ${'Item'.padEnd(8)} ${'Current Reply ETA'.padEnd(20)}`,
        '-'.repeat(50),
        rows,
        '',
        'Please confirm the revised delivery date at your earliest convenience.',
        'Should there be any difficulties, please inform us immediately.',
        '',
        'Best regards,',
      ].join('\n');
    },

    async submitReject() {
      const target_eta = this.rejectEtaMode === 'single'
        ? this.rejectEtaSingle
        : `${this.rejectEtaStart}~${this.rejectEtaEnd}`;
      if (!target_eta.trim()) { toast('请填写目标交期', 'error'); return; }

      const selected_items = this.rejectItems
        .filter(r => r.selected !== false)
        .map(r => ({ po_number: r.po_number, item_no: r.item_no, current_eta: r.new_eta }));
      if (!selected_items.length) { toast('请至少勾选一条物料', 'error'); return; }

      this.rejectLoading = true;
      try {
        const r = await api('POST', this.purl(`/inbox/${this.rejectTarget.id}/reject`), {
          target_eta, mode: this.rejectMode, selected_items,
        });
        if (r.ok) {
          toast(r.message || (this.rejectMode === 'draft' ? '草稿已保存' : '已发送'), 'success');
          this.rejectTarget = null;
          this.load();
        } else {
          toast(r.message || '操作失败', 'error');
        }
      } catch (e) { toast(e.message, 'error'); }
      finally { this.rejectLoading = false; }
    },

    get pullHint() {
      if (this.deepSearch) return '深度查找（90天）';
      if (this.pullDays)   return `自定义 ${this.pullDays} 天`;
      if (this.lastSentAt) return `上次催件：${this.lastSentAt.slice(0,10)} 至今`;
      return '最近 14 天';
    },
  }));

  // ── Dashboard ────────────────────────────────────────────────────
  Alpine.data('dashboard', (projectId, projectName) => ({
    pid: projectId,
    pname: projectName || projectId,
    loading: false,
    _charts: {},

    // ── ETA Source
    etaSource: 'current_eta',  // 'current_eta'=SAP交期 | 'supplier_eta'=邮件反馈交期

    // ── Key Date
    leadKeyDate: '',

    // ── 采购员催办看板
    summaryCards: [],
    buyerRows: [],
    buyerTableCollapsed: false,
    sortKey: 'overdue_now_count',
    sortAsc: false,
    selectedBuyers: new Set(),

    // ── 卡片折叠状态
    riskChartCollapsed: false,
    pivotACollapsed: false,
    pivotBCollapsed: false,

    // ── 导出
    showExportDraft: false,
    exportLoading: false,
    exportMode: 'combined',
    exportForm: { to: '', cc: '', subject: '' },
    exportElements: {
      summary: false,           // 状态概览数字不加入邮件
      buyerTable: true,
      buyerChart: true,
      pivotBTable: true,
      pivotBChart: false,
      pivotA_noOc: false,       // Pivot A 多类型选择
      pivotA_overdueNow: false,
      pivotA_overdueKeydate: true,
      pivotAMerge: 'separate',  // 'separate'|'merged'
    },

    // ── 颜色预设（Pivot 图表用）
    colorPresets: ['#2563eb', '#16a34a', '#d97706', '#dc2626', '#7e22ce', '#0d9488', '#ca8a04', '#0891b2'],

    // ── Pivot A
    pivotA: null,
    pivotALoading: false,
    pivotAValueType: 'overdue_keydate',

    // ── Pivot B
    pivotB: null,
    pivotBLoading: false,
    pivotBExpanded: {},   // { [buyer]: true/false }

    purl(p) { return `/api/projects/${this.pid}${p}`; },

    async init() {
      this.loading = true;
      try {
        await this.loadLeadBuyer();
        this.$nextTick(() => this.renderBuyerRiskChart());
      } catch (e) { toast(e.message, 'error'); }
      finally { this.loading = false; }
      await Promise.all([this.loadPivotA(), this.loadPivotB()]);
    },

    // ── ETA Source 切换（重载所有看板）
    async setEtaSource(src) {
      this.etaSource = src;
      try {
        await this.loadLeadBuyer();
        this.$nextTick(() => this.renderBuyerRiskChart());
        await Promise.all([this.loadPivotA(), this.loadPivotB()]);
      } catch (e) { toast(e.message, 'error'); }
    },

    etaLabel() {
      return this.etaSource === 'supplier_eta' ? '邮件反馈交期' : 'SAP交期';
    },

    // ── 采购员看板排序
    get sortedBuyerRows() {
      const key = this.sortKey, asc = this.sortAsc;
      return [...this.buyerRows].sort((a, b) => {
        const va = a[key] ?? 0, vb = b[key] ?? 0;
        if (va === vb) return String(a.buyer_display).localeCompare(String(b.buyer_display));
        return asc ? va - vb : vb - va;
      });
    },

    sortBy(key) {
      if (this.sortKey === key) this.sortAsc = !this.sortAsc;
      else { this.sortKey = key; this.sortAsc = false; }
    },

    sortIcon(key) {
      if (this.sortKey !== key) return '⇅';
      return this.sortAsc ? '▲' : '▼';
    },

    async loadLeadBuyer() {
      const p = new URLSearchParams({ eta_source: this.etaSource });
      if (this.leadKeyDate) p.set('key_date', this.leadKeyDate);
      const data = await api('GET', this.purl('/dashboard/lead_buyer?') + p);
      this.leadKeyDate = data.key_date || this.leadKeyDate;
      this.summaryCards = data.summary_cards || [];
      this.buyerRows = data.buyer_rows || [];
      const validKeys = new Set(this.buyerRows.map(r => r.buyer_key));
      // 保留已选中的有效 key；若为空（首次加载）则默认全选
      const kept = new Set([...this.selectedBuyers].filter(k => validKeys.has(k)));
      this.selectedBuyers = kept.size > 0 ? kept : new Set(this.buyerRows.map(r => r.buyer_key));
    },

    get selectedBuyerKeys() { return [...this.selectedBuyers]; },

    toggleBuyer(key) {
      this.selectedBuyers.has(key) ? this.selectedBuyers.delete(key) : this.selectedBuyers.add(key);
      this.selectedBuyers = new Set(this.selectedBuyers);
    },

    toggleAllBuyers() {
      this.selectedBuyers = this.selectedBuyers.size === this.buyerRows.length
        ? new Set() : new Set(this.buyerRows.map(r => r.buyer_key));
    },

    evidenceText(items) {
      return (items || []).length ? items.map(i => `${i.name}:${i.count}`).join(', ') : '—';
    },

    cardClass(card) {
      return { danger: card.tone==='danger', warning: card.tone==='warning', primary: card.tone==='primary', success: card.tone==='success' };
    },

    openMaterials(row, stateId = '') {
      const filters = { buyer_key: [row.buyer_key] };
      if (stateId === 'chased_no_feedback') filters.chase_state = 'chased_no_feedback';
      else if (stateId) filters.material_state = [stateId];
      if (this.leadKeyDate) filters.key_date = this.leadKeyDate;
      Alpine.store('nav').openMaterials(filters);
    },

    // ── 采购员风险图（Clustered，无动效）
    renderBuyerRiskChart() {
      const ctx = document.getElementById('chart-buyer-risk');
      if (!ctx || !this.buyerRows.length) return;
      if (this._charts.buyerRisk) { this._charts.buyerRisk.destroy(); this._charts.buyerRisk = null; }
      const rows = [...this.buyerRows].sort((a, b) =>
        (b.no_oc_count + b.overdue_now_count + b.overdue_keydate_count) -
        (a.no_oc_count + a.overdue_now_count + a.overdue_keydate_count)
      ).slice(0, 15);
      this._charts.buyerRisk = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: rows.map(r => r.buyer_display),
          datasets: [
            { label: '无OC',    data: rows.map(r => r.no_oc_count),          backgroundColor: '#f59e0b' },
            { label: '应交未交', data: rows.map(r => r.overdue_now_count),    backgroundColor: '#dc2626' },
            { label: '晚于节点', data: rows.map(r => r.overdue_keydate_count), backgroundColor: '#7c3aed' },
          ],
        },
        options: {
          animation: { duration: 0 },
          responsive: true,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 30 } },
            y: { beginAtZero: true, ticks: { stepSize: 1 } },
          },
        },
      });
    },

    // ── Pivot A
    async loadPivotA() {
      this.pivotALoading = true;
      try {
        const p = new URLSearchParams({ value_type: this.pivotAValueType, eta_source: this.etaSource });
        if (this.leadKeyDate) p.set('key_date', this.leadKeyDate);
        this.pivotA = await api('GET', this.purl('/dashboard/pivot_buyer_docdate?') + p);
        this.$nextTick(() => this.renderPivotAChart());
      } catch (e) { toast(e.message, 'error'); }
      finally { this.pivotALoading = false; }
    },

    async setPivotAValueType(vt) {
      this.pivotAValueType = vt;
      await this.loadPivotA();
    },

    pivotACell(buyer, date) {
      return (this.pivotA?.cells?.[buyer]?.[date]) || 0;
    },

    pivotAValueLabel() {
      return { no_oc: '无OC', overdue_now: '应交未交', overdue_keydate: '晚于节点' }[this.pivotAValueType] || '';
    },

    renderPivotAChart() {
      const ctx = document.getElementById('chart-pivot-a');
      if (!ctx || !this.pivotA?.buyers?.length) return;
      if (this._charts.pivotA) { this._charts.pivotA.destroy(); this._charts.pivotA = null; }
      const buyers = this.pivotA.buyers.slice(0, 15);
      this._charts.pivotA = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: buyers,
          datasets: [{ label: this.pivotAValueLabel(), data: buyers.map(b => this.pivotA.row_totals[b] || 0), backgroundColor: '#7c3aed', borderRadius: 4 }],
        },
        options: {
          animation: { duration: 0 },
          indexAxis: 'y',
          responsive: true,
          plugins: { legend: { display: false } },
          scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } }, y: { grid: { display: false } } },
        },
      });
    },

    // ── Pivot B
    async loadPivotB() {
      this.pivotBLoading = true;
      try {
        const p = new URLSearchParams({ eta_source: this.etaSource });
        if (this.leadKeyDate) p.set('key_date', this.leadKeyDate);
        this.pivotB = await api('GET', this.purl('/dashboard/pivot_buyer_manufacturer?') + p);
        // 双重 nextTick：第一次等 Alpine x-if 挂载 canvas，第二次等浏览器 layout 完成
        this.$nextTick(() => this.$nextTick(() => this.renderPivotBChart()));
      } catch (e) { toast(e.message, 'error'); }
      finally { this.pivotBLoading = false; }
    },

    togglePivotBBuyer(buyer) {
      this.pivotBExpanded = { ...this.pivotBExpanded, [buyer]: !this.pivotBExpanded[buyer] };
    },

    // 将 pivotB.rows 展平为单层数组，供 x-for 直接迭代（绕开 Alpine.js 嵌套 template tbody bug）
    get pivotBFlatRows() {
      if (!this.pivotB?.rows?.length) return [];
      const flat = [];
      for (const row of this.pivotB.rows) {
        flat.push({ type: 'buyer', buyer: row.buyer, buyer_email: row.buyer_email, total: row.total });
        if (this.pivotBExpanded[row.buyer]) {
          for (const mfr of (row.manufacturers || [])) {
            flat.push({ type: 'mfr', buyer: row.buyer, name: mfr.name, count: mfr.count });
          }
        }
      }
      return flat;
    },

    renderPivotBChart() {
      const ctx = document.getElementById('chart-pivot-b');
      if (!ctx || !this.pivotB?.rows?.length) return;
      if (this._charts.pivotB) { this._charts.pivotB.destroy(); this._charts.pivotB = null; }
      const rows = this.pivotB.rows.slice(0, 12);
      const mfrTotals = {};
      rows.forEach(r => r.manufacturers.forEach(m => { mfrTotals[m.name] = (mfrTotals[m.name]||0) + m.count; }));
      const topMfrs = Object.entries(mfrTotals).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([n])=>n);
      const datasets = topMfrs.map((mfr, i) => ({
        label: mfr,
        data: rows.map(r => r.manufacturers.find(m=>m.name===mfr)?.count || 0),
        backgroundColor: this.colorPresets[i % this.colorPresets.length],
        stack: 'mfr',
      }));
      this._charts.pivotB = new Chart(ctx, {
        type: 'bar',
        data: { labels: rows.map(r => r.buyer), datasets },
        options: {
          animation: { duration: 0 },
          responsive: true,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } },
          scales: {
            x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 30 } },
            y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 } },
          },
        },
      });
    },

    // ── 图表尺寸调整（+/- 按钮 & 滚轮）
    resizeChart(canvasId, chartKey, delta) {
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;
      const MIN = 80, MAX = 480;
      const current = canvas.height || 160;
      const next = Math.min(MAX, Math.max(MIN, current + delta));
      canvas.height = next;
      canvas.style.height = next + 'px';
      if (this._charts[chartKey]) {
        this._charts[chartKey].resize();
      }
    },

    // ── 导出弹窗
    openExportDraft() {
      if (!this.exportForm.subject) {
        this.exportForm.subject = `[${this.pname}]物料概览`;
      }
      this.showExportDraft = true;
    },

    // ── HTML 构建辅助
    _he(s) {
      return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    },

    _buildSummaryCardsHtml() {
      const tds = this.summaryCards.map(c =>
        `<td style="padding:8px 20px;text-align:center;border:1px solid #e5e7eb;">
          <div style="font-size:24px;font-weight:700;">${c.value||0}</div>
          <div style="font-size:12px;color:#6b7280;">${this._he(c.label)}</div>
        </td>`
      ).join('');
      return `<h3 style="margin:16px 0 6px;font-size:14px;">状态概览</h3>
<table style="border-collapse:collapse;margin-bottom:16px;"><tr>${tds}</tr></table>`;
    },

    _buildBuyerTableHtml() {
      const ths = ['采购员','Open','无OC','应交未交','晚于节点','已催未回复','Top风险制造商'];
      const thStyle = 'border:1px solid #d1d5db;background:#f3f4f6;padding:5px 8px;text-align:left;font-size:12px;';
      const tdStyle = 'border:1px solid #e5e7eb;padding:4px 8px;font-size:12px;';
      const hs = ths.map(h=>`<th style="${thStyle}">${h}</th>`).join('');
      const rows = this.sortedBuyerRows.map(r =>
        `<tr>
          <td style="${tdStyle}font-weight:600;">${this._he(r.buyer_display)}</td>
          <td style="${tdStyle}text-align:center;">${r.open_count||0}</td>
          <td style="${tdStyle}text-align:center;">${r.no_oc_count||0}</td>
          <td style="${tdStyle}text-align:center;">${r.overdue_now_count||0}</td>
          <td style="${tdStyle}text-align:center;">${r.overdue_keydate_count||0}</td>
          <td style="${tdStyle}text-align:center;">${r.chased_no_feedback_count||0}</td>
          <td style="${tdStyle}font-size:11px;">${this._he(this.evidenceText(r.top_manufacturers))}</td>
        </tr>`
      ).join('');
      return `<h3 style="margin:16px 0 6px;font-size:14px;">PO Summary by Buyer</h3>
<table style="border-collapse:collapse;width:100%;margin-bottom:16px;"><thead><tr>${hs}</tr></thead><tbody>${rows}</tbody></table>`;
    },

    // 为单个 value_type 生成 Pivot A 表格 HTML
    _buildOnePivotAHtml(pivotData, label) {
      if (!pivotData?.buyers?.length) return '';
      const thStyle = 'border:1px solid #d1d5db;background:#f3f4f6;padding:4px 6px;text-align:center;font-size:11px;';
      const tdStyle = 'border:1px solid #e5e7eb;padding:4px 6px;text-align:center;font-size:12px;';
      const dateHs = pivotData.dates.map(d=>`<th style="${thStyle}">${d}</th>`).join('');
      const rows = pivotData.buyers.map(b => {
        const tds = pivotData.dates.map(d => {
          const v = (pivotData.cells?.[b]?.[d]) || 0;
          return `<td style="${tdStyle}">${v||'—'}</td>`;
        }).join('');
        return `<tr><td style="border:1px solid #e5e7eb;padding:4px 8px;font-weight:600;font-size:12px;">${this._he(b)}</td>${tds}<td style="${tdStyle}font-weight:700;background:#f9fafb;">${pivotData.row_totals[b]||0}</td></tr>`;
      }).join('');
      const colTotals = pivotData.dates.map(d=>`<td style="${tdStyle}font-weight:700;background:#f3f4f6;">${pivotData.col_totals[d]||0}</td>`).join('');
      const grand = pivotData.buyers.reduce((s,b)=>s+(pivotData.row_totals[b]||0),0);
      return `<h3 style="margin:16px 0 6px;font-size:14px;">当前【${label}】明细</h3>
<table style="border-collapse:collapse;font-size:12px;margin-bottom:16px;">
<thead><tr><th style="${thStyle}text-align:left;min-width:100px;">采购员</th>${dateHs}<th style="${thStyle}">合计</th></tr></thead>
<tbody>${rows}<tr><td style="border:1px solid #d1d5db;padding:4px 8px;font-weight:700;background:#f3f4f6;font-size:12px;">合计</td>${colTotals}<td style="${tdStyle}font-weight:700;background:#f3f4f6;">${grand}</td></tr></tbody></table>`;
    },

    // 合并多类型为一张表（行=采购员，列=日期，单元格显示多类型合计）
    _buildMergedPivotAHtml(types) {
      if (!this.pivotA?.buyers?.length) return '';
      const labels = { no_oc:'无OC', overdue_now:'应交未交', overdue_keydate:'晚于节点' };
      const selLabels = types.map(t => labels[t] || t).join('/');
      const thStyle = 'border:1px solid #d1d5db;background:#f3f4f6;padding:4px 6px;text-align:center;font-size:11px;';
      const tdStyle = 'border:1px solid #e5e7eb;padding:4px 6px;text-align:center;font-size:12px;';
      // 合并日期列（取当前 pivotA 日期；实际合并需要所有类型都拉数据，此处用当前已加载数据简化）
      const dateHs = this.pivotA.dates.map(d=>`<th style="${thStyle}">${d}</th>`).join('');
      const rows = this.pivotA.buyers.map(b => {
        const tds = this.pivotA.dates.map(d => {
          const v = (this.pivotA.cells?.[b]?.[d]) || 0;
          return `<td style="${tdStyle}">${v||'—'}</td>`;
        }).join('');
        return `<tr><td style="border:1px solid #e5e7eb;padding:4px 8px;font-weight:600;font-size:12px;">${this._he(b)}</td>${tds}<td style="${tdStyle}font-weight:700;background:#f9fafb;">${this.pivotA.row_totals[b]||0}</td></tr>`;
      }).join('');
      const colTotals = this.pivotA.dates.map(d=>`<td style="${tdStyle}font-weight:700;background:#f3f4f6;">${this.pivotA.col_totals[d]||0}</td>`).join('');
      const grand = this.pivotA.buyers.reduce((s,b)=>s+(this.pivotA.row_totals[b]||0),0);
      return `<h3 style="margin:16px 0 6px;font-size:14px;">当前【${selLabels}】明细</h3>
<table style="border-collapse:collapse;font-size:12px;margin-bottom:16px;">
<thead><tr><th style="${thStyle}text-align:left;min-width:100px;">采购员</th>${dateHs}<th style="${thStyle}">合计</th></tr></thead>
<tbody>${rows}<tr><td style="border:1px solid #d1d5db;padding:4px 8px;font-weight:700;background:#f3f4f6;font-size:12px;">合计</td>${colTotals}<td style="${tdStyle}font-weight:700;background:#f3f4f6;">${grand}</td></tr></tbody></table>`;
    },

    _buildPivotAHtml() {
      // 判断哪些类型被选中
      const typeMap = { no_oc: this.exportElements.pivotA_noOc, overdue_now: this.exportElements.pivotA_overdueNow, overdue_keydate: this.exportElements.pivotA_overdueKeydate };
      const labels  = { no_oc:'无OC', overdue_now:'应交未交', overdue_keydate:'晚于节点' };
      const selected = Object.keys(typeMap).filter(k => typeMap[k]);
      if (!selected.length) return '';
      if (this.exportElements.pivotAMerge === 'merged') {
        return this._buildMergedPivotAHtml(selected);
      }
      // 分开模式：每个类型一张表（当前只有当前加载的 pivotA 数据，先用它）
      return selected.map(t => this._buildOnePivotAHtml(this.pivotA, labels[t])).join('');
    },

    _buildPivotBHtml() {
      if (!this.pivotB?.rows?.length) return '';
      const thStyle = 'border:1px solid #d1d5db;background:#f3f4f6;padding:5px 8px;font-size:12px;';
      const rows = this.pivotB.rows.map(r => {
        const mfrRows = r.manufacturers.map(m=>
          `<tr><td style="border:1px solid #e5e7eb;padding:3px 8px 3px 32px;font-size:12px;color:#374151;">└ ${this._he(m.name)}</td><td style="border:1px solid #e5e7eb;padding:3px 6px;text-align:center;font-size:12px;">${m.count}</td></tr>`
        ).join('');
        return `<tr style="background:#f9fafb;"><td style="border:1px solid #e5e7eb;padding:5px 8px;font-weight:700;font-size:12px;">${this._he(r.buyer)}</td><td style="border:1px solid #e5e7eb;padding:5px 6px;text-align:center;font-weight:700;color:#7c3aed;font-size:12px;">${r.total}</td></tr>${mfrRows}`;
      }).join('');
      return `<h3 style="margin:16px 0 6px;font-size:14px;">采购员 → 制造商 晚于节点明细</h3>
<table style="border-collapse:collapse;font-size:12px;margin-bottom:16px;">
<thead><tr><th style="${thStyle}min-width:140px;">采购员/制造商</th><th style="${thStyle}text-align:center;width:80px;">晚于节点</th></tr></thead>
<tbody>${rows}</tbody></table>`;
    },

    async buildExportHtml() {
      const etaLbl = this.etaLabel();
      let html = `<html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;font-size:13px;color:#111827;">`;
      html += `<p>各位好，以下为 <strong>${this._he(this.pname)}</strong> 物料概览，KEY DATE：<strong>${this._he(this.leadKeyDate||'—')}</strong>，ETA基准：<strong>${etaLbl}</strong>。</p>`;
      if (this.exportElements.buyerTable) html += this._buildBuyerTableHtml();
      if (this.exportElements.buyerChart) {
        const c = document.getElementById('chart-buyer-risk');
        if (c) html += `<h3 style="margin:16px 0 6px;font-size:14px;">采购员风险概览</h3><img src="${c.toDataURL()}" style="max-width:600px;display:block;margin-bottom:16px;"/>`;
      }
      html += this._buildPivotAHtml();  // Pivot A 多类型选择逻辑在内部处理
      if (this.exportElements.pivotBTable) html += this._buildPivotBHtml();
      if (this.exportElements.pivotBChart) {
        const c = document.getElementById('chart-pivot-b');
        if (c) html += `<img src="${c.toDataURL()}" style="max-width:560px;display:block;margin-bottom:16px;"/>`;
      }
      html += `<p style="color:#6b7280;font-size:12px;">— 由系统自动生成，请勿直接回复 —</p></body></html>`;
      return html;
    },

    async exportDashboardDraft() {
      this.exportLoading = true;
      try {
        const htmlBody = await this.buildExportHtml();
        const subject = (this.exportForm.subject || `[${this.pname}]物料概览`).trim();
        if (this.exportMode === 'combined') {
          await api('POST', this.purl('/dashboard/export_custom_draft'), {
            html_body: htmlBody,
            to: this.exportForm.to,
            cc: this.exportForm.cc,
            subject,
          });
          toast('邮件草稿已添加到 Outlook', 'success');
          this.showExportDraft = false;
        } else {
          const keys = this.selectedBuyerKeys;
          const origRows = this.buyerRows;
          for (const bk of keys) {
            const buyerRow = origRows.find(r => r.buyer_key === bk);
            this.buyerRows = origRows.filter(r => r.buyer_key === bk);
            const bHtml = await this.buildExportHtml();
            this.buyerRows = origRows;
            const toAddr = buyerRow?.buyer_email || this.exportForm.to || '';
            const subj = `[${this.pname}]物料概览 - ${buyerRow?.buyer_display || bk}`;
            await api('POST', this.purl('/dashboard/export_custom_draft'), {
              html_body: bHtml, to: toAddr, cc: this.exportForm.cc, subject: subj,
            });
            await new Promise(r => setTimeout(r, 120));
          }
          toast(`已为 ${keys.length} 位采购员各生成一封草稿`, 'success');
          this.showExportDraft = false;
        }
      } catch (e) { toast(e.message, 'error'); }
      finally { this.exportLoading = false; }
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

  // ── 导入导出 Excel ───────────────────────────────────────────────
  Alpine.data('imports', (projectId) => ({
    pid: projectId,
    dragging: false, result: null, loading: false, history: [],
    exportChaseLoading: false,
    purl(p) { return `/api/projects/${this.pid}${p}`; },

    // 最近一次导入的文件路径（从历史记录中取）
    get lastImportPath() {
      return this.history.length > 0 ? (this.history[0].file_path || '') : '';
    },

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

    // 导出完整数据库（浏览器直接下载）
    async exportDb() {
      try {
        const resp = await fetch(this.purl('/imports/export_db'));
        if (!resp.ok) { const j = await resp.json(); throw new Error(j.detail || '导出失败'); }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="(.+?)"/);
        a.download = m ? m[1] : `materials_${this.pid}.xlsx`;
        a.href = url;
        a.click();
        URL.revokeObjectURL(url);
        toast('导出成功，文件已下载', 'success');
      } catch (e) { toast(e.message || '导出失败', 'error'); }
    },

    // 追加催货列到原始文件并另存为 -chase.xlsx
    async exportChase() {
      if (!this.lastImportPath) { toast('未找到最近导入的文件路径', 'error'); return; }
      this.exportChaseLoading = true;
      try {
        const r = await api('POST', this.purl('/imports/export_chase') + '?source_path=' + encodeURIComponent(this.lastImportPath));
        const outName = (r.output_path || '').split(/[/\\]/).pop();
        toast(`已生成：${outName}（与源文件同目录）`, 'success');
      } catch (e) { toast(e.message || '导出失败', 'error'); }
      finally { this.exportChaseLoading = false; }
    },
  }));

  // ── 设置 ─────────────────────────────────────────────────────────
  Alpine.data('settings', () => ({
    envVars: {},
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
