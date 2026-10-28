import './style.css'

type Message = { role: 'user' | 'assistant' | 'tool'; content: string }
type Review = { id: string; kind?: string; call: { name: string; arguments: unknown }; diff: string }
type Profile = { name: string; model: string; provider: string }
type Background = { id: string; status: string; description: string; result: string }
type Child = { id: string; task: string; model?: string; allow_write: boolean; applied: boolean; usage?: { requests: number; input_tokens: number; output_tokens: number } | null }
type Session = {
  id: string; model: string; provider: string; workspace: string; updated: string; status: string
  messages: Message[]; review: Review | null; plan: string; planMode: boolean; autoApprove: boolean
  mode: string; reasoning: boolean; profiles: Profile[]; attachments: { name: string; size: number }[]
  usage: { requests: number; input_tokens: number; output_tokens: number; cost: number | null; known_cost: number }
  background: Background[]; children: Child[]
  sandbox: { backend: string; profile: string; network: string; approvedDomains: string[]; outside: string[] }
}
type Summary = { id: string; model: string; updated: string; title: string }
type Result = { message: string; sessionId?: string; exit?: boolean; applyPreview?: { childId: string; diff: string } | null }
type StreamEvent = { kind: string; text?: string; tool?: string; status?: string; error?: string }

const app = document.querySelector<HTMLDivElement>('#app')!
app.innerHTML = `
  <div class="shell">
    <aside class="sidebar" id="sidebar">
      <div class="brand"><span class="brand-icon">✦</span><span>CodeRill</span><span class="brand-small">WORKSPACE</span><button id="sidebar-close" class="icon-button mobile-only" type="button" aria-label="关闭侧栏">×</button></div>
      <button id="new-chat" class="new-chat" type="button"><span class="plus">＋</span> 新建任务 <kbd>Ctrl N</kbd></button>
      <label class="search-wrap"><span>⌕</span><input id="search" type="search" placeholder="搜索会话" aria-label="搜索会话"></label>
      <div class="side-heading">会话记录</div>
      <nav id="sessions" class="sessions" aria-label="会话记录"></nav>
      <div class="sidebar-footer"><span class="status-dot"></span><span>本地运行</span><button id="help" type="button" title="命令帮助">?</button></div>
    </aside>
    <div class="workarea">
      <header class="topbar">
        <button id="menu" class="icon-button mobile-only" type="button" aria-label="打开侧栏">☰</button>
        <div class="breadcrumb"><span id="project-name">工作区</span><span class="slash">/</span><strong id="thread-title">新建任务</strong></div>
        <div class="top-actions"><span class="live" id="live"><span class="status-dot"></span><span>就绪</span></span>
          <button id="fork" class="top-button" type="button" title="分叉会话">分叉</button>
          <button id="share" class="top-button" type="button" title="分享已脱敏记录">分享</button>
          <button id="inspector-toggle" class="icon-button" type="button" aria-label="显示或隐藏详情面板">◫</button>
        </div>
      </header>
      <div class="content-grid">
        <main class="main">
          <div id="conversation" class="conversation" aria-live="polite">
            <div class="thread-inner">
              <div id="empty" class="empty"><div class="empty-icon">✦</div><h1>一起把代码做好</h1><p>描述任务，或从左侧继续之前的会话。</p>
                <div class="suggestions"><button type="button" data-suggestion="解释这个项目的结构">解释项目结构</button><button type="button" data-suggestion="帮我检查最近的代码改动">检查最近的改动</button></div>
              </div>
              <div id="messages" class="messages"></div>
              <div id="activity" class="activity" hidden></div>
            </div>
          </div>
          <div class="bottom">
            <div id="alert" class="alert" role="status" hidden></div>
            <div id="recover" class="recover" hidden><span>上次运行被中断，可继续处理未完成的步骤。</span><button id="recover-button" type="button">恢复运行</button></div>
            <section id="review" class="review" hidden aria-label="操作审批">
              <div class="review-title"><span>⚠</span><strong>需要批准</strong><span>操作前请检查内容</span></div>
              <div id="review-call" class="review-call"></div><pre id="review-diff"></pre>
              <div class="review-actions"><button data-decision="deny" type="button">拒绝</button><button data-decision="approve" type="button">批准一次</button><button data-decision="always" type="button" title="仅记住该目标的授权">始终批准此目标</button></div>
            </section>
            <form id="composer" class="composer">
              <div id="attachments" class="attachment-list"></div>
              <label class="sr-only" for="prompt">输入消息或斜杠命令</label>
              <textarea id="prompt" rows="3" placeholder="描述任务，或输入 / 查看命令…"></textarea>
              <div class="composer-footer"><div class="composer-tools"><button id="attach" type="button" title="添加文件或图片" aria-label="添加附件">＋</button><button id="commands" type="button" title="命令">/ 命令</button><span id="composer-mode" class="mode-label">标准模式</span></div>
                <div class="composer-right"><span>Enter 发送 · Shift Enter 换行</span><button id="stop" class="stop" type="button" hidden>停止</button><button id="send" class="send" type="submit" aria-label="发送消息">↑</button></div>
              </div>
            </form>
            <input id="file-input" type="file" multiple hidden>
            <div class="footnote">CodeRill 可以读取与修改当前工作区。重要改动会请求你的批准。</div>
          </div>
        </main>
        <aside id="inspector" class="inspector" aria-label="任务详情">
          <div class="inspector-heading"><strong>任务详情</strong><button id="inspector-close" class="icon-button mobile-only" type="button" aria-label="关闭详情">×</button></div>
          <section><h2>工作区</h2><div id="workspace" class="workspace-path"></div></section>
          <section><h2>沙箱</h2><div id="sandbox-status" class="workspace-path"></div></section>
          <section><h2>模型</h2><select id="model" aria-label="选择模型"></select></section>
          <section><h2>运行模式</h2><label class="setting"><span>计划模式<small>先调查与规划，不修改文件</small></span><input id="plan-mode" type="checkbox"></label>
            <label class="setting"><span>自动批准<small>跳过普通工具确认；额外域名仍需授权</small></span><input id="auto-approve" type="checkbox"></label>
            <label class="setting"><span>显示推理过程</span><input id="reasoning" type="checkbox"></label>
            <label class="select-setting">权限范围<select id="permission-mode"><option value="standard">标准</option><option value="aggressive">宽松</option><option value="bypass">全部允许</option></select></label>
          </section>
          <section><h2>任务计划</h2><pre id="plan" class="plan-text">暂无计划</pre></section>
          <section><h2>用量</h2><div id="usage" class="usage"></div></section>
          <section><h2>后台任务与子任务</h2><div id="background" class="background-list">暂无后台任务</div></section>
          <section class="inspector-links"><button type="button" data-command="/undo">撤销上次文件修改</button><button type="button" data-command="/mcp">查看 MCP 工具</button><button type="button" data-command="/background">刷新后台任务</button></section>
        </aside>
      </div>
    </div>
  </div>
  <dialog id="command-dialog" class="dialog"><div class="dialog-header"><h2>命令</h2><button type="button" data-close aria-label="关闭">×</button></div><p>也可以在输入框直接输入斜杠命令。</p><div id="command-list" class="command-list"></div></dialog>
  <dialog id="output-dialog" class="dialog output-dialog"><div class="dialog-header"><h2 id="output-title">结果</h2><button type="button" data-close aria-label="关闭">×</button></div><pre id="output-text"></pre><div id="output-actions" class="dialog-actions"></div></dialog>`

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector)!
const prompt = $<HTMLTextAreaElement>('#prompt')
const messages = $('#messages')
const conversation = $('#conversation')
const outputDialog = $<HTMLDialogElement>('#output-dialog')
let current: Session | null = null
let summaries: Summary[] = []
let busy = false
let uploading = false
let activity: string[] = []
let pendingApply: { childId: string; diff: string } | null = null
if (matchMedia('(max-width: 1050px)').matches) $('#inspector').classList.add('closed')

