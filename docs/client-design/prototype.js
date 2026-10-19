// Design-only state: no model requests, shell commands, configuration writes, or workspace mutations.
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const app = $('#app');
const screen = $('#screen');
const dialog = $('#dialog');
const dialogContent = $('#dialog-content');
const exampleTaskText = '帮我完善会话恢复流程。中断后再次进入时，要能正确处理未完成的工具调用，避免重复执行。';
const state = {
  view: 'new', project: 'CodeRill', model: 'deepseek-v4-flash', mode: 'execute',
  permission: 'standard', review: 'pending', inspector: false, inspectorTab: 'changes',
  draft: '', attachments: [], jobTab: 'running', jobStopped: false, settingTab: 'general',
  userMessage: exampleTaskText,
};
const projects = [
  { name: 'CodeRill', path: 'C:\\Users\\YCL\\Desktop\\智能agent\\CodeRill', time: '刚刚', sessions: 6 },
  { name: 'FirstCoder', path: 'C:\\Users\\YCL\\Desktop\\智能agent\\FirstCoder', time: '昨天', sessions: 4 },
  { name: 'CoreCoder', path: 'C:\\Users\\YCL\\Desktop\\智能agent\\CoreCoder', time: '10 月 15 日', sessions: 2 },
];
const models = [
  { name: 'deepseek-v4-flash', provider: 'OpenAI 兼容接口', profile: 'deepseek' },
  { name: 'claude-sonnet', provider: 'Anthropic', profile: 'anthropic' },
  { name: '自定义模型', provider: 'OpenAI 兼容接口', profile: 'custom' },
];
const commands = [
  ['/help', '查看全部命令', '命令与快捷操作'], ['/new', '新建任务', '开始一个新的会话'],
  ['/list', '查看会话', '搜索本地保存的会话'], ['/resume ', '恢复会话', '继续指定 ID 的会话'],
  ['/fork', '分支会话', '复制历史，在新的会话中继续'], ['/where', '查看工作区', '项目路径与当前会话 ID'],
  ['/share', '导出会话', '预览并导出脱敏后的文本'], ['/cost', '用量与费用', '查看 token、请求数与估算费用'],
  ['/undo', '撤销上次修改', '先检查目标文件和可撤销范围'], ['/plan on', '进入只读计划', '调查与规划，不修改文件'],
  ['/plan off', '退出只读计划', '回到执行模式'], ['/approve on', '开启自动批准', '切换前确认权限范围'],
  ['/approve off', '关闭自动批准', '恢复逐项审批'], ['/mode standard', '标准权限', '未授权操作需要批准'],
  ['/mode aggressive', '宽松权限', '允许部分文件与命令操作'], ['/mode bypass', '全部允许', '工具执行不再逐项请求批准'],
  ['/reasoning on', '显示推理流', '仅显示模型实际返回的内容'], ['/reasoning off', '收起推理流', '保持回复区简洁'],
  ['/mcp', '查看 MCP 工具', '已连接服务器和可用工具'], ['/attach ', '附加本地文件', '填写文件或图片的本地路径'],
  ['/model ', '切换模型配置', '选择已有模型配置档'], ['/background', '查看后台任务', '查询子任务与结果'],
  ['/apply ', '审查子任务改动', '预览后显式应用工作区差异'], ['/exit', '结束当前会话', '保留历史，不关闭本地服务'],
];
let returnFocus = null;
let toastTimer;
let paletteKind = 'commands';
let paletteItems = [];
let paletteIndex = 0;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}
function icon(name) {
  return '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
}
function hydrate(root = document) {
  $$('[data-icon]', root).forEach((element) => { element.innerHTML = icon(element.dataset.icon); });
}
function fragment(name) {
  return $('#' + name).content.cloneNode(true);
}
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4200);
}
function closeDialog() {
  if (dialog.open) dialog.close();
}
function showDialog(content, className = '', anchor) {
  if (!dialog.open) returnFocus = document.activeElement;
  if (dialog.open) dialog.close();
  dialog.className = 'overlay-dialog ' + className;
  dialog.removeAttribute('style');
  dialogContent.innerHTML = content;
  hydrate(dialogContent);
  dialog.showModal();
  if (anchor) {
    const rect = anchor.getBoundingClientRect();
    const height = dialog.offsetHeight;
    const width = dialog.offsetWidth;
    const top = rect.top > height + 16 ? rect.top - height - 8 : Math.min(rect.bottom + 8, innerHeight - height - 12);
    dialog.style.top = Math.max(12, top) + 'px';
    dialog.style.left = Math.max(12, Math.min(innerWidth - width - 12, rect.right - width)) + 'px';
  }
}
function dialogHead(title) {
  return '<div class="dialog-head"><h2 id="dialog-title">' + title + '</h2><button class="icon-button" data-action="close-dialog" aria-label="关闭对话框">' + icon('close') + '</button></div>';
}
function saveDraft() {
  if ($('#prompt')) state.draft = $('#prompt').value;
}
function closeSidebar() {
  app.classList.remove('sidebar-open');
  $('.workspace').inert = false;
  $('.sidebar').removeAttribute('role');
  $('.sidebar').removeAttribute('aria-modal');
}
function toggleSidebar() {
  if (innerWidth < 768) {
    const opening = !app.classList.contains('sidebar-open');
    if (!opening) { closeSidebar(); $('.mobile-menu').focus(); return; }
    app.classList.remove('sidebar-collapsed');
    app.classList.add('sidebar-open');
    $('.workspace').inert = true;
    $('.sidebar').setAttribute('role', 'dialog');
    $('.sidebar').setAttribute('aria-modal', 'true');
    $('.sidebar-toggle').focus();
  } else if (innerWidth < 1024) app.classList.toggle('sidebar-expanded');
  else app.classList.toggle('sidebar-collapsed');
}
function renderHeader() {
  const labels = { new: '新建任务', chat: '完善会话恢复流程', projects: '项目', background: '后台任务' };
  $('#breadcrumb').innerHTML = '<span class="crumb-project">' + escapeHtml(state.project) + '</span><span class="separator">/</span><strong>' + labels[state.view] + '</strong>';
  $('#top-actions').innerHTML = state.view === 'chat'
    ? '<button class="text-button" data-action="fork" title="创建分支会话">' + icon('git') + '<span>分支</span></button><button class="text-button" data-action="export" title="预览脱敏导出">' + icon('download') + '<span>导出</span></button><button class="icon-button" data-action="task-more" aria-label="更多会话操作">' + icon('more') + '</button><button class="icon-button" data-action="inspector" aria-label="显示或隐藏任务详情" aria-expanded="' + state.inspector + '" title="任务详情">' + icon('panel') + '</button>'
    : '<button class="icon-button" data-action="commands" aria-label="搜索命令" title="搜索命令 · Ctrl K">' + icon('search') + '</button>';
}
function renderComposer() {
  const target = $('[data-composer]');
  if (!target) return;
  target.replaceChildren(fragment('composer-template'));
  $('#prompt').value = state.draft;
  $('#model-label').textContent = state.model;
  $('#workspace-label').textContent = state.project;
  $('#workspace-label').parentElement.title = projects.find((project) => project.name === state.project)?.path || state.project;
  $('#mode-label').textContent = state.mode === 'plan' ? '只读计划' : '执行';
  $('.mode-button').classList.toggle('plan', state.mode === 'plan');
  $('#permission-label').textContent = { standard: '标准权限', aggressive: '宽松权限', bypass: '全部允许' }[state.permission];
  renderAttachments();
  hydrate(target);
  updateSend();
}
function renderAttachments() {
  const box = $('#attachments');
  if (!box) return;
  box.innerHTML = state.attachments.map((name, index) => '<span class="attachment-chip">' + icon('file') + '<span class="file-name">' + escapeHtml(name) + '</span><button type="button" class="icon-button" data-remove-attachment="' + index + '" aria-label="移除附件 ' + escapeHtml(name) + '">' + icon('close') + '</button></span>').join('');
}
function updateSend() {
  const send = $('#send');
  if (!send) return;
  const pending = state.view === 'chat' && state.review === 'pending';
  send.disabled = pending || !(state.draft.trim() || state.attachments.length);
  send.title = pending ? '先处理待审批操作' : '发送任务 · Enter';
  $('.mode-button').disabled = pending;
  $('.mode-button').title = pending ? '处理待审批操作后，再切换执行方式' : '执行方式';
}
function renderView(view) {
  state.view = view;
  if (view !== 'chat') state.inspector = false;
  screen.replaceChildren(fragment('view-' + view));
  $$('.primary-nav [data-view]').forEach((button) => {
    const selected = button.dataset.view === view;
    button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $$('.recent-item').forEach((button, index) => button.classList.toggle('selected', view === 'chat' && index === 0));
  renderHeader();
  renderComposer();
  if (view === 'chat') {
    $('#user-message').textContent = state.userMessage;
    renderReview();
    renderInspector();
  }
  if (view === 'projects') renderProjects();
  if (view === 'background') renderJobs();
  hydrate(screen);
  closeSidebar();
}
function renderReview() {
  const target = $('#review-state');
  if (!target) return;
  if (state.review === 'plan') target.innerHTML = '<div class="plan-preview"><p>只读计划 · 尚未修改任何文件</p><ol><li>检查已完成与结果未知的工具调用如何结算。</li><li>在恢复入口补齐状态判断，避免重复执行已有操作。</li><li>补充中断恢复用例，再运行相关检查。</li></ol><button class="button" data-action="leave-plan">切换到执行</button></div>';
  else if (state.review === 'pending') target.replaceChildren(fragment('review-pending'));
  else if (state.review === 'approved') target.innerHTML = '<div class="result-heading">' + icon('check') + '<span>已批准这一次修改</span></div><p class="result-text">已补充恢复前的结果结算步骤，避免再次运行已完成的工具调用。相关恢复用例已通过。</p><div class="result-checks">' + icon('terminal') + '<span>示例验证结果：8 项检查通过 · 1 个文件改动</span></div><div class="result-actions"><button class="button" data-action="open-changes">' + icon('git') + '查看改动</button><button class="text-button" data-action="reset-review">重新预览审批</button></div>';
  else target.innerHTML = '<div class="denied-notice">' + icon('close') + '<span>这次修改已拒绝，文件未更改。</span></div><p class="result-text">你可以继续说明期望的处理方式，我会据此调整方案。</p><button class="text-button" data-action="reset-review">重新预览审批</button>';
  $('#composer-state').hidden = state.review !== 'pending';
  $('.before-review').textContent = state.review === 'plan' ? '根据检查结果，建议按以下步骤处理：' : '恢复前需要先结算已有工具结果。准备修改以下判断，请确认：';
  if (state.review !== 'pending') $('#prompt').placeholder = '继续这个任务，或提出新的要求…';
  hydrate(target);
  updateSend();
}
function renderInspector() {
  const layout = $('#chat-layout');
  if (!layout) return;
  layout.classList.toggle('details-hidden', !state.inspector);
  const names = { changes: '改动', plan: '计划', run: '运行' };
  $$('.inspector-tabs > button').forEach((button) => {
    button.setAttribute('aria-selected', String(button.dataset.inspector === state.inspectorTab));
    button.tabIndex = button.dataset.inspector === state.inspectorTab ? 0 : -1;
  });
  $('.inspector-tabs [data-inspector="changes"] span').textContent = state.review === 'plan' ? '0' : '1';
  const body = $('#inspector-body');
  body.setAttribute('aria-label', names[state.inspectorTab]);
  if (state.inspectorTab === 'changes') {
    const preview = $('#review-pending').content.querySelector('.diff-preview').outerHTML;
    body.innerHTML = '<p class="inspector-caption">本次操作 · 1 个文件</p><button class="changed-file" data-action="diff-note">' + icon('file') + '<span>runtime.py</span><span class="diff-stats"><b>+2</b><em>−1</em></span></button><p class="inspector-path">coderill / agent / runtime.py</p>' + preview + '<p class="inspector-footnote">' + (state.review === 'pending' ? '这项修改正在等待批准。查看差异不会执行操作。' : state.review === 'approved' ? '此处展示设计样例的文件差异，不代表工作区已修改。' : '操作已拒绝，此差异未应用。') + '</p>';
    if (state.review === 'plan') body.innerHTML = '<p class="inspector-caption">只读计划</p><p class="inspector-footnote">当前没有文件改动。计划阶段只调查与规划。</p>';
  } else if (state.inspectorTab === 'plan') {
    body.innerHTML = '<p class="inspector-caption">会话恢复 · 3 个步骤</p><div class="plan-item">' + icon('check') + '<p>检查会话与工具状态记录<small>已完成 · 查看 4 个文件</small></p></div><div class="plan-item ' + (state.review === 'approved' ? '' : 'waiting') + '">' + icon(state.review === 'approved' ? 'check' : 'clock') + '<p>补齐恢复前的结果结算<small>' + (state.review === 'approved' ? '已完成' : state.review === 'pending' ? '等待你的批准' : state.review === 'plan' ? '计划已列出，尚未执行' : '本次修改已拒绝') + '</small></p></div><div class="plan-item ' + (state.review === 'approved' ? '' : 'waiting') + '">' + icon(state.review === 'approved' ? 'check' : 'clock') + '<p>运行相关恢复用例<small>' + (state.review === 'approved' ? '示例结果：8 项检查通过' : '尚未执行') + '</small></p></div>';
  } else {
    body.innerHTML = '<p class="inspector-caption">当前会话的运行信息 · 示例数据</p>' + property('工作区', state.project) + property('模型', state.model) + property('权限', { standard: '标准', aggressive: '宽松', bypass: '全部允许' }[state.permission]) + property('输入 token', '12,840') + property('输出 token', '1,026') + property('模型请求', '3 次') + property('费用', '暂不可用') + '<p class="inspector-footnote">未配置模型价格时，不将费用显示为零。</p><button class="text-button" data-action="settings">查看模型与权限设置 ' + icon('right') + '</button>';
  }
  renderHeader();
  hydrate(body);
}
function property(label, value) {
  return '<div class="property-row"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></div>';
}
function renderProjects(filter = '') {
  const items = projects.filter((project) => (project.name + project.path).toLowerCase().includes(filter.toLowerCase()));
  $('#project-list').innerHTML = items.length ? items.map((project) => '<button class="project-row" data-project="' + escapeHtml(project.name) + '"><span class="project-row-icon" data-icon="folder"></span><span class="project-row-main"><strong>' + escapeHtml(project.name) + '</strong><small>' + escapeHtml(project.path) + '</small></span><span class="project-row-meta">' + project.sessions + ' 个会话<br>' + project.time + '</span><span data-icon="right"></span></button>').join('') : '<p class="empty-list">没有找到项目。试试其他名称或路径。</p>';
  hydrate($('#project-list'));
}
function renderJobs() {
  $$('.page-tabs > button').forEach((button) => button.classList.toggle('selected', button.dataset.jobTab === state.jobTab));
  if (state.jobTab === 'running') $('#job-list').innerHTML = '<article class="job-row"><span class="job-icon" data-icon="search"></span><div class="job-main"><h2>检查会话事件重放的边界情况</h2><p>CodeRill<span>·</span>只读子任务<span>·</span>2 分钟前</p></div><span class="job-status ' + (state.jobStopped ? 'stopped' : '') + '">' + (state.jobStopped ? '已停止' : '示例：运行中') + '</span><button class="button" data-action="job-details">查看任务</button></article>';
  else $('#job-list').innerHTML = '<article class="job-row"><span class="job-icon" data-icon="check"></span><div class="job-main"><h2>梳理工具注册与权限检查路径</h2><p>CodeRill<span>·</span>只读报告<span>·</span>今天 13:20</p></div><span class="job-status complete">已完成</span><button class="button" data-action="job-report">查看报告</button></article><article class="job-row"><span class="job-icon" data-icon="git"></span><div class="job-main"><h2>补充会话恢复的测试用例</h2><p>CodeRill<span>·</span>独立工作区<span>·</span>昨天 18:42</p></div><span class="job-status complete">待审查改动</span><button class="button" data-action="child-diff">审查改动</button></article>';
  hydrate($('#job-list'));
}
function openModels(anchor) {
  showDialog('<h2 class="menu-header" id="dialog-title">选择模型配置</h2><div class="menu-list">' + models.map((model) => '<button class="menu-item" data-model="' + escapeHtml(model.name) + '"><span><strong>' + escapeHtml(model.name) + '</strong><small>' + model.provider + ' · ' + model.profile + '</small></span>' + (model.name === state.model ? icon('check') : '') + '</button>').join('') + '</div><div class="menu-note">示例配置，仅展示选择方式。真实列表从本地配置读取。</div>', 'menu-dialog', anchor);
}
function openModes(anchor) {
  showDialog('<h2 class="menu-header" id="dialog-title">执行方式</h2><div class="menu-list"><button class="menu-item" data-mode="execute">' + icon('code') + '<span><strong>执行</strong><small>按当前权限设置执行操作</small></span>' + (state.mode === 'execute' ? icon('check') : '') + '</button><button class="menu-item" data-mode="plan">' + icon('plan') + '<span><strong>只读计划</strong><small>调查与规划，不修改文件</small></span>' + (state.mode === 'plan' ? icon('check') : '') + '</button></div>', 'menu-dialog', anchor);
}
function openProjects(anchor) {
  showDialog('<h2 class="menu-header" id="dialog-title">选择工作区</h2><div class="menu-list">' + projects.map((project) => '<button class="menu-item" data-project="' + escapeHtml(project.name) + '">' + icon('folder') + '<span><strong>' + escapeHtml(project.name) + '</strong><small>' + escapeHtml(project.path) + '</small></span>' + (state.project === project.name ? icon('check') : '') + '</button>').join('') + '<button class="menu-item" data-action="add-project">' + icon('plus') + '<span><strong>添加其他文件夹</strong></span></button></div>', 'menu-dialog', anchor);
}
function openAddProject() {
  showDialog(dialogHead('添加工作文件夹') + '<form id="project-form"><div class="dialog-body"><p>输入项目的本地绝对路径。在正式客户端中，由本地服务检查目录是否可用。</p><label class="dialog-field">文件夹路径<input id="project-path" placeholder="C:\\Users\\YCL\\Desktop\\my-project" autocomplete="off" required></label><p class="field-help">设计预览只把目录加入本页列表，不会读取或创建文件夹。</p></div><div class="dialog-footer"><button type="button" class="button" data-action="close-dialog">取消</button><button type="submit" class="button primary">加入预览</button></div></form>');
}
function openPalette(kind = 'commands') {
  paletteKind = kind;
  showDialog('<div class="palette-search"><span data-icon="search"></span><label class="sr-only" for="palette-query" id="dialog-title">' + (kind === 'tasks' ? '搜索任务' : '搜索命令') + '</label><input id="palette-query" type="search" autocomplete="off" placeholder="' + (kind === 'tasks' ? '搜索最近任务…' : '搜索命令，或输入 /…') + '"><button class="escape-key" data-action="close-dialog">Esc</button></div><p class="palette-label">' + (kind === 'tasks' ? '最近会话' : '命令与快捷操作') + '</p><div class="command-list" id="command-results"></div><div class="palette-footer"><span>↑ ↓ 选择</span><span>Enter ' + (kind === 'tasks' ? '打开' : '填入输入框') + '</span><span>Esc 关闭</span></div>', 'palette-dialog');
  filterPalette('');
  $('#palette-query').focus();
}
function filterPalette(query) {
  const search = query.replace(/^\//, '').toLowerCase();
  paletteItems = paletteKind === 'tasks'
    ? ['完善会话恢复流程', '检查工具调用的权限边界', '理解这个项目的结构', '整理测试运行结果'].filter((title) => title.includes(search)).map((title) => [title, 'CodeRill · 本地会话'])
    : commands.filter((item) => item.join(' ').toLowerCase().includes(search))
      .sort((a, b) => Number(b[1].toLowerCase().includes(search)) - Number(a[1].toLowerCase().includes(search)));
  paletteIndex = 0;
  $('#command-results').innerHTML = paletteItems.length ? paletteItems.map((item, index) => '<button class="command-item ' + (index === 0 ? 'highlight' : '') + '" data-palette-index="' + index + '"><span data-icon="' + (paletteKind === 'tasks' ? 'clock' : 'slash') + '"></span><span><strong>' + escapeHtml(paletteKind === 'tasks' ? item[0] : item[1]) + '</strong><small>' + escapeHtml(paletteKind === 'tasks' ? item[1] : item[2]) + '</small></span>' + (paletteKind === 'tasks' ? '' : '<code>' + escapeHtml(item[0]) + '</code>') + '</button>').join('') : '<p class="empty-list">没有匹配项。试试“模型”或“恢复”。</p>';
  hydrate($('#command-results'));
}
function choosePalette(index) {
  const item = paletteItems[index];
  if (!item) return;
  closeDialog();
  if (paletteKind === 'tasks') {
    state.review = 'pending';
    state.mode = 'execute';
    state.userMessage = exampleTaskText;
    state.inspector = innerWidth >= 1180;
    renderView('chat');
  } else {
    if (!$('#prompt')) renderView('new');
    state.draft = item[0];
    $('#prompt').value = state.draft;
    $('#prompt').focus();
    updateSend();
  }
}
function settingsContent(tab) {
  if (tab === 'general') return '<p class="settings-intro">按照你的习惯，安排这个工作空间。</p><div class="setting-row"><div><strong>界面语言</strong><p>菜单与提示使用的语言</p></div><select aria-label="界面语言"><option>简体中文</option></select></div><div class="setting-row"><div><strong>发送消息</strong><p>使用 Shift + Enter 换行</p></div><select aria-label="发送方式"><option>Enter 发送</option><option>Ctrl + Enter 发送</option></select></div><div class="setting-row"><div><strong>显示模型推理流</strong><p>仅在模型实际返回推理内容时展示，可折叠</p></div><input class="switch" type="checkbox" aria-label="显示模型推理流"></div><div class="setting-row"><div><strong>减少动态效果</strong><p>正式客户端默认遵循系统设置</p></div><input class="switch" type="checkbox" aria-label="减少动态效果"></div><p class="settings-caption">CodeRill · 本地客户端界面设计 v1</p>';
  if (tab === 'models') return '<p class="settings-intro">模型配置来自本地文件。此处展示字段与层级。</p><div class="model-config"><div class="config-title">' + escapeHtml(state.model) + '<span class="quiet-badge">当前配置 · 示例</span></div>' + property('Provider', state.model === 'claude-sonnet' ? 'Anthropic' : 'OpenAI 兼容接口') + property('Model', state.model) + property('Base URL', '使用本地配置中的地址') + property('API Key', '•••••••• · 从环境变量读取') + '</div><div class="setting-row"><div><strong>模型选择入口</strong><p>输入框右下角可随时切换已配置模型</p></div><span class="quiet-badge">配置读取</span></div><p class="settings-caption">密钥不通过前端明文展示。保存和编辑配置需要后续接口支持。</p>';
  if (tab === 'permissions') return '<p class="settings-intro">执行方式和操作权限分别设置。只读计划始终限制修改操作。</p>' + [
    ['standard', '标准', '未授权的修改、命令和外部操作需要你批准。'],
    ['aggressive', '宽松', '部分文件操作与命令可自动执行，仍受运行时规则约束。'],
    ['bypass', '全部允许', '工具执行不再逐项请求批准。仅在你信任当前工作区与操作时使用。'],
  ].map((item) => '<label class="settings-option ' + (state.permission === item[0] ? 'selected ' : '') + (item[0] === 'bypass' ? 'danger' : '') + '"><input type="radio" name="permission" value="' + item[0] + '"' + (state.permission === item[0] ? ' checked' : '') + '><span><strong>' + item[1] + '</strong><p>' + item[2] + '</p></span></label>').join('') + '<p class="settings-caption">“始终允许”授权应精确显示文件、命令或工具范围；不会扩大为所有同类操作。</p>';
  if (tab === 'extensions') return '<p class="settings-intro">工作流知识和外部工具，各自按需加载。</p><div class="setting-row"><div><strong>Skills</strong><p>先展示名称与描述，需要时读取完整指令。</p></div><span class="quiet-badge">按需加载</span></div><div class="setting-row"><div><strong>MCP</strong><p>展示已连接服务器、工具与具体错误。</p></div><span class="quiet-badge">本地配置</span></div><div class="model-config"><div class="config-title">没有连接信息</div><p class="settings-intro" style="margin:8px 0 0">设计预览不读取本机扩展。正式列表由 Agent 运行时提供。</p></div><p class="settings-caption">配置入口：~/.coderill/mcp.json<br>项目技能：.agents/skills/</p>';
  return '<p class="settings-intro">主要操作都可以通过键盘完成。</p>' + [
    ['搜索命令', 'Ctrl / Cmd + K'], ['新建任务', 'Ctrl / Cmd + Shift + O'], ['展开 / 收起侧栏', 'Ctrl / Cmd + B'],
    ['发送消息', 'Enter'], ['插入换行', 'Shift + Enter'], ['关闭菜单或对话框', 'Esc'],
  ].map((item) => '<div class="shortcut-row"><span>' + item[0] + '</span><kbd>' + item[1] + '</kbd></div>').join('');
}
function openSettings(tab = 'general') {
  state.settingTab = tab;
  const tabs = [['general', '通用', 'gear'], ['models', '模型', 'code'], ['permissions', '权限', 'shield'], ['extensions', '扩展', 'box'], ['shortcuts', '快捷键', 'terminal']];
  showDialog('<div class="settings-layout"><nav class="settings-nav" aria-label="设置分类"><h2>设置</h2>' + tabs.map((item) => '<button data-setting-tab="' + item[0] + '" class="' + (item[0] === tab ? 'selected' : '') + '">' + icon(item[2]) + '<span>' + item[1] + '</span></button>').join('') + '</nav><div class="settings-main">' + dialogHead(tabs.find((item) => item[0] === tab)[1]) + '<div class="settings-content">' + settingsContent(tab) + '</div><div class="settings-bottom">设计预览 · 调整只影响当前预览，不保存到本机配置</div></div></div>', 'settings-dialog');
}
function showGuide() {
  showDialog(dialogHead('CodeRill 客户端 · 设计预览') + '<div class="dialog-body"><p>参考你提供的桌面界面，保留浅色、留白、窄侧栏和居中输入框的节奏。以下页面均可点击体验。</p><div class="guide-grid"><div><strong>01 · 新建任务</strong><small>模型、项目、执行方式、附件与命令</small></div><div><strong>02 · 对话与审批</strong><small>点击左侧任务，审查差异并批准或拒绝</small></div><div><strong>03 · 项目与后台任务</strong><small>列表、搜索、工作区切换、子任务报告</small></div><div><strong>04 · 设置</strong><small>通用、模型、权限、扩展、快捷键</small></div></div><div class="guide-note">这是界面设计稿。任务、结果、模型与用量均为示例；没有模型调用、命令执行或真实工作区修改。刷新页面恢复初始状态。</div></div><div class="dialog-footer"><button class="button" data-action="guide-chat">查看审批示例</button><button class="button primary" data-action="close-dialog">开始体验</button></div>');
}
function showExport() {
  showDialog(dialogHead('导出会话') + '<div class="dialog-body"><p>先预览脱敏后的会话文本，再决定是否复制或下载。</p><div class="model-config sample-report"><strong>完善会话恢复流程</strong><p>你：完善中断后的恢复处理，避免重复执行。</p><p>CodeRill：已查看相关文件，准备修改恢复判断。</p><p>状态：' + ({ pending: '等待批准', approved: '已批准 · 示例完成', denied: '已拒绝', plan: '只读计划' }[state.review]) + '</p><small>工作区路径：[本地路径已隐藏]</small></div><p class="field-help">此原型展示导出预览，不公开上传任何内容。</p></div><div class="dialog-footer"><button class="button" data-action="close-dialog">关闭</button><button class="button primary" data-action="copy-export">复制示例文本</button></div>');
}
function showJobReport(writable = false) {
  showDialog(dialogHead(writable ? '审查子任务改动' : '子任务报告') + '<div class="dialog-body"><p>' + (writable ? '子任务在独立工作区完成。应用前检查文件差异。' : '梳理工具注册与权限检查路径 · 示例报告') + '</p>' + (writable ? '<div class="model-config">' + property('工作区', '独立 Git worktree') + property('变更文件', 'tests/test_runtime.py') + property('变化', '+18 行 / −0 行') + '</div><div class="guide-note">原型没有可应用的真实改动。正式客户端必须先取得完整 diff，应用前再次校验。</div>' : '<div class="sample-report"><ol><li>State 负责装配工具注册表、权限管理器和 AgentRuntime。</li><li>模型返回的工具调用经过参数校验与权限检查后执行。</li><li>工具结果写回会话，成为下一次模型请求的上下文。</li></ol></div>') + '</div><div class="dialog-footer"><button class="button" data-action="close-dialog">关闭</button>' + (writable ? '<button class="button primary" disabled>应用改动（仅设计展示）</button>' : '') + '</div>');
}
function submitPrompt() {
  saveDraft();
  if (!(state.draft.trim() || state.attachments.length) || (state.view === 'chat' && state.review === 'pending')) return;
  if (state.draft.startsWith('/')) {
    const command = state.draft.trim();
    if (command === '/new') { state.draft = ''; renderView('new'); return; }
    if (command === '/background') { renderView('background'); return; }
    if (command === '/cost') { state.inspector = true; state.inspectorTab = 'run'; renderView('chat'); return; }
    if (command === '/list' || command.startsWith('/resume')) { openPalette('tasks'); return; }
    if (command === '/mcp') { openSettings('extensions'); return; }
    if (command.startsWith('/plan ')) { state.mode = command.endsWith('on') ? 'plan' : 'execute'; state.draft = ''; renderComposer(); toast('已在预览中切换执行方式'); return; }
    if (command === '/share') { showExport(); return; }
    toast('设计预览：此命令已保留，正式客户端将交给现有 CLI 运行时处理。');
    return;
  }
  state.userMessage = state.draft || '请查看附加文件并给出建议。';
  state.draft = '';
  state.attachments = [];
  state.review = state.mode === 'plan' ? 'plan' : 'pending';
  state.inspector = innerWidth >= 1180;
  renderView('chat');
  toast(state.mode === 'plan' ? '已展示只读计划样例，没有修改任何文件。' : '已进入任务与审批的示例流程，没有发起真实执行。');
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('button,[data-setting-tab]');
  if (!button || button.disabled) return;
  if (button.dataset.view) {
    saveDraft();
    if (button.dataset.view === 'new') { saveDraft(); state.draft = ''; }
    if (button.dataset.view === 'chat') { state.review = 'pending'; state.mode = 'execute'; state.userMessage = exampleTaskText; state.inspector = innerWidth >= 1180; }
    renderView(button.dataset.view);
    return;
  }
  if (button.dataset.suggestion) { state.draft = button.dataset.suggestion; $('#prompt').value = state.draft; $('#prompt').focus(); updateSend(); return; }
  if (button.dataset.project) {
    saveDraft();
    state.project = button.dataset.project;
    closeDialog();
    if (state.view !== 'new') renderView('new');
    else { renderComposer(); renderHeader(); }
    return;
  }
  if (button.dataset.model) { state.model = button.dataset.model; closeDialog(); saveDraft(); renderComposer(); renderInspector(); return; }
  if (button.dataset.mode) { state.mode = button.dataset.mode; closeDialog(); saveDraft(); renderComposer(); return; }
  if (button.dataset.removeAttachment !== undefined) { state.attachments.splice(Number(button.dataset.removeAttachment), 1); renderAttachments(); updateSend(); return; }
  if (button.dataset.inspector) { state.inspectorTab = button.dataset.inspector; renderInspector(); return; }
  if (button.dataset.paletteIndex !== undefined) { choosePalette(Number(button.dataset.paletteIndex)); return; }
  if (button.dataset.jobTab) { state.jobTab = button.dataset.jobTab; renderJobs(); return; }
  if (button.dataset.settingTab) {
    const tab = button.dataset.settingTab;
    state.settingTab = tab;
    const titles = { general: '通用', models: '模型', permissions: '权限', extensions: '扩展', shortcuts: '快捷键' };
    $$('.settings-nav > button').forEach((item) => item.classList.toggle('selected', item.dataset.settingTab === tab));
    $('#dialog-title').textContent = titles[tab];
    $('.settings-content').innerHTML = settingsContent(tab);
    hydrate($('.settings-content'));
    return;
  }
  switch (button.dataset.action) {
    case 'toggle-sidebar': toggleSidebar(); break;
    case 'toggle-project': $('#project-tree').hidden = !$('#project-tree').hidden; $('.tree-chevron').classList.toggle('closed-chevron', $('#project-tree').hidden); break;
    case 'settings': openSettings(); break;
    case 'permissions': openSettings('permissions'); break;
    case 'model': openModels(button); break;
    case 'mode': openModes(button); break;
    case 'project-picker': openProjects(button); break;
    case 'add-project': openAddProject(); break;
    case 'attach': $('#file-input').click(); break;
    case 'commands': openPalette(); break;
    case 'search-tasks': openPalette('tasks'); break;
    case 'guide': showGuide(); break;
    case 'guide-chat': closeDialog(); state.review = 'pending'; state.mode = 'execute'; state.inspector = innerWidth >= 1180; renderView('chat'); break;
    case 'close-dialog': closeDialog(); break;
    case 'inspector': state.inspector = !state.inspector; renderInspector(); break;
    case 'open-changes': state.inspector = true; state.inspectorTab = 'changes'; renderInspector(); break;
    case 'approve': state.review = 'approved'; renderReview(); renderInspector(); break;
    case 'deny': state.review = 'denied'; renderReview(); renderInspector(); break;
    case 'reset-review': state.review = 'pending'; renderReview(); renderInspector(); break;
    case 'leave-plan': state.mode = 'execute'; saveDraft(); renderComposer(); toast('已在预览中切换到执行。发送任务后再按权限处理操作。'); break;
    case 'grant': showDialog('<h2 class="menu-header" id="dialog-title">授权范围</h2><div class="menu-list"><button class="menu-item" data-action="approve-grant"><span><strong>始终允许此文件的修改</strong><small>当前工作区 · coderill/agent/runtime.py</small></span></button></div><div class="menu-note">只记住这个目标，不允许其他文件或任意命令。</div>', 'menu-dialog', button); break;
    case 'approve-grant': closeDialog(); state.review = 'approved'; renderReview(); renderInspector(); toast('仅在设计预览中展示授权后的状态，没有保存权限。'); break;
    case 'diff-note': toast('文件差异已在下方展开。原型只展示一项示例改动。'); break;
    case 'fork': showDialog(dialogHead('从这里创建分支') + '<div class="dialog-body"><p>复制当前历史，在一个新的会话中尝试另一种思路。当前会话会保留。</p><div class="guide-note">设计预览不创建真实会话。正式实现复用 /fork。</div></div><div class="dialog-footer"><button class="button primary" data-action="close-dialog">知道了</button></div>'); break;
    case 'export': showExport(); break;
    case 'copy-export': navigator.clipboard.writeText('CodeRill 会话设计样例\n任务：完善会话恢复流程\n工作区：[本地路径已隐藏]\n此内容为设计示例。').then(() => toast('已复制示例文本')).catch(() => toast('浏览器未允许复制，请手动选择文本。')); break;
    case 'task-more': showDialog('<h2 class="menu-header" id="dialog-title">会话操作</h2><div class="menu-list"><button class="menu-item" data-action="undo-info">' + icon('back') + '<span><strong>撤销上次文件修改</strong><small>先检查目标和可恢复范围</small></span></button><button class="menu-item" data-action="recovery-info">' + icon('clock') + '<span><strong>查看中断与恢复状态</strong></span></button></div>', 'menu-dialog', button); break;
    case 'undo-info': showDialog(dialogHead('撤销上次文件修改') + '<div class="dialog-body"><p>执行前展示目标文件，并检查文件在写入后是否又被修改。</p><div class="guide-note">当前 CodeRill 的撤销记录仅保存在进程内存中。重启后不可使用，也不包含 Shell 命令的副作用。</div></div><div class="dialog-footer"><button class="button" data-action="close-dialog">关闭</button><button class="button primary" disabled>此预览没有真实可撤销改动</button></div>'); break;
    case 'recovery-info': showDialog(dialogHead('恢复上一次中断') + '<div class="dialog-body"><p>上一次操作的结果可能尚未记录。先检查实际文件或进程状态，再决定是否继续。</p><div class="guide-note">正式客户端调用现有 resume_turn，不自动重新执行结果未知的工具。恢复失败时保留会话与草稿。</div></div><div class="dialog-footer"><button class="button primary" data-action="close-dialog">知道了</button></div>'); break;
    case 'refresh-jobs': toast('设计样例状态已保留，未连接后台运行时。'); break;
    case 'job-details': showDialog(dialogHead('检查会话事件重放的边界情况') + '<div class="dialog-body"><p>CodeRill · 独立只读子会话 · 设计样例</p>' + property('状态', state.jobStopped ? '已停止' : '运行中（示例）') + property('权限', '只读工具') + property('当前步骤', '检查事件顺序与未完成调用') + '</div><div class="dialog-footer"><button class="button" data-action="close-dialog">关闭</button><button class="button" data-action="cancel-job"' + (state.jobStopped ? ' disabled' : '') + '>停止这个任务</button></div>'); break;
    case 'cancel-job': state.jobStopped = true; closeDialog(); renderJobs(); toast('已展示子任务停止状态，没有终止任何真实进程。'); break;
    case 'job-report': showJobReport(); break;
    case 'child-diff': showJobReport(true); break;
    case 'confirm-bypass': state.permission = 'bypass'; closeDialog(); saveDraft(); renderComposer(); toast('已在预览中选择全部允许；真实权限没有变化。'); break;
  }
});
document.addEventListener('input', (event) => {
  if (event.target.id === 'prompt') {
    state.draft = event.target.value;
    updateSend();
    event.target.style.height = 'auto';
    event.target.style.height = Math.min(220, Math.max(state.view === 'chat' ? 65 : 80, event.target.scrollHeight)) + 'px';
    if (state.draft === '/') openPalette();
  }
  if (event.target.id === 'palette-query') filterPalette(event.target.value);
  if (event.target.id === 'project-search') renderProjects(event.target.value);
});
document.addEventListener('change', (event) => {
  if (event.target.id === 'file-input') {
    state.attachments.push(...[...event.target.files].map((file) => file.name));
    event.target.value = '';
    renderAttachments(); updateSend();
    toast('已添加文件名到预览；文件内容不会上传或读取。');
  }
  if (event.target.name === 'permission') {
    if (event.target.value === 'bypass') {
      showDialog(dialogHead('切换到全部允许？') + '<div class="dialog-body"><p>正式运行时，这个模式会跳过工具的逐项确认。当前只改变设计预览。</p></div><div class="dialog-footer"><button class="button" data-action="close-dialog" autofocus>取消</button><button class="button primary" data-action="confirm-bypass">在预览中切换</button></div>');
    } else {
      state.permission = event.target.value;
      $$('.settings-option').forEach((option) => option.classList.toggle('selected', $('input', option).value === state.permission));
      saveDraft(); renderComposer(); renderInspector();
    }
  }
});
document.addEventListener('submit', (event) => {
  if (event.target.id === 'composer') { event.preventDefault(); submitPrompt(); }
  if (event.target.id === 'project-form') {
    event.preventDefault();
    const path = $('#project-path').value.trim();
    if (!/^(?:[a-zA-Z]:[\\/]|\/)/.test(path)) { $('#project-path').setCustomValidity('请输入绝对路径，例如 C:\\Users\\YCL\\Desktop\\my-project'); $('#project-path').reportValidity(); $('#project-path').setCustomValidity(''); return; }
    const name = path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
    projects.push({ name, path, sessions: 0, time: '刚刚' });
    closeDialog();
    if (state.view === 'projects') renderProjects();
    toast('已加入本次设计预览，未访问该目录。');
  }
});
document.addEventListener('keydown', (event) => {
  if (event.isComposing) return;
  const modifier = event.ctrlKey || event.metaKey;
  if (modifier && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); return; }
  if (modifier && event.shiftKey && event.key.toLowerCase() === 'o') { event.preventDefault(); closeDialog(); state.draft = ''; renderView('new'); return; }
  if (modifier && event.key.toLowerCase() === 'b' && !dialog.open) { event.preventDefault(); $('.sidebar-toggle').click(); return; }
  if (!dialog.open && app.classList.contains('sidebar-open')) {
    if (event.key === 'Escape') { event.preventDefault(); closeSidebar(); $('.mobile-menu').focus(); return; }
    if (event.key === 'Tab') {
      const buttons = $$('button', $('.sidebar')).filter((item) => item.getClientRects().length && !item.disabled);
      if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
    }
  }
  if (!dialog.open && event.key === 'Escape' && state.inspector && innerWidth < 1180) {
    event.preventDefault(); state.inspector = false; renderInspector(); $('.top-actions [data-action="inspector"]')?.focus(); return;
  }
  if (dialog.open && $('#palette-query')) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      paletteIndex = Math.max(0, Math.min(paletteItems.length - 1, paletteIndex + (event.key === 'ArrowDown' ? 1 : -1)));
      $$('.command-item').forEach((item, index) => item.classList.toggle('highlight', index === paletteIndex));
      $('.command-item.highlight')?.scrollIntoView({ block: 'nearest' });
    }
    if (event.key === 'Enter') { event.preventDefault(); choosePalette(paletteIndex); }
  } else if (dialog.open && dialog.classList.contains('menu-dialog') && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault();
    const buttons = $$('.menu-item', dialog);
    const current = buttons.indexOf(document.activeElement);
    const next = (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  } else if (!dialog.open && event.target.id === 'prompt' && event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault(); submitPrompt();
  } else if (!dialog.open && event.target.closest('.inspector-tabs') && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
    event.preventDefault();
    const tabs = $$('.inspector-tabs > button');
    const next = (tabs.indexOf(document.activeElement) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    state.inspectorTab = tabs[next].dataset.inspector;
    renderInspector(); tabs[next].focus();
  }
});
dialog.addEventListener('click', (event) => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
});
dialog.addEventListener('close', () => {
  if (dialog.open) return;
  if (returnFocus?.isConnected) returnFocus.focus();
  else $('#prompt')?.focus();
});
hydrate();
renderView('new');
