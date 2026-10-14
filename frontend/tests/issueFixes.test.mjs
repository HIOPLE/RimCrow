import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'

// 执行组件中的实际函数，省去挂载整套应用和 pywebview。
function componentFunctions(path, names, context) {
  const { descriptor } = parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
  const { scriptSetupAst } = compileScript(descriptor, { id: path })
  const declarations = scriptSetupAst.filter(node => node.type === 'VariableDeclaration'
    && node.declarations.some(declaration => names.includes(declaration.id.name)))
  assert.equal(declarations.length, names.length)
  const code = declarations.map(node => descriptor.scriptSetup.content.slice(node.start, node.end)).join('\n')
  return runInNewContext(`${code}\n;({ ${names.join(', ')} })`, context)
}

const calls = []
const context = {
  newRepoUrl: { value: '' }, newRepoBranch: { value: '' }, isParsing: { value: false },
  previewReadme: { value: {} }, selectedCatalogItem: { value: null },
  workspaceStore: { github: {}, fetchGithubRepos() {} },
  t: (_key, fallback) => fallback, checkResult: result => result.status === 'success',
  toast: { success() {} },
  loadRepoReadme: async (url, branch) => calls.push(['readme', url, branch]),
  window: { pywebview: { api: {
    github_fetch_info: async (url, branch) => {
      calls.push(['info', url, branch])
      return { status: 'success', data: { owner: 'author', repo: 'mod', default_branch: 'main', latest_source_branch: branch || 'main' } }
    },
    github_subscribe: async payload => {
      calls.push(['subscribe', payload])
      return { status: 'success' }
    },
  } } },
}
const github = componentFunctions('../src/features/workspace/views/GithubCommand.vue', ['parseNewRepo', 'confirmSubscribe'], context)
for (const [url, explicitBranch, expectedBranch] of [
  ['https://github.com/author/mod/tree/rimworld-1.6', '', 'rimworld-1.6'],
  ['https://github.com/author/mod/tree/feature/rimworld?tab=readme', '', 'feature/rimworld'],
  ['https://github.com/author/mod/tree/old', 'rimworld-1.7', 'rimworld-1.7'],
  ['https://github.com/author/mod', '', 'main'],
]) {
  calls.length = 0
  context.newRepoUrl.value = url
  context.newRepoBranch.value = explicitBranch
  await github.parseNewRepo()
  assert.equal(calls[0][1], 'https://github.com/author/mod')
  assert.equal(calls[0][2] || 'main', expectedBranch)
  assert.equal(calls[1][2], expectedBranch)
  await github.confirmSubscribe('source')
  assert.equal(calls[2][1].url, 'https://github.com/author/mod')
  assert.equal(calls[2][1].default_branch, expectedBranch)
}

const dragContext = {
  DRAG_THRESHOLD: 4,
  dragState: { active: true, moved: false, startX: 0, startY: 0, offsetX: 0, offsetY: 0 },
  dragOffset: { value: { x: 0, y: 0 } }, isPanelOpen: { value: false },
  rootRef: { value: {
    getBoundingClientRect: () => ({ left: 780, top: 76, width: 200, height: 32 }),
    parentElement: { getBoundingClientRect: () => ({ left: 4, right: 996, top: 64, bottom: 796 }) },
  } },
}
const guide = componentFunctions('../src/features/guide/GuideCenter.vue', ['handlePointerMove'], dragContext)
guide.handlePointerMove({ clientX: -10000, clientY: -10000 })
assert.equal(dragContext.dragOffset.value.x, -776)
assert.equal(dragContext.dragOffset.value.y, -12)
dragContext.dragOffset.value = { x: 0, y: 0 }
guide.handlePointerMove({ clientX: 10000, clientY: 10000 })
assert.equal(dragContext.dragOffset.value.x, 16)
assert.equal(dragContext.dragOffset.value.y, 688)
console.log('Git branch selection and guide drag bounds passed')