const commandItems = [
  ['/help', '显示所有 CLI 命令'], ['/new', '新建会话'], ['/list', '列出会话'], ['/resume ', '恢复指定会话'],
  ['/fork', '分叉当前会话'], ['/where', '查看工作区'], ['/share', '导出脱敏会话'], ['/cost', '查看用量'],
  ['/undo', '撤销上次文件修改'], ['/plan on', '打开计划模式'], ['/plan off', '关闭计划模式'],
  ['/approve on', '打开自动批准'], ['/approve off', '关闭自动批准'], ['/mode standard', '标准权限'],
  ['/mode aggressive', '宽松权限'], ['/mode bypass', '全部允许'], ['/reasoning on', '显示推理过程'],
  ['/reasoning off', '隐藏推理过程'], ['/mcp', '查看 MCP 工具'], ['/attach ', '附加本地路径'],
  ['/model ', '切换模型配置'], ['/background', '后台任务'], ['/apply ', '预览并应用子任务'], ['/exit', '退出当前会话'],
]

async function api<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
  return data as T
}

function notice(text: string, error = false) {
  const element = $('#alert')
  element.textContent = text
  element.classList.toggle('error', error)
  element.hidden = !text
}

function showError(error: unknown) { notice(error instanceof Error ? error.message : String(error), true) }

