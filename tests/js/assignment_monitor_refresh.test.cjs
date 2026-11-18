// A failed refresh must never erase the saved list or claim fresh data.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');

const source = readFileSync('app/templates/components/lms_pending_assignments_widget.html', 'utf8');
const start = source.indexOf('    function loadAssignments(');
const end = source.indexOf('\n    // Render assignments to DOM', start);
assert(start >= 0 && end > start);
const loadFunction = source.slice(start, end);

const saved = {
    success: true, pending: [{title: 'CCP'}, {title: 'Assignment 3'}, {title: 'Assignment 3'}],
    missed: [{title: 'Assignment 01'}, {title: 'Lab Task 1'}], total_pending: 3, total_missed: 2,
    refreshed_at: '2026-11-17T15:07:00Z'
};

function harness(initial = saved) {
    const state = {data: initial, isLoading: false, isRefreshing: false,
        lastRefreshTime: initial ? new Date(initial.refreshed_at) : null};
    const calls = {render: [], success: 0, errors: [], banner: [], reset: 0, emptyError: []};
    let response;
    const context = vm.createContext({
        widgetState: state, console: {error() {}}, Date,
        setTimeout() { return 1; }, clearTimeout() {},
        fetch: () => response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
        showLoading() {}, startProgressTicker() {}, updateAgeIndicator() {},
        renderAssignments(data) { calls.render.push(data); },
        completeProgressSuccess() { calls.success++; },
        completeProgressError(message) { calls.errors.push(message); },
        showError(message) { calls.emptyError.push(message); },
        resetHeaderRefreshButton() { calls.reset++; },
        window: {hideLMSBanner() {}, showFlashMessage() {},
            showLMSBanner(...args) { calls.banner.push(args); }}
    });
    vm.runInContext(loadFunction, context);
    return {state, calls, async load(data, force = true, status = 200) {
        response = data instanceof Error ? data : {ok: status === 200, status, json: async () => data};
        context.loadAssignments(force);
        await new Promise(resolve => setImmediate(resolve));
    }};
}

for (const failure of [
    {success: true, pending: [], missed: [], total_pending: 0, total_missed: 0, error: 'Scrape failed'},
    {success: false, error: 'Scrape failed'},
    {success: true, pending: [], missed: [], warning: 'Partial scrape'},
    {success: true, pending: [], missed: [], failed_courses: ['Networks']},
    {success: true, pending: [], missed: [], refresh_failed: true},
    new Error('Network error')
]) {
    test(`failed refresh preserves saved counts and age: ${failure.error || failure.warning || failure.message || JSON.stringify(failure)}`, async () => {
        const h = harness();
        const originalTime = h.state.lastRefreshTime;
        await h.load(failure);
        assert.equal(h.state.data, saved);
        assert.equal(h.state.lastRefreshTime, originalTime);
        assert.equal(h.state.data.total_pending, 3);
        assert.equal(h.state.data.total_missed, 2);
        assert.equal(h.calls.render.length, 0);
        assert.equal(h.calls.success, 0);
        assert.equal(h.calls.errors.length, 1);
        assert.equal(h.calls.reset, 1);
    });
}

test('manual refresh distinguishes the daily limit from the cooldown', async () => {
    const h = harness();
    await h.load({error_code: 'REFRESH_COOLDOWN'}, true, 429);
    await h.load({error_code: 'REFRESH_DAILY_LIMIT'}, true, 429);
    assert.deepEqual(h.calls.errors, ['Cooldown active', 'Daily limit reached']);
    assert.equal(h.state.data, saved);
});

test('failed initial request can show the saved snapshot without reporting refresh success', async () => {
    const h = harness(null);
    await h.load({...saved, success: false, using_saved_snapshot: true, refresh_failed: true,
        user_message: 'Refresh failed. Showing your last saved assignments.'}, false);
    assert.equal(h.calls.render.length, 1);
    assert.equal(h.state.data.total_pending, 3);
    assert.equal(h.state.data.total_missed, 2);
    assert.equal(h.state.lastRefreshTime.toISOString(), saved.refreshed_at.replace('Z', '.000Z'));
    assert.equal(h.calls.success, 0);
    assert.equal(h.calls.banner.length, 1);
    assert.equal(h.calls.emptyError.length, 0);
});

test('first request failure shows an error instead of all caught up or an endless loader', async () => {
    const h = harness(null);
    await h.load({success: false, error: 'Scrape failed', pending: [], missed: []});
    assert.equal(h.state.data, null);
    assert.equal(h.state.lastRefreshTime, null);
    assert.equal(h.calls.render.length, 0);
    assert.equal(h.calls.emptyError.length, 1);
    assert.equal(h.calls.success, 0);
});

test('a verified successful empty refresh can clear the saved assignments', async () => {
    const h = harness();
    await h.load({success: true, pending: [], missed: [], total_pending: 0, total_missed: 0,
        refreshed_at: '2026-11-18T15:07:00Z'});
    assert.equal(h.state.data.total_pending, 0);
    assert.equal(h.calls.render.length, 1);
    assert.equal(h.calls.success, 1);
    assert.equal(h.calls.errors.length, 0);
});
