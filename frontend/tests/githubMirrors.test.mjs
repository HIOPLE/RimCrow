import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(new URL('../package.json', import.meta.url))
const { ref, reactive, computed, watch } = require('vue')
const source = readFileSync(new URL('../src/features/settings/panel/SettingsNetworkTab.vue', import.meta.url), 'utf8')
const controls = source.slice(source.indexOf('const githubMirrorCatalog ='), source.indexOf('const hostsState ='))
const props = reactive({ formData: { github_mirrors: [], github_mirror_id: 'direct', github_dynamic_mirrors_enabled: true, _github_mirrors_path: 'data/github_mirrors.json' } })
let openedPath = ''
let errors = 0
const context = vm.createContext({ ref, reactive, computed, watch, onMounted: () => {}, props, URL, t: (_key, fallback) => fallback,
  toast: { error: () => { errors += 1 } },
  window: { pywebview: { api: {
    github_probe_mirror: async ({ url_template }) => ({ status: 'success', data: {
      support: {
        web: { status: 'supported' }, raw: { status: 'supported' }, archive: { status: 'supported' },
        release_asset: { status: 'unknown' },
      },
      probed_url_template: url_template,
      probed_at: 1,
    } }),
    path_open_file: async path => { openedPath = path; return { status: 'success' } },
  } } },
})
vm.runInContext(controls + '\nglobalThis.controls = { githubMirrorOptions, mirrorDraft, mirrorError, handleEditMirror, handleApplyMirror, handleRemoveMirror, handleOpenMirrorsFile }', context)
const ui = context.controls
props.formData.github_mirrors = [
  { id: 'preset', label: 'GH-Proxy', support: { web: { status: 'supported' }, raw: { status: 'supported' }, archive: { status: 'supported' }, release_asset: { status: 'supported' } } },
  { id: 'probed', label: 'Custom', support: {
    web: { status: 'supported' }, raw: { status: 'supported' }, archive: { status: 'unsupported' }, release_asset: { status: 'unknown' },
  } },
]
assert.equal(ui.githubMirrorOptions.value[0].label, 'GitHub 直连')
assert.equal(ui.githubMirrorOptions.value[1].label, '自动选择镜像')
props.formData.github_mirrors = []
ui.handleEditMirror()
Object.assign(ui.mirrorDraft.value, { id: 'custom', label: 'Custom', url_template: 'https://example.org/{url}' })
assert.equal(props.formData.github_mirrors.length, 0, '弹窗草稿不能提前修改设置')
await ui.handleApplyMirror()
assert.equal(props.formData.github_mirror_id, 'custom:custom')
assert.equal(props.formData.github_mirrors.length, 1)
const original = props.formData.github_mirrors[0]
ui.handleEditMirror(original)
ui.mirrorDraft.value.label = 'Edited'
assert.equal(original.label, 'Custom')
ui.mirrorDraft.value.id = 'renamed'
await ui.handleApplyMirror()
assert.equal(props.formData.github_mirror_id, 'custom:renamed')
assert.equal(props.formData.github_mirrors[0].label, 'Edited')

for (const override of [
  { id: 'renamed' }, { id: 'direct' }, { url_template: 'http://example.org/{url}' },
  { url_template: 'https://example.org/{url}/{url}' },
]) {
  ui.handleEditMirror()
  Object.assign(ui.mirrorDraft.value, { id: 'new', label: 'New', url_template: 'https://example.org/{url}', ...override })
  await ui.handleApplyMirror()
  assert.ok(ui.mirrorError.value, '无效定义应给出错误')
  assert.equal(props.formData.github_mirrors.length, 1)
}
await ui.handleOpenMirrorsFile()
assert.equal(openedPath, 'data/github_mirrors.json')
context.window.pywebview.api.path_open_file = async () => ({ status: 'error' })
await ui.handleOpenMirrorsFile()
assert.equal(errors, 1)
ui.handleRemoveMirror()
assert.equal(props.formData.github_mirrors.length, 0)
assert.equal(props.formData.github_mirror_id, 'direct')
console.log('GitHub 镜像编辑交互测试通过。')
