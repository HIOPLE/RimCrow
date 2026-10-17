import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(new URL('../package.json', import.meta.url))
const { computed, ref, reactive, watch, effectScope, nextTick } = require('vue')
const source = readFileSync(new URL('../src/features/settings/panel/SettingsNetworkTab.vue', import.meta.url), 'utf8')
// 直接运行组件的状态监听器，验证交互时是否真正发起获取请求。
const watcher = source.slice(source.indexOf('const hostsState ='), source.indexOf('const secretStatus ='))
const sourceUrl = 'https://example.invalid/hosts'
const props = reactive({ formData: { network: { github_hosts_enabled: false, hosts_sources: [{ url: sourceUrl, enabled: true }] } } })
const timers = new Map()
let timerId = 0
let fetchCount = 0
let cancelCount = 0
let status = {
  status: 'idle', source_url: sourceUrl, dynamic_hosts: { 'saved.example': '1.1.1.1' }, effective_hosts: { 'saved.example': '1.1.1.1' }, can_refresh: false,
  preview: { status: 'idle', source_url: '', hosts: {}, source_errors: [] },
}
const api = {
  network_refresh_github_hosts: async (network) => {
    assert.deepEqual(JSON.parse(JSON.stringify(network.hosts_sources)), JSON.parse(JSON.stringify(props.formData.network.hosts_sources)))
    if (!network.github_hosts_enabled) {
      cancelCount += 1
      status = { ...status, status: 'idle', preview: { ...status.preview, status: 'idle', source_url: '', hosts: {} } }
      return { status: 'success', data: status }
    }
    fetchCount += 1
    const previewSource = [...new Set(network.hosts_sources.filter(row => row.enabled).map(row => row.url.trim()).filter(Boolean))].join('\n')
    status = { ...status, status: 'ready', error: '', preview: { ...status.preview, status: 'loading', source_url: previewSource, error: '', hosts: {} } }
    return { status: 'success', data: status }
  },
  network_github_hosts_status: async () => ({ status: 'success', data: status }),
}
const context = vm.createContext({ computed, ref, watch, props, window: { pywebview: { api } },
  setTimeout: fn => { timers.set(++timerId, fn); return timerId },
  clearTimeout: id => timers.delete(id),
})
let scope = effectScope()
const mount = () => scope.run(() => vm.runInContext('(() => { ' + watcher + '\nglobalThis.controls = { hostsState, hostsRetry } })()', context))
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick() }
const tick = async () => { const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); await settle() }

try {
  mount()
  await settle()
  props.formData.network.github_hosts_enabled = true
  await settle()
  assert.equal(fetchCount, 1, '开启开关应立即按未保存的表单获取')
  assert.equal(context.controls.hostsState.value.status, 'loading')
  assert.equal(timers.size, 1)
  status = { ...status, status: 'ready', is_applied: true, preview: { ...status.preview, status: 'ready', source_url: sourceUrl, hosts: { 'preview.example': '2.2.2.2' } } }
  await tick()
  assert.equal(context.controls.hostsState.value.status, 'ready')
  assert.equal(context.controls.hostsState.value.is_applied, true)
  assert.deepEqual(context.controls.hostsState.value.hosts, { 'preview.example': '2.2.2.2' })
  assert.deepEqual(context.controls.hostsState.value.dynamic_hosts, { 'saved.example': '1.1.1.1' })
  assert.equal(timers.size, 0, '获取完成后不应继续每秒轮询状态')
  scope.stop()

  status = { ...status, status: 'loading', can_refresh: true, preview: { ...status.preview, status: 'loading', source_url: sourceUrl } }
  scope = effectScope()
  mount()
  await settle()
  assert.equal(fetchCount, 1, '打开页面不得额外触发下载')
  assert.equal(context.controls.hostsState.value.status, 'loading')
  status = { ...status, status: 'error', error: 'timeout', preview: { ...status.preview, status: 'error', source_url: sourceUrl, error: 'timeout' } }
  await tick()
  assert.equal(context.controls.hostsState.value.error, 'timeout')
  context.controls.hostsRetry.value += 1
  await settle()
  assert.equal(fetchCount, 2, '手动重试应发起一次下载')
  status = { ...status, status: 'ready', is_applied: true, preview: { ...status.preview, status: 'ready', source_url: sourceUrl } }
  await tick()
  assert.equal(context.controls.hostsState.value.status, 'ready')
  assert.equal(timers.size, 0)

  props.formData.network.hosts_sources = [{ url: 'https://other.example/hosts', enabled: true }, { url: 'https://second.example/hosts.json', enabled: false }]
  await settle()
  assert.equal(fetchCount, 3, '来源变更应获取新来源')
  assert.equal(context.controls.hostsState.value.status, 'loading')
  props.formData.network.hosts_sources[1].url = 'https://edited.example/hosts'
  await settle()
  assert.equal(fetchCount, 3, '编辑停用来源不得触发获取')
  props.formData.network.hosts_sources[1].enabled = true
  await settle()
  assert.equal(fetchCount, 4, '启用单个来源应获取更新后的列表')
  props.formData.network.hosts_sources.splice(0, 1)
  await settle()
  assert.equal(fetchCount, 5, '删除启用来源应重新获取并移除旧来源')
  props.formData.network.github_hosts_enabled = false
  await settle()
  assert.equal(fetchCount, 5, '关闭开关不得触发下载')
  assert.equal(cancelCount, 1, '关闭开关应取消旧结果提交')
  assert.equal(context.controls.hostsState.value.status, 'idle')
  assert.deepEqual(Object.keys(props.formData.network).sort(), ['github_hosts_enabled', 'hosts_sources'])
  scope.stop()
  assert.equal(timers.size, 0)
} finally {
  scope.stop()
}
console.log('动态 Hosts 界面状态测试通过。')