function showOutput(title: string, text: string, actions: { label: string; run: () => void }[] = []) {
  $('#output-title').textContent = title
  $('#output-text').textContent = text
  const footer = $('#output-actions')
  footer.replaceChildren()
  for (const action of actions) {
    const button = document.createElement('button')
    button.textContent = action.label
    button.onclick = action.run
    footer.append(button)
  }
  outputDialog.showModal()
}

function appendMessage(item: Message): HTMLElement {
  const row = document.createElement('article')
  row.className = `message ${item.role}`
  const avatar = document.createElement('div')
  avatar.className = 'avatar'
  avatar.textContent = item.role === 'user' ? '你' : item.role === 'tool' ? '›' : '✦'
  const body = document.createElement('div')
  body.className = 'message-body'
  const name = document.createElement('div')
  name.className = 'message-name'
  name.textContent = item.role === 'user' ? '你' : item.role === 'tool' ? '工具结果' : 'CodeRill'
  const text = document.createElement('div')
  text.className = 'message-text'
  text.textContent = item.role === 'tool' ? item.content.slice(0, 1200) : item.content
  body.append(name, text)
  row.append(avatar, body)
  messages.append(row)
  conversation.scrollTop = conversation.scrollHeight
  return text
}

function renderActivity() {
  const box = $('#activity')
  box.hidden = !activity.length
  box.replaceChildren()
  for (const item of activity.slice(-8)) {
    const line = document.createElement('div')
    line.textContent = item
    box.append(line)
  }
  conversation.scrollTop = conversation.scrollHeight
}

