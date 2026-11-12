// Behavioral checks for quiz selection, refresh, recovery, and download links.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync('app/static/js/lms_quizzes.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
    constructor(tag = 'div') {
        this.tagName = tag; this.children = []; this.style = {}; this.value = '';
        this.attributes = {}; this.events = {}; this.textContent = ''; this.disabled = false;
        this.classes = new Set();
        this.classList = {add: n => this.classes.add(n), remove: n => this.classes.delete(n),
            toggle: (n, enabled) => enabled ? this.classes.add(n) : this.classes.delete(n)};
    }
    appendChild(child) { this.children.push(child); return child; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(name, fn) { this.events[name] = fn; }
    querySelector(selector) { this.parts ??= {}; return this.parts[selector] ??= new Element(); }
}

function harness({tom = true} = {}) {
    const nodes = new Map();
    const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
    let onReady;
    const timers = new Map();
    let serial = 0;
    const calls = [];
    const state = {optionsError: false, error: false, stale: false, survey: false, surveyRequired: false, delayed: new Map(),
        quizzes: [{quiz_no: 1, title: '<img onerror=alert(1)>', marks: 0,
            quiz_file: 'https://lms.bahria.edu.pk/Student/Download.php?f=one%3D', solution_file: null}]};
    function TomSelect(select) {
        this.select = select; this.options = {};
        this.clear = () => { select.value = ''; };
        this.clearOptions = () => { this.options = {}; };
        this.addOptions = options => options.forEach(option => { this.options[option.value] = option; });
        this.setValue = value => { select.value = value; };
        this.refreshOptions = () => {};
        this.enable = () => { select.disabled = false; };
        this.disable = () => { select.disabled = true; };
        select.tomselect = this;
    }
    const context = vm.createContext({URL, DOMException, AbortController, console,
        document: {addEventListener: (name, fn) => { onReady = fn; }, getElementById: node,
            createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})},
        window: {location: {origin: 'https://quiz.test'}, TomSelect: tom ? TomSelect : null,
            addEventListener() {}, showSurveyModal: () => { state.survey = true; }},
        ...(tom ? {TomSelect} : {}),
        setTimeout: (fn, delay) => { timers.set(++serial, {fn, delay}); return serial; },
        clearTimeout: id => timers.delete(id),
        fetch: async url => {
            calls.push(url);
            const key = url.searchParams.get('semester');
            if (state.delayed.has(key)) return state.delayed.get(key);
            let data = {success: true};
            let ok = true;
            if (url.pathname.endsWith('/options')) {
                data = {success: !state.optionsError, semesters: [{code: 'Fall=', name: 'Fall-2026'}]};
                if (state.optionsError) data.error = 'Options unavailable';
            } else if (url.pathname.endsWith('/courses')) data.courses = [{code: `${key}-course`, name: 'Algorithms'}];
            else if (url.pathname.endsWith('/refresh-status')) data = {done: true, data: state.quizzes};
            else if (state.surveyRequired) data = {success: false, survey_required: true, error: 'CMS_SURVEY_REQUIRED'};
            else if (state.error) { ok = false; data = {error: 'LMS unavailable'}; }
            else data = {...data, quizzes: state.quizzes, is_stale: state.stale, task_id: state.stale ? 'task' : null};
            return {ok, json: async () => data};
        }
    });
    vm.runInContext(source, context);
    return {node, state, calls, timers, start: async () => { onReady(); await tick(); },
        change: async (id, value) => { node(id).value = value; node(id).events.change(); await tick(); }};
}

test('renders zero marks, safe titles, individual links, and missing values', async () => {
    const h = harness(); await h.start();
    assert.equal(h.node('semesterSelect').value, 'Fall=');
    assert.equal(h.node('courseSelect').value, '');
    assert.equal(h.node('courseRefreshActions').style.display, 'none');
    await h.change('courseSelect', 'Fall=-course');
    const cells = h.node('quizzesTableBody').children[0].children;
    assert.equal(cells[1].textContent, '<img onerror=alert(1)>');
    assert.equal(cells[1].children.length, 0);
    assert.equal(cells[3].textContent, '0');
    assert.equal(cells[4].textContent, '—');
    assert.equal(cells[2].children[0].href, '/lms/download/' + encodeURIComponent(h.state.quizzes[0].quiz_file));
    assert.equal(h.node('courseRefreshActions').style.display, 'block');
    assert(!source.includes('downloadAll'));
});

