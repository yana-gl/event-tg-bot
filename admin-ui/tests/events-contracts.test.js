import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { SourceTextModule, SyntheticModule } from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createPinia, defineStore } from 'pinia'
import * as vue from 'vue'

const storeSource = await readFile(new URL('../src/stores/events.js', import.meta.url), 'utf8')
const detailSource = await readFile(new URL('../src/views/EventDetail.vue', import.meta.url), 'utf8')
const { descriptor } = parse(detailSource, { filename: 'EventDetail.vue' })
const detailScript = compileScript(descriptor, { id: 'event-detail-test' }).content

async function loadModule(source, dependencies) {
  const module = new SourceTextModule(source)
  await module.link((specifier) => {
    assert.ok(Object.hasOwn(dependencies, specifier), `Unexpected import: ${specifier}`)
    const exports = dependencies[specifier]
    return new SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value)
    })
  })
  await module.evaluate()
  return module.namespace
}

async function createStore() {
  const requests = []
  const { useEventsStore } = await loadModule(storeSource, {
    pinia: { defineStore },
    '../api/client': {
      apiGet(path) {
        const request = { path, ...Promise.withResolvers() }
        requests.push(request)
        return request.promise
      },
      apiPost() { assert.fail('Unexpected API write') }
    }
  })
  return { store: useEventsStore(createPinia()), useEventsStore, requests }
}

test('list requests preserve the selected filter and accept an empty response', async () => {
  const { store, requests } = await createStore()
  const initial = store.fetchList()
  assert.equal(requests[0].path, '/api/events?status=exceptions')
  assert.equal(store.loading, true)
  requests[0].resolve({ items: [{ id: 1 }] })
  await initial
  assert.deepEqual(store.items, [{ id: 1 }])
  assert.equal(store.loading, false)

  const filtered = store.fetchList('published')
  requests[1].resolve({ items: [{ id: 2 }] })
  await filtered
  const refreshed = store.fetchList()
  assert.equal(requests[2].path, '/api/events?status=published')
  requests[2].resolve({})
  await refreshed
  assert.deepEqual(store.items, [])
  assert.equal(store.loading, false)
})

for (const staleFails of [false, true]) {
  for (const currentFails of [false, true]) {
    for (const currentFirst of [false, true]) {
      test(`draft → published: stale ${staleFails ? 'error' : 'success'}, current ${currentFails ? 'error' : 'success'}, ${currentFirst ? 'current' : 'stale'} finishes first`, async () => {
        const { store, requests } = await createStore()
        const originalItems = [{ id: 1 }]
        const publishedItems = [{ id: 3, status: 'published' }]
        const failures = [new Error('draft failed'), new Error('published failed')]
        const errors = []
        store.items = originalItems
        const pending = [
          store.fetchList('draft').catch((error) => errors.push(error)),
          store.fetchList('published').catch((error) => errors.push(error))
        ]
        assert.deepEqual(requests.map(({ path }) => path), [
          '/api/events?status=draft', '/api/events?status=published'
        ])
        assert.equal(store.status, 'published')
        assert.equal(store.loading, true)

        let currentFinished = false
        for (const index of currentFirst ? [1, 0] : [0, 1]) {
          if ([staleFails, currentFails][index]) {
            requests[index].reject(failures[index])
          } else {
            requests[index].resolve({ items: index === 0 ? [{ id: 2, status: 'draft' }] : publishedItems })
          }
          await pending[index]
          if (index === 1) currentFinished = true
          assert.equal(store.status, 'published')
          assert.equal(store.loading, !currentFinished)
          assert.deepEqual(store.items, currentFinished && !currentFails ? publishedItems : originalItems)
          assert.deepEqual(errors, currentFinished && currentFails ? [failures[1]] : [])
        }
      })
    }
  }
}

test('returning to the same filter still ignores the earlier request', async () => {
  const { store, requests } = await createStore()
  const firstDraft = store.fetchList('draft')
  const published = store.fetchList('published')
  const latestDraft = store.fetchList('draft')
  requests[2].resolve({ items: [{ id: 3 }] })
  await latestDraft
  requests[0].resolve({ items: [{ id: 1 }] })
  requests[1].resolve({ items: [{ id: 2 }] })
  await Promise.all([firstDraft, published])
  assert.deepEqual(store.items, [{ id: 3 }])
  assert.equal(store.status, 'draft')
  assert.equal(store.loading, false)
})