function render() {
  messages.replaceChildren()
  for (const item of current?.messages || []) appendMessage(item)
  $('#empty').hidden = Boolean(current?.messages.length)
  $('#workspace').textContent = current?.workspace || ''
  $('#sandbox-status').textContent = current?.sandbox.backend === 'docker'
    ? `Docker · ${current.sandbox.profile} · 网络 ${current.sandbox.network}\n已授权域名：${current.sandbox.approvedDomains.join('、') || '无'}\n沙箱外：${current.sandbox.outside.join('、')}`
    : '关闭'
  $('#project-name').textContent = current?.workspace.split(/[\\/]/).pop() || '工作区'
  $('#thread-title').textContent = summaries.find(item => item.id === current?.id)?.title || '新建任务'
  $('#live').querySelector('span:last-child')!.textContent = busy ? '运行中' : current?.status === 'waiting' ? '待批准' : '就绪'
  $('#live').classList.toggle('running', busy)
  $('#recover').hidden = busy || current?.status !== 'running'
  const review = $('#review')
  review.hidden = !current?.review
  if (current?.review) {
    $('#review-call').textContent = `${current.review.call.name} ${JSON.stringify(current.review.call.arguments)}`
    $('#review-diff').textContent = current.review.diff || '此操作没有文件差异。'
    $('#review [data-decision="always"]').textContent = current.review.kind === 'sandbox_network' ? '本会话允许' : '始终批准此目标'
  }
  const model = $<HTMLSelectElement>('#model')
  model.replaceChildren()
  const option = new Option(`${current?.provider || ''} / ${current?.model || ''}`, 'current')
  model.add(option)
  for (const profile of current?.profiles || []) model.add(new Option(`${profile.name} · ${profile.provider}/${profile.model}`, profile.name))
  model.value = 'current'
  $<HTMLInputElement>('#plan-mode').checked = Boolean(current?.planMode)
  $<HTMLInputElement>('#auto-approve').checked = Boolean(current?.autoApprove)
  $<HTMLInputElement>('#reasoning').checked = Boolean(current?.reasoning)
  $<HTMLSelectElement>('#permission-mode').value = current?.mode || 'standard'
  const modes: Record<string, string> = { standard: '标准', aggressive: '宽松', bypass: '全部允许' }
  $('#composer-mode').textContent = current?.planMode ? '计划模式' : `${modes[current?.mode || 'standard']}模式`
  $('#plan').textContent = current?.plan || '暂无计划'
  const usage = current?.usage
  $('#usage').textContent = usage ? `${usage.requests} 次请求 · ${usage.input_tokens} 输入 / ${usage.output_tokens} 输出 token\n估算费用：${usage.cost === null ? `未知（已知部分 $${usage.known_cost.toFixed(4)}）` : `$${usage.cost.toFixed(4)}`}` : '暂无用量'
  const background = $('#background')
  background.replaceChildren()
  for (const item of current?.background || []) {
    const row = document.createElement('div')
    row.className = 'background-item'
    row.textContent = `${item.status} · ${item.description}`
    row.title = item.result
    background.append(row)
  }
  for (const child of current?.children || []) {
    const row = document.createElement('div')
    row.className = 'background-item'
    row.textContent = `子任务 · ${child.task}${child.usage ? ` · ${child.model || '未知模型'} · ${child.usage.input_tokens} 输入 / ${child.usage.output_tokens} 输出 token` : ''}`
    if (child.allow_write && !child.applied) {
      const button = document.createElement('button')
      button.textContent = '预览改动'
      button.onclick = () => void command(`/apply ${child.id}`)
      row.append(button)
    }
    background.append(row)
  }
  if (!background.childElementCount) background.textContent = '暂无后台任务'
  const attachments = $('#attachments')
  attachments.replaceChildren()
  for (const item of current?.attachments || []) {
    const chip = document.createElement('span')
    chip.textContent = `▢ ${item.name}`
    attachments.append(chip)
  }
  prompt.disabled = busy || uploading || !current
  $<HTMLButtonElement>('#send').disabled = prompt.disabled
  $<HTMLButtonElement>('#attach').disabled = busy || uploading || !current
  $<HTMLButtonElement>('#stop').hidden = !busy
  $<HTMLButtonElement>('#send').hidden = busy
  renderActivity()
}

async function refreshSessions() {
  summaries = await api<Summary[]>('/api/sessions')
  const nav = $('#sessions')
  nav.replaceChildren()
  const search = $<HTMLInputElement>('#search').value.toLowerCase()
  for (const item of summaries.filter(item => `${item.title} ${item.id}`.toLowerCase().includes(search))) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = `session ${item.id === current?.id ? 'active' : ''}`
    button.title = `${item.title} · ${item.id}`
    button.textContent = item.title
    button.disabled = busy || uploading
    button.onclick = () => void openSession(item.id)
    nav.append(button)
  }
  $('#thread-title').textContent = summaries.find(item => item.id === current?.id)?.title || '新建任务'
}

async function openSession(id: string) {
  if (busy || uploading) return
  try {
    current = await api<Session>(`/api/sessions/${id}`)
    activity = []
    await refreshSessions()
    render()
    $('#sidebar').classList.remove('open')
  } catch (error) { showError(error) }
}