test('semester reset clears old course, rows, and refresh action', async () => {
    const h = harness(); await h.start(); await h.change('courseSelect', 'Fall=-course');
    await h.change('semesterSelect', 'Spring=');
    assert.equal(h.node('courseSelect').value, '');
    assert.equal(h.node('quizzesTableBody').children.length, 0);
    assert.equal(h.node('courseRefreshActions').style.display, 'none');
    assert(h.node('courseSelect').tomselect.options['Spring=-course']);
    assert(!h.node('courseSelect').tomselect.options['Fall=-course']);
});

test('late course responses never replace the new semester choices', async () => {
    const h = harness(); await h.start();
    let resolveOld;
    h.state.delayed.set('Old', new Promise(resolve => { resolveOld = resolve; }));
    await h.change('semesterSelect', 'Old');
    await h.change('semesterSelect', 'New');
    resolveOld({ok: true, json: async () => ({success: true, courses: [{code: 'wrong', name: 'Wrong'}]})});
    await tick();
    assert(h.node('courseSelect').tomselect.options['New-course']);
    assert(!h.node('courseSelect').tomselect.options.wrong);
});

test('late quiz responses never replace rows from a newly selected semester', async () => {
    const h = harness(); await h.start();
    let resolveOld;
    h.state.delayed.set('Fall=', new Promise(resolve => { resolveOld = resolve; }));
    await h.change('courseSelect', 'Fall=-course');
    await h.change('semesterSelect', 'Spring=');
    h.state.quizzes = [{quiz_no: 2, title: 'New semester quiz', marks: null}];
    await h.change('courseSelect', 'Spring=-course');
    resolveOld({ok: true, json: async () => ({success: true, quizzes: [{quiz_no: 1, title: 'Old quiz'}]})});
    await tick();
    assert.equal(h.node('quizzesTableBody').children[0].children[1].textContent, 'New semester quiz');
});

test('survey failures open the existing modal and retain a working retry action', async () => {
    const h = harness(); await h.start(); h.state.surveyRequired = true;
    await h.change('courseSelect', 'Fall=-course');
    assert(h.state.survey);
    assert.match(h.node('errorMessage').textContent, /mandatory CMS surveys/);
    h.state.surveyRequired = false;
    h.node('retryButton').events.click(); await tick();
    assert.equal(h.node('quizzesContainer').style.display, 'block');
});

test('errors and empty results keep refresh available; Retry forces a fetch', async () => {
    const h = harness(); await h.start(); h.state.error = true;
    await h.change('courseSelect', 'Fall=-course');
    assert.equal(h.node('errorState').style.display, 'flex');
    assert.equal(h.node('courseRefreshActions').style.display, 'block');
    h.state.error = false; h.state.quizzes = [];
    h.node('retryButton').events.click(); await tick();
    assert(h.calls.at(-1).pathname.endsWith('/fetch'));
    assert.equal(h.calls.at(-1).searchParams.get('refresh'), 'true');
    assert.equal(h.node('emptyState').querySelector('.empty-state-title').textContent, 'No Quizzes Found');
    assert.equal(h.node('courseRefreshActions').style.display, 'block');
});

test('stale cache refresh updates rows and selection change cancels polling', async () => {
    const h = harness(); await h.start(); h.state.stale = true;
    await h.change('courseSelect', 'Fall=-course');
    assert.match(h.node('quizStatus').textContent, /Showing saved/);
    const poll = [...h.timers.values()].find(timer => timer.delay === 3000);
    h.state.quizzes = []; await poll.fn(); await tick();
    assert.equal(h.node('quizzesTableBody').children.length, 0);
    assert.equal(h.node('quizStatus').textContent, 'Quizzes updated.');
    await h.change('courseSelect', 'Fall=-course');
    await h.change('semesterSelect', 'Spring=');
    assert(![...h.timers.values()].some(timer => timer.delay === 3000));
    assert.equal(h.node('quizStatus').style.display, 'none');
});

test('initial failure can retry; native select fallback works without Tom Select', async () => {
    const h = harness({tom: false}); h.state.optionsError = true; await h.start();
    assert.equal(h.node('connectionErrorState').style.display, 'flex');
    h.state.optionsError = false; h.node('connectionRetryButton').events.click(); await tick();
    assert.equal(h.node('mainContent').style.display, 'block');
    assert.equal(h.node('courseSelect').disabled, false);
    await h.change('courseSelect', 'Fall=-course');
    assert.equal(h.node('quizzesTableBody').children.length, 1);
});
