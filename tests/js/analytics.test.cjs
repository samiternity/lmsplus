const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = readFileSync('app/static/js/analytics.js', 'utf8');
const config = {
    measurement_id: 'G-TEST123', user_id: 'pseudonymous-account',
    page_location: 'https://lmsplus.xyz/dashboard', page_title: 'LMS+', page_referrer: ''
};

function harness({missing = false, malformed = false, blocked = false} = {}) {
    const scripts = [];
    const context = vm.createContext({
        window: {},
        document: {
            getElementById: () => missing ? null : {textContent: malformed ? 'bad json' : JSON.stringify(config)},
            createElement: () => ({}),
            head: {appendChild(script) {
                if (blocked) throw new Error('Tracking blocked');
                scripts.push(script);
            }}
        }
    });
    return {context, scripts, run: () => vm.runInContext(source, context),
        calls: () => Array.from(context.window.dataLayer || [], call => Array.from(call))};
}

test('configures identity and safe metadata before exactly one manual page view', () => {
    const h = harness(); h.run(); h.run();
    const calls = h.calls();
    assert.deepEqual(calls.map(c => c[0]), ['set', 'js', 'config', 'event']);
    const settings = calls[2][2];
    assert.equal(settings.user_id, config.user_id);
    assert.equal(settings.send_page_view, false);
    assert.equal(settings.allow_google_signals, false);
    assert.equal(settings.allow_ad_personalization_signals, false);
    for (const metadata of [calls[0][1], settings, calls[3][2]]) {
        assert.equal(metadata.page_location, config.page_location);
        assert.equal(metadata.page_title, 'LMS+');
        assert.equal(metadata.page_referrer, '');
    }
    assert.equal(calls[3][1], 'page_view');
    assert.equal(calls[3][2].send_to, config.measurement_id);
    assert.equal(h.scripts.length, 1);
    assert.equal(h.scripts[0].async, true);
    assert.equal(h.scripts[0].referrerPolicy, 'no-referrer');
    assert.equal(h.scripts[0].src, 'https://www.googletagmanager.com/gtag/js?id=G-TEST123');
});

test('missing or malformed settings do not load a tracker or break app scripts', () => {
    for (const options of [{missing: true}, {malformed: true}]) {
        const h = harness(options);
        assert.doesNotThrow(h.run);
        assert.equal(h.calls().length, 0);
        assert.equal(h.scripts.length, 0);
        vm.runInContext('window.appWorks = true', h.context);
        assert.equal(h.context.window.appWorks, true);
    }
});

test('blocked external script leaves app scripts working', () => {
    const h = harness({blocked: true});
    assert.doesNotThrow(h.run);
    vm.runInContext('window.appWorks = true', h.context);
    assert.equal(h.context.window.appWorks, true);
    assert.equal(h.scripts.length, 0);
});