async function newSession() {
  if (busy || uploading) return
  try {
    current = await api<Session>('/api/sessions', { from: current?.id })
    activity = []
    await refreshSessions()
    render()
    prompt.focus()
    $('#sidebar').classList.remove('open')
  } catch (error) { showError(error) }
}

async function command(text: string) {
  if (!current || busy || uploading) return
  try {
    const result = await api<Result>(`/api/sessions/${current.id}/command`, { text })
    if (result.sessionId) { await openSession(result.sessionId); return }
    if (result.exit) { current = null; render(); return }
    if (result.applyPreview) {
      pendingApply = result.applyPreview
      showOutput(`应用子任务 ${pendingApply.childId}`, pendingApply.diff, [{ label: '确认应用改动', run: () => void applyChild() }])
    } else if (['/help', '/cost', '/share', '/where', '/list', '/mcp', '/background'].some(cmd => text.startsWith(cmd))) {
      showOutput(text, result.message, text === '/share' ? [{ label: '复制', run: () => void navigator.clipboard.writeText(result.message) }] : [])
    } else notice(result.message)
    current = await api<Session>(`/api/sessions/${current.id}`)
    render()
    await refreshSessions()
  } catch (error) { showError(error) }
}

async function applyChild() {
  if (!current || !pendingApply) return
  try {
    const result = await api<Result>(`/api/sessions/${current.id}/apply`, { childId: pendingApply.childId })
    pendingApply = null
    outputDialog.close()
    notice(result.message)
    current = await api<Session>(`/api/sessions/${current.id}`)
    render()
  } catch (error) { showError(error) }
}