test('list request tracking is independent for each store instance', async () => {
  const { store, useEventsStore, requests } = await createStore()
  const otherStore = useEventsStore(createPinia())
  const first = store.fetchList('draft')
  const second = otherStore.fetchList('published')
  requests[0].resolve({ items: [{ id: 1 }] })
  await first
  assert.deepEqual(store.items, [{ id: 1 }])
  assert.equal(store.loading, false)
  assert.equal(otherStore.loading, true)
  requests[1].resolve({ items: [{ id: 2 }] })
  await second
  assert.deepEqual(otherStore.items, [{ id: 2 }])
  assert.equal(otherStore.loading, false)
})

async function createDetail(t, category) {
  const store = vue.reactive({
    detail: {
      id: 42, title: 'Event', category, date: '2026-11-07', status: 'published',
      event_state: 'scheduled', conflict_post_id: 7, conflict_at: '2026-11-06T12:00:00Z'
    },
    update: t.mock.fn(async () => {}),
    fetchDetail: t.mock.fn(async () => {})
  })
  const notify = t.mock.fn()
  const { default: component } = await loadModule(detailScript, {
    vue: { ...vue, onMounted() {} },
    'vue-router': { useRoute: () => ({ params: { id: '42' } }), useRouter: () => ({}) },
    '../stores/events': { useEventsStore: () => store, EVENT_STATES: {} },
    'vuestic-ui': { useToast: () => ({ init: notify }), useModal: () => ({}) },
    '../api/client': { apiGet() { assert.fail('Unexpected API read') } }
  })
  const scope = vue.effectScope()
  t.after(() => scope.stop())
  const view = scope.run(() => component.setup({}, { expose() {} }))
  return { store, view, notify }
}

for (const action of ['save', 'resolveConflict']) {
  for (const scenario of [
    { name: 'loaded null', initial: null, expected: null },
    { name: 'existing category', initial: 'music', expected: 'music' },
    { name: 'selected category', initial: null, edited: 'lecture', expected: 'lecture' },
    { name: 'cleared to empty string', initial: 'music', edited: '', expected: null },
    { name: 'cleared to null', initial: 'music', edited: null, expected: null },
    { name: 'cleared to undefined', initial: 'music', edited: undefined, expected: null }
  ]) {
    if (action === 'resolveConflict' && scenario.name !== 'cleared to empty string') continue
    test(`${action} sends a valid category: ${scenario.name}`, async (t) => {
      const { store, view, notify } = await createDetail(t, scenario.initial)
      if (Object.hasOwn(scenario, 'edited')) view.form.category = scenario.edited
      view.form.title = 'Edited event'
      await view[action](true)
      assert.equal(store.update.mock.callCount(), 1)
      const [id, fields] = store.update.mock.calls[0].arguments
      const payload = JSON.parse(JSON.stringify(fields))
      assert.equal(id, '42')
      assert.equal(payload.category, scenario.expected)
      assert.equal(payload.title, 'Edited event')
      assert.equal(payload.date, store.detail.date)
      assert.equal(payload.status, store.detail.status)
      assert.equal(payload.end_date, '')
      if (action === 'resolveConflict') {
        assert.deepEqual(payload.resolve_conflict, { post_id: 7, at: store.detail.conflict_at })
      } else {
        assert.equal(Object.hasOwn(payload, 'resolve_conflict'), false)
      }
      assert.deepEqual(store.fetchDetail.mock.calls[0].arguments, ['42'])
      assert.equal(notify.mock.calls[0].arguments[0].color, 'success')
    })
  }
}

test('closing a conflict without edits does not send form fields', async (t) => {
  const { store, view } = await createDetail(t, null)
  view.form.title = 'Unsaved title'
  await view.resolveConflict(false)
  assert.deepEqual(store.update.mock.calls[0].arguments, [
    '42', { resolve_conflict: { post_id: 7, at: store.detail.conflict_at } }
  ])
})