async function stream(action: 'turn' | 'review' | 'recover', body: object) {
  if (!current || busy || uploading) return
  const id = current.id
  busy = true
  activity = ['正在处理…']
  notice('')
  render()
  let answer: HTMLElement | null = null
  let finished = false
  try {
    const response = await fetch(`/api/sessions/${id}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let pending = ''
    while (true) {
      const { done, value } = await reader.read()
      pending += decoder.decode(value || new Uint8Array(), { stream: !done })
      const lines = pending.split('\n')
      pending = lines.pop() || ''
      for (const line of lines) {
        if (!line) continue
        const event = JSON.parse(line) as StreamEvent
        if (event.kind === 'text_delta') {
          if (!answer) answer = appendMessage({ role: 'assistant', content: '' })
          answer.textContent += event.text || ''
          conversation.scrollTop = conversation.scrollHeight
        } else if (event.kind === 'tool_started') activity.push(`正在运行 ${event.tool || '工具'} · ${event.status || 'host'}`)
        else if (event.kind === 'tool_result') activity.push(`工具完成 · ${(event.text || '').slice(0, 100)}`)
        else if (event.kind === 'reasoning_delta') activity.push(`推理 · ${(event.text || '').slice(0, 100)}`)
        else if (event.kind === 'done') {
          finished = true
          activity = []
          if (event.status !== 'complete' && event.status !== 'waiting') notice(event.error || event.status || '运行未完成', true)
        }
        renderActivity()
      }
      if (done) break
    }
    if (!finished) throw new Error('连接中断，运行结果未返回；请检查会话状态')
  } catch (error) { activity = []; showError(error) }
  finally {
    busy = false
    try {
      current = await api<Session>(`/api/sessions/${id}`)
      await refreshSessions()
    } catch (error) { showError(error) }
    render()
    prompt.focus()
  }
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function upload(files: FileList) {
  if (!current || busy || uploading) return
  uploading = true
  render()
  try {
    for (const file of Array.from(files)) {
      try {
        if (file.size > 10_000_000) throw new Error(`${file.name} 超过 10 MB`)
        const mediaType = file.type || (file.name.endsWith('.json') ? 'application/json' : 'text/plain')
        await api(`/api/sessions/${current.id}/upload`, { name: file.name, mediaType, data: await readBase64(file) })
      } catch (error) { showError(error) }
    }
    current = await api<Session>(`/api/sessions/${current.id}`)
  } catch (error) { showError(error) }
  finally {
    uploading = false
    render()
  }
}

function setupCommands() {
  const list = $('#command-list')
  for (const [name, description] of commandItems) {
    const button = document.createElement('button')
    button.type = 'button'
    button.innerHTML = `<code>${name.trim()}</code><span>${description}</span>`
    button.onclick = () => {
      $<HTMLDialogElement>('#command-dialog').close()
      if (name.endsWith(' ')) { prompt.value = name; prompt.focus() }
      else void command(name)
    }
    list.append(button)
  }
}

$<HTMLFormElement>('#composer').onsubmit = event => {
  event.preventDefault()
  const text = prompt.value.trim()
  if (!text || !current || busy || uploading) return
  if (current.review && !text.startsWith('/')) { notice('请先处理待批准的操作', true); return }
  prompt.value = ''
  if (text.startsWith('/')) { void command(text); return }
  current.messages.push({ role: 'user', content: text })
  render()
  void stream('turn', { text })
}
prompt.onkeydown = event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    $<HTMLFormElement>('#composer').requestSubmit()
  }
}
$('#review').addEventListener('click', event => {
  const decision = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-decision]')?.dataset.decision
  if (decision && current?.review) void stream('review', { id: current.review.id, decision })
})
$('#new-chat').onclick = () => void newSession()
$('#menu').onclick = () => $('#sidebar').classList.toggle('open')
$('#sidebar-close').onclick = () => $('#sidebar').classList.remove('open')
$('#inspector-toggle').onclick = () => $('#inspector').classList.toggle('closed')
$('#inspector-close').onclick = () => $('#inspector').classList.add('closed')
$('#search').addEventListener('input', () => void refreshSessions())
$('#fork').onclick = () => void command('/fork')
$('#share').onclick = () => void command('/share')
$('#help').onclick = () => $<HTMLDialogElement>('#command-dialog').showModal()
$('#commands').onclick = () => $<HTMLDialogElement>('#command-dialog').showModal()
$('#attach').onclick = () => $<HTMLInputElement>('#file-input').click()
$<HTMLInputElement>('#file-input').onchange = event => {
  const input = event.target as HTMLInputElement
  if (input.files) void upload(input.files)
  input.value = ''
}
$('#stop').onclick = () => { if (current) void api(`/api/sessions/${current.id}/cancel`, {}).catch(showError) }
$('#recover-button').onclick = () => void stream('recover', {})
$<HTMLSelectElement>('#model').onchange = event => {
  const value = (event.target as HTMLSelectElement).value
  if (value !== 'current') void command(`/model ${value}`)
}
$<HTMLInputElement>('#plan-mode').onchange = event => void command(`/plan ${(event.target as HTMLInputElement).checked ? 'on' : 'off'}`)
$<HTMLInputElement>('#auto-approve').onchange = event => void command(`/approve ${(event.target as HTMLInputElement).checked ? 'on' : 'off'}`)
$<HTMLInputElement>('#reasoning').onchange = event => void command(`/reasoning ${(event.target as HTMLInputElement).checked ? 'on' : 'off'}`)
$<HTMLSelectElement>('#permission-mode').onchange = event => void command(`/mode ${(event.target as HTMLSelectElement).value}`)
document.querySelectorAll<HTMLButtonElement>('[data-command]').forEach(button => button.onclick = () => void command(button.dataset.command!))
document.querySelectorAll<HTMLButtonElement>('[data-close]').forEach(button => button.onclick = () => button.closest('dialog')?.close())
document.querySelectorAll<HTMLButtonElement>('[data-suggestion]').forEach(button => button.onclick = () => { prompt.value = button.dataset.suggestion || ''; prompt.focus() })
document.addEventListener('keydown', event => {
  if (event.ctrlKey && event.key.toLowerCase() === 'n') { event.preventDefault(); void newSession() }
  if (event.ctrlKey && event.key.toLowerCase() === 'k') { event.preventDefault(); $<HTMLDialogElement>('#command-dialog').showModal() }
})

setupCommands()
void (async () => {
  try {
    const list = await api<Summary[]>('/api/sessions')
    if (list.length) await openSession(list[0].id)
    else await newSession()
  } catch (error) { showError(error) }
})()
